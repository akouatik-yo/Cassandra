"""Les douze tests d'acceptation obligatoires du cahier des charges."""
import ast
import copy
import json
import socket
from pathlib import Path

import pytest

from conftest import NOW, engine_input, history_from, source, synthetic_observations
from oracle_calibre.app import OracleApp
from oracle_calibre.config import dependency_lock_hash
from oracle_calibre.engine import build_history, run_forecast
from oracle_calibre.engine.stacking import stack_weights
from oracle_calibre.ingestion.llm import LLMIsolationError, LLMRequest
from oracle_calibre.ledger import ImmutableRecordError, Ledger
from oracle_calibre.schema import MANDATORY_RESOLUTION_FIELDS, ValidationError

PKG = Path(__file__).resolve().parent.parent / "oracle_calibre"

DOC_A = {"title": "Note A", "url": "https://ex.org/a", "published_at": "2026-09-28",
         "text": "Hausse confirmée|2026-09-27|INSEE|yes|statistic\nSondage favorable|2026-09-25|IFOP|yes|poll\n"
                 "Report possible|2026-09-26|Ministère|no|official_statement"}
DOC_B = {"title": "Reprise de A", "url": "https://ex.org/b", "published_at": "2026-09-29",
         "text": "Hausse confirmée (reprise)|2026-09-27|INSEE|yes|statistic"}


def _full_flow(app, text, sources=(), links=None, edits=None, market=None):
    d = app.start_draft(text, now=NOW)
    if sources:
        app.add_sources(d["draft_id"], list(sources))
    app.validate(d["draft_id"], edits, True)
    return app.commit(d["draft_id"], market=market, links=links)


# 1 ---------------------------------------------------------------------------
def test_01_replay_from_snapshots_matches(tmp_path, llm, llm2):
    app = OracleApp(tmp_path, primary=llm, secondary=llm2)
    r1 = _full_flow(app, "Le chômage baissera-t-il ?", [DOC_A, DOC_B], market={"p": 0.62, "source": "manifold"})
    app.resolve(r1["question"]["question_id"], 1, "https://example.org/officiel", 0.95)
    r2 = _full_flow(app, "Le PIB progressera-t-il ?", [DOC_A], edits={"resolution_rule": "OUI si PIB > 0,3 %."})
    r3 = _full_flow(app, "L'inflation passera-t-elle sous 2 % ?", [DOC_B],
                    links=[{"question_id": r2["question"]["question_id"], "relation": "implies", "hard": True}])
    # Nouvelle instance, rechargée depuis le disque, SANS aucun client LLM.
    fresh = OracleApp(tmp_path, primary=None)
    for rec in (r1, r2, r3):
        rep = fresh.replay(rec["forecast_id"])
        assert rep["max_abs_diff"] < 1e-9, rep
        assert rep["engine_input_hash_match"]
        assert rec["reproducibility"]["dependency_lock_hash"] == dependency_lock_hash()


# 2 ---------------------------------------------------------------------------
FORBIDDEN_IMPORTS = ("anthropic", "openai", "oracle_calibre.ingestion", "google.generativeai", "mistralai")


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                pkg = ".".join(path.relative_to(PKG.parent).with_suffix("").parts[:-node.level])
                mod = f"{pkg}.{mod}" if mod else pkg
            names.append(mod)
    return names


def test_02_no_llm_call_outside_ingestion(cfg, llm):
    # a) statique : engine/ et ledger/ n'importent ni SDK de LLM ni ingestion/
    for sub in ("engine", "ledger", "extensions"):
        for f in (PKG / sub).rglob("*.py"):
            for name in _imports(f):
                assert not name.startswith(FORBIDDEN_IMPORTS), f"{f}: import interdit {name}"
            assert "api.anthropic.com" not in f.read_text(encoding="utf-8")
    for f in PKG.rglob("*.py"):
        if "ingestion" in f.parts:
            continue
        for name in _imports(f):
            assert not name.startswith(("anthropic", "openai")), f"{f}: SDK LLM importé hors ingestion"
    # b) dynamique : un appel depuis un module hors ingestion est refusé
    with pytest.raises(LLMIsolationError):
        llm.complete(LLMRequest("formalize", "x"))
    # c) le moteur tourne sans réseau
    real = socket.socket

    def blocked(*a, **k):
        raise AssertionError("accès réseau depuis le moteur")

    socket.socket = blocked
    try:
        out = run_forecast(engine_input(lineage=[source(1, "A1")]), build_history([], cfg), cfg)
    finally:
        socket.socket = real
    assert 0 < out["final_output"]["p_raw"] < 1


