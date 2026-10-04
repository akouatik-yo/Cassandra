"""Ingestion : formalisation, stabilité de la règle, décomposition, extraction.

Toutes les sorties LLM passent par _call(), qui les fige en snapshots
(requête, réponse, modèle) référencés par SHA-256. La confiance déclarée
par un LLM n'est jamais demandée ni utilisée : la stabilité et la
self-consistency se mesurent par accord entre sorties répétées.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone

from ..canonical import canonical_json, hash_obj, sha256_hex
from ..schema import AMBIGUITY_POLICIES, QUESTION_TYPES, RELATIONS
from .llm import LLMClient, LLMRequest, parse_json_reply

VERDICTS = ("YES", "NO", "AMBIGUOUS")
TOOL_MARKERS = re.compile(r"oracle\s*calibr|forecast_id", re.IGNORECASE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def render(template: str, **values) -> str:
    out = template
    for k, v in values.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def prompts_hash(prompts: dict) -> str:
    return sha256_hex(canonical_json(prompts))


class Session:
    """Contexte d'une ingestion : clients, prompts et snapshots accumulés."""

    def __init__(self, primary: LLMClient, prompts: dict, cfg: dict, secondary: LLMClient | None = None):
        self.primary, self.secondary, self.prompts, self.cfg = primary, secondary, prompts, cfg
        self.snapshots: list[dict] = []

    def call(self, prompt_id: str, role: str = "primary", **values):
        """Appel LLM figé. sample_index = rang de l'appel pour ce prompt et ce rôle :
        l'ordre des appels étant déterministe, le rejeu retrouve chaque réponse."""
        client = self.primary if role == "primary" else self.secondary
        sample_index = sum(1 for s in self.snapshots if s["prompt_id"] == prompt_id and s["role"] == role)
        prompt = render(self.prompts[prompt_id], **values)
        req = LLMRequest(prompt_id, prompt, role, sample_index)
        text = client.complete(req)
        parsed = parse_json_reply(text)
        info = client.info()
        self.snapshots.append({
            "request_key": req.key(), "prompt_id": prompt_id, "role": role, "sample_index": sample_index,
            "prompt": prompt, "response_text": text, "model_id": info["model_id"],
            "model_version": info["model_version"], "provider": info["llm_provider"],
        })
        return parsed

    def bundle(self) -> dict:
        hashes = [hash_obj(s) for s in self.snapshots]
        return {"snapshot_hashes": hashes, "llm_snapshots_hash": hash_obj(hashes)}


def _clean_verdicts(v, n: int) -> list[str]:
    out = [str(x).upper() for x in (v or [])][:n]
    out = [x if x in VERDICTS else "AMBIGUOUS" for x in out]
    return out + ["AMBIGUOUS"] * (n - len(out))


def agreement(a: list[str], b: list[str]) -> float:
    if not a:
        return 0.0
    same = 0
    for i in range(len(a)):
        if a[i] == b[i]:
            same += 1
    return same / len(a)


def mean_pairwise_agreement(vectors: list[list[str]]) -> float:
    if len(vectors) < 2:
        return 0.0
    s, n = 0.0, 0
    for i in range(len(vectors)):
        for j in range(i + 1, len(vectors)):
            s += agreement(vectors[i], vectors[j])
            n += 1
    return s / n


def majority(vectors: list[list[str]]) -> list[str]:
    out = []
    for i in range(len(vectors[0])):
        counts: dict[str, int] = {}
        for v in vectors:
            counts[v[i]] = counts.get(v[i], 0) + 1
        out.append(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0])
    return out


def _scenarios_text(scen: list[str]) -> str:
    return "\n".join(f"{i + 1}. {s}" for i, s in enumerate(scen))


def formalize(session: Session, question_text: str, now: str) -> dict:
    """Étape 1 : formalisation + N reformulations indépendantes + second modèle."""
    f = session.call("formalize", question=question_text, now=now)
    qtype = f.get("question_type") if f.get("question_type") in QUESTION_TYPES else "binary"
    policy = f.get("ambiguity_policy") if f.get("ambiguity_policy") in AMBIGUITY_POLICIES else "cancel"
    scen = [str(s) for s in (f.get("test_scenarios") or [])][:5]
    subq = []
    for s in f.get("subquestions") or []:
        if s.get("relation") in RELATIONS:
            subq.append({"text": s.get("text", ""), "resolution_rule": s.get("resolution_rule", ""),
                         "relation": s["relation"], "hard": bool(s.get("hard", False))})
    draft = {
        "input_text": question_text, "now": now,
        "question_type": qtype, "text": f.get("text") or question_text, "definition": f.get("definition", ""),
        "resolution_rule": f.get("resolution_rule", ""), "resolution_source": f.get("resolution_source", ""),
        "source_kind": f.get("source_kind", "other"), "resolution_deadline": f.get("resolution_deadline", ""),
        "horizon": f.get("horizon", ""), "ambiguity_policy": policy, "domain": f.get("domain") or "other",
        "is_atomic": bool(f.get("is_atomic", True)), "subquestions": subq, "test_scenarios": scen,
        "self_negating_risk": bool(f.get("self_negating_risk", False)),
        "self_negating_reason": f.get("self_negating_reason", ""),
    }
    measure_rule_stability(session, draft)
    return draft


