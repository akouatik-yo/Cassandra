import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from oracle_calibre.canonical import Mulberry32  # noqa: E402
from oracle_calibre.config import load_config  # noqa: E402
from oracle_calibre.ingestion import ScriptedClient  # noqa: E402

NOW = "2026-10-04T12:00:00.000Z"


@pytest.fixture
def cfg():
    return load_config()


def scripted_llm(req):
    """LLM factice, déterministe, qui respecte le contrat des prompts.

    Les documents de test contiennent des lignes « FAIT|date|source|sens|type » ;
    la 3e extraction omet la dernière ligne (self-consistency < 1).
    """
    p = req.prompt
    if req.prompt_id == "formalize":
        q = p.split("Question from the user: ", 1)[1].split("\n", 1)[0]
        return {
            "question_type": "binary", "text": q, "definition": "Termes au sens usuel.",
            "resolution_rule": "OUI si l'événement est confirmé par la source officielle avant l'échéance.",
            "resolution_source": "https://example.org/officiel", "source_kind": "official_statistics",
            "resolution_deadline": "2027-01-02", "horizon": "P90D", "ambiguity_policy": "cancel",
            "domain": "economics", "is_atomic": True, "subquestions": [],
            "test_scenarios": ["Confirmé le 1er décembre", "Infirmé", "Confirmé après l'échéance",
                               "Source muette", "Confirmé par une source non officielle"],
            "self_negating_risk": False, "self_negating_reason": "",
        }
    if req.prompt_id == "apply_rule":
        return {"verdicts": ["YES", "NO", "NO", "NO", "AMBIGUOUS"]}
    if req.prompt_id == "reformulate":
        v = ["YES", "NO", "NO", "AMBIGUOUS", "NO"] if req.sample_index % 2 else ["YES", "NO", "NO", "NO", "AMBIGUOUS"]
        if req.role == "secondary":
            v = ["YES", "NO", "AMBIGUOUS", "NO", "NO"]
        return {"resolution_rule": f"Règle reformulée {req.sample_index}", "verdicts": v}
    if req.prompt_id == "extract":
        text = p.split("<<<\n", 1)[1].split("\n>>>", 1)[0]
        run = int(p.split("Extraction run ", 1)[1].split(".", 1)[0])
        lines = [l for l in text.splitlines() if l.count("|") == 4]
        if run == 3 and len(lines) > 1:
            lines = lines[:-1]
        trips = []
        for l in lines:
            fact, date, src, d, t = l.split("|")
            trips.append({"fact": fact, "date": date, "primary_source": src, "direction": d, "evidence_type": t,
                          "mentions_forecasting_tool": False})
        return {"triplets": trips}
    raise AssertionError(req.prompt_id)


@pytest.fixture
def llm():
    return ScriptedClient(scripted_llm)


@pytest.fixture
def llm2():
    c = ScriptedClient(scripted_llm)
    c.model_id = "scripted-second-model"
    return c


def synthetic_observations(n, *, quality=1.0, good=True, seed=7, t0=20600.0, step=0.1, class_key="binary|le90",
                           domain="economics", noise_outcomes=False, with_market=False):
    """Historique synthétique : y ~ Bern(pi) ; bayes informatif (good) ou anti-informatif."""
    rng = Mulberry32(seed)
    obs = []
    for i in range(n):
        pi = 0.1 + 0.8 * rng.random()
        y = 1 if rng.random() < (0.5 if noise_outcomes else pi) else 0
        pb = pi if good else 1.0 - pi
        pb = min(0.97, max(0.03, pb))
        comps = {"base_rate": 0.5, "bayes_hierarchical": pb}
        if with_market:
            comps["market_anchor"] = min(0.97, max(0.03, pi + 0.05 * (rng.random() - 0.5)))
        obs.append({"question_id": f"s{seed}-{i:04d}", "forecast_id": f"f{seed}-{i}", "t": t0 + i * step, "y": y,
                    "class_key": class_key, "domain": domain, "quality": quality, "components": comps,
                    "base_rate_p": 0.5, "mu_logit": None, "features": {}})
    return obs


def history_from(obs, cancelled=0):
    stats = {}
    for o in obs:
        st = stats.setdefault(o["class_key"], {"n": 0, "cancelled": 0})
        st["n"] += 1
    for st in stats.values():
        st["cancelled"] = cancelled
    ref = [{"question_type": "binary", "bucket": o["class_key"].split("|")[1], "domain": o["domain"], "y": o["y"],
            "t": o["t"]} for o in obs]
    return {"observations": sorted(obs, key=lambda o: (o["t"], o["question_id"])), "reference": ref,
            "resolution_stats": stats, "latest": {}}


def engine_input(as_of="2026-10-04T12:00:00.000Z", lineage=None, market=None, linked=None, stability=1.0):
    return {"question": {"question_id": "q-new", "question_type": "binary", "horizon": "P60D", "domain": "economics",
                         "resolution_stability_score": stability, "resolution_inter_model_agreement": None,
                         "source_kind": "official_statistics", "self_negating_risk": False},
            "as_of": as_of, "lineage": lineage or [], "market": market, "linked": linked or [],
            "linked_constraints": [], "annotations": []}


def source(i, anc, direction="yes", etype="poll", derived=False, ts="2026-09-30", consistency=1.0):
    return {"source_id": f"SRC-{i:03d}", "primary_ancestor_id": anc, "timestamp": ts, "method": "test",
            "derived_from_tool_output": derived, "self_consistency_score": consistency, "direction": direction,
            "evidence_type": etype, "fact": f"fait {anc}"}


def dump(obj):
    return json.dumps(obj, indent=1, ensure_ascii=False)
