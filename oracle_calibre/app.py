"""Orchestration : relie ingestion -> moteur -> registre, sans mélanger leurs rôles.

Ce module n'appelle jamais un LLM lui-même : il passe des clients aux
fonctions d'ingestion. Le moteur ne reçoit que des données figées.
"""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

from . import __version__
from .canonical import hash_obj
from .config import code_commit, dependency_lock_hash, load_config, load_prompts
from .engine import build_history, run_forecast
from .ingestion import ReplayClient, Session, apply_human_validation, extract_sources, formalize, prompts_hash, utc_now
from .ledger import Ledger, SnapshotStore
from .ledger.anchor import make_anchor
from .ledger.scoring import dashboard, score_records
from .ledger.shadow import shadow_comparison
from .ledger.tournament import tournament
from .schema import ValidationError, iso_duration_days, missing_resolution_fields

DEFINITION_VERSION = "1.0"


def build_engine_input(question: dict, draft: dict, lineage: list[dict], as_of: str, market: dict | None,
                       linked: list[dict], linked_constraints: list[dict], annotations: list[dict]) -> dict:
    return {
        "question": {
            "question_id": question["question_id"], "question_type": question["question_type"],
            "horizon": question["horizon"], "domain": draft.get("domain", "other"),
            "resolution_stability_score": question["resolution_stability_score"],
            "resolution_inter_model_agreement": question["resolution_inter_model_agreement"],
            "source_kind": draft.get("source_kind", "other"),
            "self_negating_risk": bool(draft.get("self_negating_risk", False)),
        },
        "as_of": as_of, "lineage": lineage, "market": market, "linked": linked,
        "linked_constraints": linked_constraints, "annotations": annotations,
    }


def linked_inputs(entries: list[dict], links: list[dict], before_seq: int | None) -> tuple[list[dict], list[dict]]:
    """Probabilités courantes des questions liées, lues dans le registre (préfixe)."""
    latest: dict[str, dict] = {}
    for e in entries:
        if before_seq is not None and e["seq"] >= before_seq:
            break
        if e["record_type"] == "forecast":
            latest[e["record"]["question"]["question_id"]] = e["record"]
    linked, internal = [], []
    ids = {l["question_id"] for l in links}
    for l in links:
        rec = latest.get(l["question_id"])
        if rec is None:
            raise ValidationError(f"question liée {l['question_id']} sans prévision au registre")
        linked.append({"question_id": l["question_id"], "relation": l["relation"], "hard": bool(l["hard"]),
                       "p": rec["final_output"]["p_reconciled"]})
    for qid in sorted(ids):
        for l2 in latest[qid]["question"].get("linked_questions") or []:
            if l2["question_id"] in ids:
                internal.append({"a_id": qid, "b_id": l2["question_id"], "relation": l2["relation"],
                                 "hard": bool(l2["hard"])})
    return linked, internal


def make_question(draft: dict, question_id: str, links: list[dict]) -> dict:
    subs = []
    for i, s in enumerate(draft.get("subquestions") or []):
        subs.append({"question_id": str(uuid.uuid5(uuid.UUID(question_id), f"sub-{i}")), **s})
    return {
        "question_id": question_id,
        "text": draft["text"],
        "question_type": draft["question_type"],
        "question_definition_version": draft.get("question_definition_version", DEFINITION_VERSION),
        "horizon": draft["horizon"],
        "resolution_rule": draft["resolution_rule"],
        "resolution_source": draft["resolution_source"],
        "resolution_deadline": draft["resolution_deadline"],
        "ambiguity_policy": draft["ambiguity_policy"],
        "resolvability_score": None,
        "resolution_stability_score": draft.get("resolution_stability_score", 0.0),
        "resolution_inter_model_agreement": draft.get("resolution_inter_model_agreement"),
        "resolution_human_validated": bool(draft.get("resolution_human_validated", False)),
        "rule_validation_protocol": draft.get("rule_validation_protocol") or {"n_reformulations": 0,
                                                                              "agreement_metric": "aucun"},
        "linked_questions": [{"question_id": l["question_id"], "relation": l["relation"], "hard": bool(l["hard"])}
                             for l in links],
        "decomposition": [s["question_id"] for s in subs],
        "decomposition_detail": subs,
        "definition": draft.get("definition", ""),
        "domain": draft.get("domain", "other"),
        "rule_edited_by_human": bool(draft.get("rule_edited_by_human", False)),
        "test_scenarios": draft.get("test_scenarios", []),
        "rule_verdicts": draft.get("rule_verdicts", []),
    }