# 3 ---------------------------------------------------------------------------
def test_03_duplicated_primary_information_does_not_move_p(cfg):
    hist = history_from(synthetic_observations(150))
    one = run_forecast(engine_input(lineage=[source(1, "ANC-X"), source(2, "ANC-Y", "no", "statistic")]), hist, cfg)
    dup = [source(1, "ANC-X"), source(2, "ANC-Y", "no", "statistic")] + [source(10 + i, "ANC-X") for i in range(9)]
    ten = run_forecast(engine_input(lineage=dup), hist, cfg)
    assert abs(one["final_output"]["p_raw"] - ten["final_output"]["p_raw"]) < 1e-12
    assert ten["models_pipeline"]["lineage_fusion"]["n_ancestors"] == 2


def test_03b_duplicates_through_ingestion(tmp_path, llm):
    app = OracleApp(tmp_path, primary=llm)
    base = _full_flow(app, "Question A ?", [DOC_A])
    many = _full_flow(app, "Question B ?", [DOC_A] + [dict(DOC_B, url=f"https://ex.org/copie{i}") for i in range(10)])
    assert abs(base["final_output"]["p_raw"] - many["final_output"]["p_raw"]) < 1e-12


# 4 ---------------------------------------------------------------------------
def _blocks(base, k, span):
    """k copies d'un même historique, chacune décalée plus loin dans le passé :
    plus d'historique validé, de qualité strictement égale."""
    out = []
    for r in range(k):
        out += [dict(o, question_id=f"{o['question_id']}-b{r}", t=o["t"] - r * span) for o in base]
    return out


def test_04_w_monotone_in_validated_history_and_zero_without(cfg):
    w_empty = run_forecast(engine_input(), history_from([]), cfg)["final_output"]["w_displayed"]
    assert w_empty < 0.1, "sans historique poolé, w doit tendre vers 0"
    base = synthetic_observations(60, seed=11, t0=20724.0, step=0.1)
    ws = [run_forecast(engine_input(), history_from(_blocks(base, k, 6.0)), cfg)["final_output"]["w_displayed"]
          for k in (1, 2, 4, 8, 16)]
    assert ws[0] > w_empty
    for a, b in zip(ws, ws[1:]):
        assert b >= a - 1e-12, ws


# 5 ---------------------------------------------------------------------------
def test_05_low_quality_resolutions_do_not_increase_w(cfg):
    good = synthetic_observations(150, seed=3)
    w0 = run_forecast(engine_input(), history_from(good), cfg)["final_output"]["w_displayed"]
    for seed in (21, 22, 23):
        bad = synthetic_observations(150, seed=seed, quality=0.1, noise_outcomes=True, t0=20610.0)
        w1 = run_forecast(engine_input(), history_from(good + bad), cfg)["final_output"]["w_displayed"]
        assert w1 <= w0 + 1e-12, (w0, w1)


# 6 ---------------------------------------------------------------------------
def _degrade(obs, last):
    out = []
    for i, o in enumerate(obs):
        if i >= len(obs) - last:
            c = dict(o["components"])
            c["bayes_hierarchical"] = 1.0 - c["bayes_hierarchical"]
            o = dict(o, components=c)
        out.append(o)
    return out


def test_06_degraded_component_loses_weight_to_diffuse(cfg):
    t_now = 20730.5
    for with_market in (False, True):
        names = ["base_rate", "bayes_hierarchical"] + (["market_anchor"] if with_market else [])
        healthy = synthetic_observations(260, seed=5, with_market=with_market)
        degraded = _degrade(healthy, 60)  # mêmes questions, mêmes issues : seul bayes se dégrade
        w_h = stack_weights(healthy, names, "binary|le90", t_now, cfg)
        w_d = stack_weights(degraded, names, "binary|le90", t_now, cfg)
        assert w_d["class"]["bayes_hierarchical"] < w_h["class"]["bayes_hierarchical"]
        assert w_d["class"]["base_rate"] > w_h["class"]["base_rate"], (with_market, w_h["class"], w_d["class"])
        assert "bayes_hierarchical" in w_d["breaks_class"]
        assert "bayes_hierarchical" not in w_h["breaks_class"]