def measure_rule_stability(session: Session, draft: dict, reformulate: bool = True) -> None:
    """Accord entre la règle retenue et N règles écrites indépendamment,
    mesuré sur leurs verdicts pour les mêmes scénarios de test.

    Les reformulations ne voient pas la règle retenue : si l'utilisateur ne
    modifie que la règle, seules ses verdicts sont recalculés.
    """
    n = session.cfg["ingestion"]["n_reformulations"]
    scen = draft["test_scenarios"]
    st = _scenarios_text(scen)
    applied = session.call("apply_rule", question=draft["text"], rule=draft["resolution_rule"], scenarios=st)
    if reformulate:
        reforms = []
        for i in range(n):
            r = session.call("reformulate", index=i + 1, question=draft["text"],
                             deadline=draft["resolution_deadline"], scenarios=st)
            reforms.append({"resolution_rule": r.get("resolution_rule", ""),
                            "verdicts": _clean_verdicts(r.get("verdicts"), len(scen))})
        second = None
        if session.secondary is not None:
            r2 = session.call("reformulate", role="secondary", index=1, question=draft["text"],
                              deadline=draft["resolution_deadline"], scenarios=st)
            second = {"resolution_rule": r2.get("resolution_rule", ""),
                      "verdicts": _clean_verdicts(r2.get("verdicts"), len(scen)),
                      "model_id": session.secondary.model_id}
        draft["reformulations"] = reforms
        draft["second_model_reformulation"] = second
    vectors = [_clean_verdicts(applied.get("verdicts"), len(scen))] + [r["verdicts"] for r in draft["reformulations"]]
    second = draft.get("second_model_reformulation")
    inter = agreement(second["verdicts"], majority(vectors)) if second else None
    draft["rule_verdicts"] = vectors[0]
    draft["resolution_stability_score"] = mean_pairwise_agreement(vectors)
    draft["resolution_inter_model_agreement"] = inter
    draft["rule_validation_protocol"] = {
        "n_reformulations": n, "n_scenarios": len(scen),
        "agreement_metric": "accord moyen par paires des verdicts YES/NO/AMBIGUOUS sur scénarios de test",
        "second_model": session.secondary.model_id if session.secondary else None,
    }


def apply_human_validation(session: Session, draft: dict, edits: dict | None, validated: bool) -> dict:
    """Écran de validation : l'utilisateur peut corriger la règle avant gel."""
    edits = {k: v for k, v in (edits or {}).items() if v not in (None, "")}
    rule_changed, text_changed = False, False
    for k in ("resolution_rule", "resolution_source", "resolution_deadline", "horizon", "ambiguity_policy", "text"):
        if k in edits and edits[k] != draft.get(k):
            draft[k] = edits[k]
            rule_changed = rule_changed or k == "resolution_rule"
            text_changed = text_changed or k in ("text", "resolution_deadline")
    if rule_changed or text_changed:
        measure_rule_stability(session, draft, reformulate=text_changed)
    draft["rule_edited_by_human"] = bool(edits)
    draft["resolution_human_validated"] = bool(validated)
    return draft


def ancestor_id(primary_source: str, date: str) -> str:
    s = unicodedata.normalize("NFKD", primary_source or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return "ANC-" + sha256_hex(f"{s}|{(date or '')[:10]}")[:12]


def extract_sources(session: Session, draft: dict, sources: list[dict], now: str) -> dict:
    """Collecte (fournie par l'utilisateur), filtre de date strict, extraction répétée.

    sources : [{title, url, published_at, text, derived_from_tool_output?}]
    """
    k_runs = session.cfg["ingestion"]["n_extractions"]
    lineage, rejected = [], []
    retrieval = []
    for d_idx, src in enumerate(sources):
        pub = src.get("published_at") or ""
        retrieval.append({k: src.get(k) for k in ("title", "url", "published_at", "text")})
        if not pub or pub[:10] > now[:10] or (len(pub) > 10 and pub > now):
            rejected.append({"title": src.get("title"), "reason": "date de publication absente ou postérieure"})
            continue
        runs = []
        for r in range(k_runs):
            out = session.call("extract", index=r + 1, question=draft["text"],
                               rule=draft["resolution_rule"], now=now, title=src.get("title", ""),
                               url=src.get("url", ""), published_at=pub, text=src.get("text", ""))
            runs.append(out.get("triplets") or [])
        seen: dict[str, dict] = {}
        for r, trips in enumerate(runs):
            for t in trips:
                anc = ancestor_id(t.get("primary_source", ""), t.get("date", ""))
                d = t.get("direction") if t.get("direction") in ("yes", "no", "neutral") else "neutral"
                entry = seen.setdefault(anc, {"votes": {}, "first": {}, "runs": set()})
                if r not in entry["runs"]:
                    entry["runs"].add(r)
                    entry["votes"][d] = entry["votes"].get(d, 0) + 1
                    entry["first"].setdefault(d, t)
        for j, anc in enumerate(sorted(seen)):
            e = seen[anc]
            modal, cnt = sorted(e["votes"].items(), key=lambda kv: (-kv[1], kv[0]))[0]
            t = e["first"][modal]
            derived = bool(src.get("derived_from_tool_output")) or bool(t.get("mentions_forecasting_tool")) \
                or bool(TOOL_MARKERS.search(src.get("text", "") or ""))
            lineage.append({
                "source_id": f"SRC-{d_idx + 1:03d}-{j + 1:02d}",
                "primary_ancestor_id": anc,
                "timestamp": pub,
                "method": f"llm_extraction:{session.primary.model_id}:k={k_runs}",
                "derived_from_tool_output": derived,
                "self_consistency_score": cnt / k_runs,
                "direction": modal,
                "evidence_type": t.get("evidence_type") or "other",
                "fact": t.get("fact", ""),
                "fact_date": t.get("date", ""),
                "primary_source": t.get("primary_source", ""),
                "document_url": src.get("url", ""),
                "document_title": src.get("title", ""),
            })
    return {"lineage": lineage, "rejected": rejected, "retrieval_snapshot_hash": hash_obj(retrieval),
            "retrieval": retrieval}