class OracleApp:
    def __init__(self, data_dir: str | Path | None, primary=None, secondary=None, cfg: dict | None = None,
                 prompts: dict | None = None):
        self.dir = Path(data_dir) if data_dir else None
        self.cfg = cfg or load_config()
        self.prompts = prompts or load_prompts()
        self.ledger = Ledger(self.dir / "ledger.jsonl" if self.dir else None)
        self.store = SnapshotStore(self.dir / "snapshots" if self.dir else None)
        self.primary, self.secondary = primary, secondary
        self.drafts: dict[str, dict] = {}

    # ------------------------------------------------------------- brouillons
    def _session(self, primary=None, secondary=None) -> Session:
        p = primary or self.primary
        if p is None:
            raise RuntimeError("aucun client LLM configuré (définir ANTHROPIC_API_KEY)")
        return Session(p, self.prompts, self.cfg, secondary if primary else self.secondary)

    def start_draft(self, text: str, now: str | None = None) -> dict:
        now = now or utc_now()
        session = self._session()
        draft = formalize(session, text, now)
        did = str(uuid.uuid4())
        self.drafts[did] = {"draft": draft, "session": session, "sources": [], "lineage": [], "rejected": [],
                            "retrieval_hash": hash_obj([]), "retrieval": [], "edits": None, "validated": False}
        return {"draft_id": did, "draft": copy.deepcopy(draft)}

    def add_sources(self, draft_id: str, sources: list[dict]) -> dict:
        """Les sources sont extraites au moment du gel, APRÈS validation de la règle."""
        self.drafts[draft_id]["sources"] = list(sources)
        return {"n_sources": len(sources)}

    def validate(self, draft_id: str, edits: dict | None, validated: bool) -> dict:
        d = self.drafts[draft_id]
        apply_human_validation(d["session"], d["draft"], edits, validated)
        d["edits"], d["validated"] = edits, validated
        return copy.deepcopy(d["draft"])

    # ------------------------------------------------------------ prévision
    def commit(self, draft_id: str, market: dict | None = None, links: list[dict] | None = None,
               supersedes: str | None = None, annotations: list[dict] | None = None,
               shadow: dict | None = None) -> dict:
        d = self.drafts[draft_id]
        draft = d["draft"]
        missing = missing_resolution_fields({**draft, "question_definition_version": DEFINITION_VERSION})
        if missing:
            raise ValidationError("enregistrement refusé, champs de résolution manquants : " + ", ".join(missing))
        iso_duration_days(draft["horizon"])
        links = links or []
        as_of = draft["now"]
        if d["sources"]:
            ex = extract_sources(d["session"], draft, d["sources"], as_of)
            d.update(lineage=ex["lineage"], rejected=ex["rejected"], retrieval_hash=ex["retrieval_snapshot_hash"])
        prev = None
        if supersedes:
            entry = self.ledger.find_forecast(supersedes)
            if entry is None:
                raise ValidationError(f"prévision {supersedes} introuvable")
            prev = entry["record"]
        question_id = prev["question"]["question_id"] if prev else str(uuid.uuid4())
        question = make_question(draft, question_id, links)
        inputs = {"kind": "forecast_inputs", "input_text": draft["input_text"], "now": as_of,
                  "edits": d["edits"], "validated": d["validated"], "sources": d["sources"], "market": market,
                  "links": question["linked_questions"], "annotations": annotations or [], "shadow": shadow,
                  "draft_override": d.get("draft_override")}
        record = self._forecast(question, draft, d["lineage"], d["session"], d["retrieval_hash"], inputs,
                                supersedes, shadow)
        del self.drafts[draft_id]
        return record

    def _forecast(self, question: dict, draft: dict, lineage: list[dict], session: Session | None,
                  retrieval_hash: str, inputs: dict, supersedes: str | None, shadow: dict | None) -> dict:
        ts = utc_now()
        snap_hashes = []
        info = session.primary.info() if session else {"llm_provider": "none", "model_id": "none",
                                                       "model_version": "none", "temperature": None,
                                                       "seed_if_supported": None}
        for s in (session.snapshots if session else []):
            h = self.store.put({"kind": "llm_output", **s})
            snap_hashes.append(h)
        bundle_hash = self.store.put({"kind": "llm_bundle", "snapshot_hashes": snap_hashes})
        retrieval_hash = self.store.put({"kind": "retrieval", "documents": inputs.get("sources") or [],
                                         "content_hash": retrieval_hash})
        inputs_hash = self.store.put(inputs)
        for h, kind in [(x, "llm_output") for x in snap_hashes] + [(bundle_hash, "llm_bundle"),
                                                                  (retrieval_hash, "retrieval"),
                                                                  (inputs_hash, "forecast_inputs")]:
            self.ledger.append_snapshot(h, kind, ts)
        entries = self.ledger.entries
        seq_next = len(entries)
        history = build_history(entries, self.cfg, before_seq=seq_next)
        linked, internal = linked_inputs(entries, question["linked_questions"], seq_next)
        inp = build_engine_input(question, draft, lineage, inputs["now"], inputs.get("market"), linked, internal,
                                 inputs.get("annotations") or [])
        out = run_forecast(inp, history, self.cfg)
        question["resolvability_score"] = out["resolvability_score"]
        record = {
            "forecast_id": str(uuid.uuid4()),
            "supersedes_forecast_id": supersedes,
            "hash_previous": None,
            "timestamp_utc": ts,
            "question": question,
            "reproducibility": {
                **info,
                "temperature_note": "temperature non transmise : non supportée par les modèles Claude actuels",
                "prompt_hash": prompts_hash(self.prompts),
                "llm_snapshots_hash": bundle_hash,
                "retrieval_snapshot_hash": retrieval_hash,
                "inputs_snapshot_hash": inputs_hash,
                "engine_input_hash": hash_obj(inp),
                "code_commit": code_commit(),
                "code_version": __version__,
                "dependency_lock_hash": dependency_lock_hash(),
                "statistical_config_version": self.cfg["statistical_config_version"],
                "statistical_config_hash": self.cfg["_config_hash"],
                "engine_runtime": "python",
            },
            "information_lineage": lineage,
            "extraction_error_rates": out["extraction_error_rates"],
            "models_pipeline": out["models_pipeline"],
            "final_output": {**out["final_output"],
                             "self_negating_reason": draft.get("self_negating_reason", "")},
            "assumptions": [
                "Règle de résolution " + ("validée par l'utilisateur" if question["resolution_human_validated"]
                                          else "NON validée par un humain : l'accord entre reformulations mesure "
                                               "la stabilité, pas la validité"),
                "Sources limitées aux documents fournis, filtrées strictement par date",
                out["models_pipeline"]["dependency_assumptions"],
            ],
        }
        if shadow:
            record["shadow"] = shadow
        entry = self.ledger.append_forecast(record)
        return entry["record"]

    # -------------------------------------------------------------- rejeu
    def replay(self, forecast_id: str) -> dict:
        """Rejoue une prévision depuis ses snapshots, sans aucun appel LLM."""
        entry = self.ledger.find_forecast(forecast_id)
        if entry is None:
            raise KeyError(forecast_id)
        rec, seq = entry["record"], entry["seq"]
        repro = rec["reproducibility"]
        inputs = self.store.get(repro["inputs_snapshot_hash"])
        bundle = self.store.get(repro["llm_snapshots_hash"])
        snaps = [self.store.get(h) for h in bundle["snapshot_hashes"]]
        if inputs.get("draft_override"):
            draft = copy.deepcopy(inputs["draft_override"])
            lineage = []
        else:
            primary = ReplayClient([s for s in snaps if s["role"] == "primary"], repro)
            secondary = ReplayClient([s for s in snaps if s["role"] == "secondary"]) \
                if any(s["role"] == "secondary" for s in snaps) else None
            session = Session(primary, self.prompts, self.cfg, secondary)
            draft = formalize(session, inputs["input_text"], inputs["now"])
            apply_human_validation(session, draft, inputs["edits"], inputs["validated"])
            lineage = extract_sources(session, draft, inputs["sources"], inputs["now"])["lineage"] \
                if inputs["sources"] else []
        entries = self.ledger.entries
        # Le rejeu voit le registre tel qu'il était juste avant les snapshots de cette prévision.
        first_snap = seq
        while first_snap > 0 and entries[first_snap - 1]["record_type"] == "snapshot":
            first_snap -= 1
        history = build_history(entries, self.cfg, before_seq=first_snap)
        question = make_question(draft, rec["question"]["question_id"], inputs["links"])
        linked, internal = linked_inputs(entries, inputs["links"], first_snap)
        inp = build_engine_input(question, draft, lineage, inputs["now"], inputs.get("market"), linked, internal,
                                 inputs.get("annotations") or [])
        out = run_forecast(inp, history, self.cfg)
        fo = rec["final_output"]
        diffs = {
            "p_raw": abs(out["final_output"]["p_raw"] - fo["p_raw"]),
            "p_reconciled": abs(out["final_output"]["p_reconciled"] - fo["p_reconciled"]),
            "w_displayed": abs(out["final_output"]["w_displayed"] - fo["w_displayed"]),
        }
        return {"forecast_id": forecast_id, "max_abs_diff": max(diffs.values()), "diffs": diffs,
                "engine_input_hash_match": hash_obj(inp) == repro["engine_input_hash"],
                "replayed": {"p_raw": out["final_output"]["p_raw"], "p_reconciled": out["final_output"]["p_reconciled"]}}

    # ------------------------------------------------------- résolution
    def resolve(self, question_id: str, outcome, effective_source: str, resolvability_score_post: float,
                ambiguity_policy_applied: str | None = None, definition_version_applied: str | None = None) -> dict:
        latest = self.ledger.latest_forecast(question_id)
        if latest is None:
            raise ValidationError(f"question {question_id} inconnue")
        q = latest["question"]
        rec = {
            "resolution_id": str(uuid.uuid4()), "question_id": question_id, "hash_previous": None,
            "timestamp_utc": utc_now(), "outcome": outcome, "effective_source": effective_source,
            "definition_version_applied": definition_version_applied or q["question_definition_version"],
            "ambiguity_policy_applied": ambiguity_policy_applied or q["ambiguity_policy"],
            "resolvability_score_post": float(resolvability_score_post),
        }
        return self.ledger.append_resolution(rec)["record"]

    def anchor(self, method: str = "pending", **kw) -> dict:
        return make_anchor(self.ledger, method, utc_now(), **kw)["record"]

    def import_reference(self, items: list[dict], source: str) -> dict:
        return self.ledger.append_reference_batch(items, source, utc_now())["record"]

    # ----------------------------------------------------- mode fantôme
    def shadow_forecast(self, item: dict, now: str | None = None) -> dict | None:
        """Prévision fantôme sans LLM : la règle vient de la plateforme."""
        if item.get("is_resolved") or item.get("community_p") is None:
            return None
        now = now or utc_now()
        deadline = item.get("close_time_iso") or ""
        if not deadline and item.get("close_time_ms"):
            from datetime import datetime, timezone
            deadline = datetime.fromtimestamp(item["close_time_ms"] / 1000, timezone.utc).date().isoformat()
        from .engine.history import to_days
        days = max(1.0, to_days(deadline) - to_days(now)) if deadline else 365.0
        draft = {
            "input_text": item["title"], "now": now, "question_type": "binary", "text": item["title"],
            "definition": "", "resolution_rule": (item.get("resolution_criteria") or "")[:4000] or
            f"Résolution officielle de la plateforme {item['platform']}",
            "resolution_source": item.get("url") or item["platform"], "source_kind": "market_platform",
            "resolution_deadline": deadline[:10] or "inconnue", "horizon": f"P{int(days)}D",
            "ambiguity_policy": "cancel", "domain": "other", "subquestions": [],
            "resolution_stability_score": 0.5, "resolution_inter_model_agreement": None,
            "resolution_human_validated": False,
            "rule_validation_protocol": {"n_reformulations": 0, "agreement_metric": "règle de la plateforme"},
        }
        shadow = {"platform": item["platform"], "external_id": item["external_id"], "url": item.get("url", ""),
                  "community_p_at_forecast": item["community_p"]}
        question = make_question(draft, str(uuid.uuid5(uuid.NAMESPACE_URL, f"{item['platform']}:{item['external_id']}")), [])
        inputs = {"kind": "forecast_inputs", "input_text": item["title"], "now": now, "edits": None,
                  "validated": False, "sources": [], "market": None, "links": [], "annotations": [],
                  "shadow": shadow, "draft_override": draft}
        return self._forecast(question, draft, [], None, hash_obj([]), inputs, None, shadow)

    def shadow_sync(self, items: list[dict]) -> list[dict]:
        done = []
        for it in items:
            if not it.get("is_resolved") or it.get("resolution") is None:
                continue
            qid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{it['platform']}:{it['external_id']}"))
            if self.ledger.latest_forecast(qid) is None or self.ledger.resolution_for(qid) is not None:
                continue
            done.append(self.resolve(qid, it["resolution"], it.get("url", it["platform"]), 0.9))
        return done

    # ------------------------------------------------------------ vues
    def verify(self) -> dict:
        return self.ledger.verify()

    def scores(self) -> list[dict]:
        return score_records(self.ledger.entries, self.cfg)

    def dashboard(self) -> dict:
        return dashboard(self.ledger.entries, self.cfg)

    def tournament(self) -> dict:
        return tournament(self.ledger.entries, self.cfg)

    def shadow_report(self) -> dict:
        return shadow_comparison(self.ledger.entries, self.cfg)

    def export(self) -> str:
        return json.dumps(self.ledger.entries, ensure_ascii=False)