# 7 ---------------------------------------------------------------------------
def test_07_recompute_p_from_stored_components_and_weights(tmp_path, llm):
    app = OracleApp(tmp_path, primary=llm)
    rec = _full_flow(app, "Question ?", [DOC_A], market={"p": 0.7, "source": "test"})
    mp, fo = rec["models_pipeline"], rec["final_output"]
    p = 0.0
    for c in mp["component_forecasts"]:
        p += mp["stacking_weights"][c["component"]] * c["p"]
    assert p == fo["p_raw"]
    assert abs(1.0 - mp["stacking_weights"]["base_rate"] - fo["w_displayed"]) < 1e-15
    # Pas de double rétrécissement : appliquer w une seconde fois donnerait autre chose.
    base = [c["p"] for c in mp["component_forecasts"] if c["component"] == "base_rate"][0]
    twice = (1 - fo["w_displayed"]) * base + fo["w_displayed"] * fo["p_raw"]
    assert abs(twice - fo["p_raw"]) > 1e-6 or fo["w_displayed"] in (0.0, 1.0)
    assert fo["p_reconciled"] == fo["p_raw"]  # aucune question liée


# 8 ---------------------------------------------------------------------------
def test_08_injected_logical_violation_is_projected(cfg, tmp_path, llm):
    linked = [{"question_id": "B", "relation": "implies", "hard": True, "p": 0.05}]
    out = run_forecast(engine_input(market={"p": 0.9}, linked=linked), build_history([], cfg), cfg)
    vec = out["models_pipeline"]["reconciliation_vector"]
    assert vec["raw"][0] > vec["raw"][1]  # violation : P(A) > P(B) avec A => B
    assert vec["reconciled"][0] <= vec["reconciled"][1] + 1e-9
    assert out["final_output"]["p_raw"] != out["final_output"]["p_reconciled"]
    assert out["models_pipeline"]["reconciliation"]["violations_after"][0] < 1e-9
    # De bout en bout : les deux valeurs sont journalisées dans le registre.
    app = OracleApp(tmp_path, primary=llm)
    b = _full_flow(app, "B ?", [])
    a = _full_flow(app, "A ?", [DOC_A], market={"p": 0.97},
                   links=[{"question_id": b["question"]["question_id"], "relation": "implies", "hard": True}])
    assert a["final_output"]["p_raw"] > b["final_output"]["p_reconciled"]
    assert a["final_output"]["p_reconciled"] <= a["models_pipeline"]["reconciliation_vector"]["reconciled"][1] + 1e-9
    stored = Ledger(tmp_path / "ledger.jsonl").find_forecast(a["forecast_id"])["record"]["final_output"]
    assert "p_raw" in stored and "p_reconciled" in stored


def test_08b_soft_constraint_only_penalised(cfg):
    linked = [{"question_id": "B", "relation": "implies", "hard": False, "p": 0.05}]
    out = run_forecast(engine_input(market={"p": 0.9}, linked=linked), build_history([], cfg), cfg)
    v = out["models_pipeline"]["reconciliation_vector"]
    assert v["reconciled"][0] < v["raw"][0]
    assert out["models_pipeline"]["reconciliation"]["method"].startswith("bregman")


# 9 ---------------------------------------------------------------------------
def test_09_tampering_invalidates_following_hashes_and_merkle(tmp_path, llm):
    app = OracleApp(tmp_path, primary=llm)
    r1 = _full_flow(app, "Q1 ?", [DOC_A])
    _full_flow(app, "Q2 ?", [])
    app.anchor("pending")
    assert app.verify()["ok"]
    path = tmp_path / "ledger.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    idx = next(i for i, l in enumerate(lines) if r1["forecast_id"] in l and '"record_type":"forecast"' in l)
    e = json.loads(lines[idx])
    e["record"]["final_output"]["p_raw"] = 0.99
    lines[idx] = json.dumps(e)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rep = Ledger(path).verify()
    assert not rep["ok"]
    assert any(p["seq"] == idx and "modifié" in p["problem"] for p in rep["problems"])
    # L'attaquant recalcule le hash de l'entrée : la suivante ne chaîne plus.
    from oracle_calibre.ledger.store import entry_hash
    e["entry_hash"] = entry_hash(e["seq"], e["record_type"], e["record"])
    lines[idx] = json.dumps(e)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rep = Ledger(path).verify()
    assert any(p["seq"] == idx + 1 and "hash_previous" in p["problem"] for p in rep["problems"])
    # Il recalcule toute la suite : la racine de Merkle ancrée ne concorde plus.
    entries = [json.loads(l) for l in lines]
    for j in range(idx + 1, len(entries)):
        entries[j]["record"]["hash_previous"] = entries[j - 1]["entry_hash"]
        entries[j]["entry_hash"] = entry_hash(entries[j]["seq"], entries[j]["record_type"], entries[j]["record"])
    path.write_text("\n".join(json.dumps(x) for x in entries) + "\n", encoding="utf-8")
    rep = Ledger(path).verify()
    assert not rep["ok"]
    assert any("Merkle" in p["problem"] for p in rep["problems"])


