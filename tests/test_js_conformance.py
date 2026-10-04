"""Conformance croisée Python (référence) / JavaScript (version hébergée).

Mêmes entrées -> mêmes hash à l'octet près, mêmes nombres à 1e-9 près.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import NOW, engine_input, history_from, source, synthetic_observations
from test_acceptance import DOC_A, DOC_B, _degrade, _full_flow
from oracle_calibre.app import OracleApp
from oracle_calibre.canonical import Mulberry32, canonical_json, hash_obj
from oracle_calibre.engine import build_history, run_forecast
from oracle_calibre.engine.reconcile import reconcile
from oracle_calibre.engine.stacking import stack_weights
from oracle_calibre.engine.uncertainty import extraction_error_rates
from oracle_calibre.ingestion.pipeline import ancestor_id
from oracle_calibre.ledger import verify_entries
from oracle_calibre.ledger.merkle import merkle_root
from oracle_calibre.ledger.scoring import dashboard, score_records
from oracle_calibre.ledger.shadow import shadow_comparison
from oracle_calibre.ledger.tournament import tournament

RUNNER = Path(__file__).with_name("js_runner.js")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node absent")


def _js(cases):
    out = subprocess.run(["node", str(RUNNER)], input=json.dumps(cases), capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _close(a, b, path="$", tol=1e-9):
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
        assert a == b, f"{path}: {a!r} != {b!r}"
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        assert abs(a - b) <= tol * max(1.0, abs(a)), f"{path}: {a!r} != {b!r}"
    elif isinstance(a, (list, tuple)):
        assert isinstance(b, list) and len(a) == len(b), f"{path}: longueurs {len(a)} != {len(b) if isinstance(b, list) else b}"
        for i, (x, y) in enumerate(zip(a, b)):
            _close(x, y, f"{path}[{i}]", tol)
    elif isinstance(a, dict):
        assert isinstance(b, dict), f"{path}: dict attendu"
        ka = {k for k, v in a.items()}
        kb = set(b)
        assert {str(k) for k in ka} == kb, f"{path}: clés {sorted(map(str, ka))} != {sorted(kb)}"
        for k, v in a.items():
            _close(v, b[str(k)], f"{path}.{k}", tol)
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


def _norm(x):
    return json.loads(json.dumps(x))


def test_python_and_js_engines_agree(tmp_path, cfg, llm, llm2):
    # Registre réel construit par l'application Python (résolutions, mises à jour, liens, fantôme).
    app = OracleApp(tmp_path, primary=llm, secondary=llm2)
    recs = []
    for i in range(7):
        r = _full_flow(app, f"Question {i} ?", [DOC_A, DOC_B] if i % 2 else [DOC_A], market={"p": 0.2 + 0.1 * i})
        recs.append(r)
        app.resolve(r["question"]["question_id"], i % 2, "https://example.org", 0.6 + 0.05 * i)
    upd = _full_flow(app, "Mise à jour ?", [DOC_A])
    d = app.start_draft("Mise à jour ?", now=NOW)
    app.validate(d["draft_id"], None, True)
    app.commit(d["draft_id"], supersedes=upd["forecast_id"])
    _full_flow(app, "Liée ?", [DOC_B], links=[{"question_id": upd["question"]["question_id"], "relation": "implies",
                                                "hard": True}], market={"p": 0.95})
    app.resolve(upd["question"]["question_id"], 1, "https://example.org", 0.9)
    app.import_reference([{"question_type": "binary", "horizon_days": 60, "domain": "economics", "outcome": 1,
                           "resolved_at": "2026-01-02"}], "test")
    app.anchor("pending")
    entries = _norm(app.ledger.entries)

    hist_syn = history_from(synthetic_observations(200, seed=4, with_market=True))
    linked = [{"question_id": "B", "relation": "implies", "hard": True, "p": 0.1},
              {"question_id": "C", "relation": "exclusive", "hard": False, "p": 0.6}]
    lineage = [source(1, "A1"), source(2, "A2", "no", "statistic"), source(3, "A1", "yes", "poll"),
               source(4, "A3", "yes", "other", derived=True), source(5, "A4", "neutral", "osint", consistency=0.67)]
    cases, expected = [], []

    def add(fn, args, value, exact=False):
        cases.append({"fn": fn, "args": _norm(args)})
        expected.append((fn, _norm(value), exact))

    sample = {"b": [1.0, 2.5, None, True, "é\n\"x\""], "a": {"z": 1e-05, "y": 1.5e16, "x": -0.0001, "w": 123456.789}}
    add("canonical", [sample], canonical_json(sample), exact=True)
    add("hash_obj", [sample], hash_obj(sample), exact=True)
    rng = Mulberry32(20261004)
    add("mulberry", [20261004, 50], [rng.next_u32() for _ in range(50)], exact=True)
    for fid_inp, hist in [
        (engine_input(lineage=lineage, market={"p": 0.8}, linked=linked), hist_syn),
        (engine_input(lineage=lineage[:2], stability=0.6), history_from([])),
        (engine_input(market={"p": 0.3}), build_history(entries, cfg)),
    ]:
        add("run_forecast", [fid_inp, hist, cfg], run_forecast(fid_inp, hist, cfg))
    add("build_history", [entries, cfg, None], build_history(entries, cfg))
    add("build_history", [entries, cfg, 20], build_history(entries, cfg, before_seq=20))
    leaves = [e["entry_hash"] for e in entries[:9]]
    add("merkle_root", [leaves], merkle_root(leaves), exact=True)
    add("verify", [entries], verify_entries(entries), exact=True)
    add("dashboard", [entries, cfg], dashboard(entries, cfg))
    add("tournament", [entries, cfg], tournament(entries, cfg))
    add("score_records", [entries, cfg], score_records(entries, cfg))
    add("shadow", [entries, cfg], shadow_comparison(entries, cfg))
    cons = [{"relation": "partition", "members": [0, 1, 2], "hard": True},
            {"relation": "implies", "a": 3, "b": 0, "hard": False}]
    add("reconcile", [[0.5, 0.4, 0.3, 0.7], cons, cfg], reconcile([0.5, 0.4, 0.3, 0.7], cons, cfg))
    obs = synthetic_observations(260, seed=9, with_market=True)
    names = ["base_rate", "bayes_hierarchical", "market_anchor"]
    add("stack_weights", [obs, names, "binary|le90", 20730.5, cfg], stack_weights(obs, names, "binary|le90", 20730.5, cfg))
    deg = _degrade(obs, 60)
    sw_deg = stack_weights(deg, names, "binary|le90", 20730.5, cfg)
    assert sw_deg["breaks_class"], "le cas dégradé doit déclencher une rupture"
    add("stack_weights", [deg, names, "binary|le90", 20730.5, cfg], sw_deg)
    add("run_forecast", [engine_input(market={"p": 0.6}), history_from(deg), cfg],
        run_forecast(engine_input(market={"p": 0.6}), history_from(deg), cfg))
    add("ancestor_id", ["Institut Économique (INSEE)", "2026-09-27T10:00"], ancestor_id("Institut Économique (INSEE)",
                                                                                        "2026-09-27T10:00"), exact=True)
    ann = [{"type": "date", "n_checked": 40, "n_errors": 3}, {"type": "relation", "n_checked": 25, "n_errors": 4}]
    add("extraction_rates", [ann, cfg], extraction_error_rates(ann, cfg))

    results = _js(cases)
    for (fn, exp, exact), res in zip(expected, results):
        assert res["ok"], f"{fn}: {res.get('error')}"
        if exact:
            assert res["value"] == exp, f"{fn}: {res['value']!r} != {exp!r}"
        else:
            _close(exp, res["value"], fn)


def test_js_verifies_python_ledger_and_detects_tampering(tmp_path, llm):
    app = OracleApp(tmp_path, primary=llm)
    _full_flow(app, "Q ?", [DOC_A])
    app.anchor("pending")
    entries = _norm(app.ledger.entries)
    entries_bad = json.loads(json.dumps(entries))
    entries_bad[0]["record"]["kind"] = "altéré"
    ok, bad = _js([{"fn": "verify", "args": [entries]}, {"fn": "verify", "args": [entries_bad]}])
    assert ok["value"]["ok"] is True
    assert bad["value"]["ok"] is False