# 10 --------------------------------------------------------------------------
def test_10_forecast_record_can_only_be_superseded(tmp_path, llm):
    app = OracleApp(tmp_path, primary=llm)
    r1 = _full_flow(app, "Q ?", [DOC_A])
    led = app.ledger
    for name in ("update", "modify", "delete", "remove", "replace", "edit", "set"):
        assert not hasattr(led, name)
    with pytest.raises(ImmutableRecordError):
        led.append_forecast(copy.deepcopy(r1))
    entries = led.entries
    entries[-1]["record"]["final_output"]["p_raw"] = 0.0
    assert led.find_forecast(r1["forecast_id"])["record"]["final_output"]["p_raw"] == r1["final_output"]["p_raw"]
    clone = copy.deepcopy(r1)
    clone["forecast_id"] = "autre-id"
    with pytest.raises(ImmutableRecordError):
        led.append_forecast(clone)  # même question sans supersedes_forecast_id
    d = app.start_draft("Q ?", now=NOW)
    app.validate(d["draft_id"], None, True)
    r2 = app.commit(d["draft_id"], supersedes=r1["forecast_id"])
    assert r2["supersedes_forecast_id"] == r1["forecast_id"]
    assert r2["question"]["question_id"] == r1["question"]["question_id"]
    assert led.find_forecast(r1["forecast_id"])["record"] == r1
    assert app.verify()["ok"]


# 11 --------------------------------------------------------------------------
@pytest.mark.parametrize("field", MANDATORY_RESOLUTION_FIELDS)
def test_11_refused_when_mandatory_resolution_field_missing(tmp_path, llm, field):
    app = OracleApp(tmp_path, primary=llm)
    good = _full_flow(app, "Q ?", [])
    bad = copy.deepcopy(good)
    bad["forecast_id"] = "x"
    bad["question"]["question_id"] = "autre"
    bad["question"][field] = ""
    with pytest.raises(ValidationError):
        Ledger(None).append_forecast(bad)
    if field != "question_definition_version":
        d = app.start_draft("Q2 ?", now=NOW)
        app.drafts[d["draft_id"]]["draft"][field] = ""
        with pytest.raises(ValidationError):
            app.commit(d["draft_id"])
    assert len([e for e in app.ledger.entries if e["record_type"] == "forecast"]) == 1


# 12 --------------------------------------------------------------------------
def test_12_tool_derived_sources_never_aggregated(cfg, tmp_path, llm):
    hist = history_from(synthetic_observations(150))
    clean = [source(1, "A1"), source(2, "A2", "no", "statistic")]
    p0 = run_forecast(engine_input(lineage=clean), hist, cfg)
    polluted = clean + [source(3, "A9", "yes", "market_price", derived=True),
                        source(4, "A1", "no", "poll", derived=True)]
    p1 = run_forecast(engine_input(lineage=polluted), hist, cfg)
    assert p0["final_output"]["p_raw"] == p1["final_output"]["p_raw"]
    excl = {e["source_id"] for e in p1["models_pipeline"]["lineage_fusion"]["excluded"]}
    assert excl == {"SRC-003", "SRC-004"}
    # Par l'ingestion : un document qui cite l'outil est marqué derived_from_tool_output.
    app = OracleApp(tmp_path, primary=llm)
    doc = {"title": "Blog", "url": "https://ex.org/c", "published_at": "2026-09-30",
           "text": "Selon Oracle Calibré, 80 %|2026-09-30|Blog|yes|other"}
    rec = _full_flow(app, "Q ?", [DOC_A, doc])
    flagged = [s for s in rec["information_lineage"] if s["derived_from_tool_output"]]
    assert flagged and all(s["document_url"] == "https://ex.org/c" for s in flagged)
    ref = _full_flow(app, "Q bis ?", [DOC_A])
    assert rec["final_output"]["p_raw"] == ref["final_output"]["p_raw"]
