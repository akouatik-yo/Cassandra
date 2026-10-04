"""Tests unitaires et de robustesse (au-delà des douze tests obligatoires)."""
import base64

import pytest

from conftest import NOW, engine_input, history_from, synthetic_observations
from test_acceptance import DOC_A, _blocks, _degrade, _full_flow
from oracle_calibre.app import OracleApp
from oracle_calibre.canonical import Mulberry32, canon_num, canonical_json
from oracle_calibre.engine import run_forecast
from oracle_calibre.engine.mathx import beta_ppf, betainc, lgamma, norm_cdf
from oracle_calibre.engine.reconcile import reconcile
from oracle_calibre.engine.stacking import stack_weights
from oracle_calibre.extensions import NotInMVP, crps_quantile, extremize
from oracle_calibre.ledger.anchor import build_timestamp_request
from oracle_calibre.ledger.merkle import merkle_root
from oracle_calibre.ledger.scoring import (block_bootstrap_ci, calibration_curve, diebold_mariano,
                                           murphy_decomposition)
from oracle_calibre.ledger.shadow import normalize_manifold, normalize_metaculus
from oracle_calibre.schema import ValidationError, iso_duration_days, validate_distribution


def test_canonical_numbers_match_js_convention():
    assert canon_num(3.0) == "3"
    assert canon_num(0.1) == "0.1"
    assert canon_num(1e-05) == "1e-05"
    assert canon_num(1.5e16) == "1.5e+16"
    assert canonical_json({"b": [1.0, None, True], "a": "é\n"}) == '{"a":"é\\n","b":[1,null,true]}'


def test_mulberry32_is_deterministic_and_uniform():
    a, b = Mulberry32(1729), Mulberry32(1729)
    xs = [a.random() for _ in range(2000)]
    assert xs == [b.random() for _ in range(2000)]
    assert all(0.0 <= x < 1.0 for x in xs)
    assert abs(sum(xs) / len(xs) - 0.5) < 0.03


def test_special_functions():
    assert abs(lgamma(5.0) - 3.1780538303479458) < 1e-12
    assert abs(betainc(2.0, 3.0, 0.4) - 0.5248) < 1e-12
    assert abs(beta_ppf(0.5, 1.0, 1.0) - 0.5) < 1e-12
    assert abs(norm_cdf(1.959963985) - 0.975) < 1e-6


def test_iso_duration_and_distributions():
    assert iso_duration_days("P90D") == 90
    assert abs(iso_duration_days("P1Y") - 365.25) < 1e-12
    for d in ({"type": "bernoulli", "p": 0.3}, {"type": "categorical", "probs": [0.2, 0.8]},
              {"type": "quantile", "levels": [0.1, 0.9], "values": [1, 2]},
              {"type": "survival", "times": [1, 2], "survival": [0.9, 0.5]}):
        validate_distribution(d)
    with pytest.raises(ValidationError):
        validate_distribution({"type": "beta", "alpha": 1})


def test_extensions_are_declared_not_implemented():
    with pytest.raises(NotInMVP):
        crps_quantile([0.5], [1.0], 1.0)
    with pytest.raises(NotInMVP):
        extremize(0.7, 0.3)


def test_merkle_root_changes_with_any_leaf():
    leaves = [f"{i:064x}" for i in range(7)]
    r = merkle_root(leaves)
    for i in range(7):
        altered = list(leaves)
        altered[i] = f"{i + 100:064x}"
        assert merkle_root(altered) != r
    assert merkle_root(leaves[:6]) != r


def test_rfc3161_request_structure():
    der = build_timestamp_request("ab" * 32, nonce=12345)
    assert der[0] == 0x30
    assert bytes.fromhex("608648016503040201") in der  # OID sha256
    assert bytes.fromhex("ab" * 32) in der
    assert der[-3:] == bytes([0x01, 0x01, 0xFF])
    assert base64.b64encode(der)


def test_reconcile_partition_and_exclusive(cfg):
    out = reconcile([0.5, 0.4, 0.3], [{"relation": "partition", "members": [0, 1, 2], "hard": True}], cfg)
    assert abs(sum(out["x"]) - 1.0) < 1e-9
    ex = reconcile([0.7, 0.6], [{"relation": "exclusive", "a": 0, "b": 1, "hard": True}], cfg)
    assert ex["x"][0] + ex["x"][1] <= 1.0 + 1e-9
    # Le point cohérent n'est pas déplacé.
    ok = reconcile([0.2, 0.5], [{"relation": "implies", "a": 0, "b": 1, "hard": True}], cfg)
    assert abs(ok["x"][0] - 0.2) < 1e-9 and abs(ok["x"][1] - 0.5) < 1e-9


def test_predd_dominance_of_projection(cfg):
    """La projection de Bregman (KL) améliore le log-score dans tous les mondes cohérents."""
    import math
    q = [0.8, 0.3]
    x = reconcile(q, [{"relation": "implies", "a": 0, "b": 1, "hard": True}], cfg)["x"]
    for world in ([0, 0], [0, 1], [1, 1]):
        def loss(p):
            return -sum(math.log(p[i] if world[i] else 1 - p[i]) for i in range(2))
        assert loss(x) < loss(q)


def test_murphy_and_calibration():
    ps = [0.1] * 10 + [0.9] * 10
    ys = [0] * 9 + [1] + [1] * 9 + [0]
    m = murphy_decomposition(ps, ys, 10)
    assert abs(m["residual_within_bin"]) < 1e-12
    assert abs(m["reliability"]) < 1e-12
    cal = calibration_curve(ps, ys, 10)
    assert all(c["ci95"][0] <= c["observed"] <= c["ci95"][1] for c in cal)


def test_paired_tests_are_deterministic():
    d = [0.1 * ((i * 7) % 5 - 2) for i in range(40)]
    assert block_bootstrap_ci(d, 500, 1) == block_bootstrap_ci(d, 500, 1)
    dm = diebold_mariano(d)
    assert 0.0 <= dm["p_value"] <= 1.0


def test_properties_hold_across_seeds(cfg):
    """Robustesse des tests 4, 5 et 6 : ils ne passent pas grâce à une graine favorable."""
    for seed in range(1, 11):
        base = synthetic_observations(60, seed=seed, t0=20724.0, step=0.1)
        ws = [run_forecast(engine_input(), history_from(_blocks(base, k, 6.0)), cfg)["final_output"]["w_displayed"]
              for k in (1, 2, 4, 8)]
        assert all(b >= a - 1e-12 for a, b in zip(ws, ws[1:])), (seed, ws)
        good = synthetic_observations(150, seed=seed)
        bad = synthetic_observations(150, seed=seed + 100, quality=0.1, noise_outcomes=True, t0=20610.0)
        w0 = run_forecast(engine_input(), history_from(good), cfg)["final_output"]["w_displayed"]
        w1 = run_forecast(engine_input(), history_from(good + bad), cfg)["final_output"]["w_displayed"]
        assert w1 <= w0 + 1e-12, seed
        for wm in (False, True):
            names = ["base_rate", "bayes_hierarchical"] + (["market_anchor"] if wm else [])
            h = synthetic_observations(260, seed=seed, with_market=wm)
            a = stack_weights(h, names, "binary|le90", 20730.5, cfg)["class"]
            b = stack_weights(_degrade(h, 60), names, "binary|le90", 20730.5, cfg)["class"]
            assert b["bayes_hierarchical"] < a["bayes_hierarchical"] and b["base_rate"] > a["base_rate"], seed


def test_resolution_scoring_tournament_and_dashboard(tmp_path, llm):
    app = OracleApp(tmp_path, primary=llm)
    for i in range(8):
        r = _full_flow(app, f"Question {i} ?", [DOC_A] if i % 2 else [], market={"p": 0.3 + 0.05 * i})
        app.resolve(r["question"]["question_id"], i % 2, "https://example.org", 0.9)
    upd = _full_flow(app, "Question mise à jour ?", [])
    d = app.start_draft("Question mise à jour ?", now=NOW)
    app.validate(d["draft_id"], None, True)
    app.commit(d["draft_id"], supersedes=upd["forecast_id"], market={"p": 0.8})
    app.resolve(upd["question"]["question_id"], 1, "https://example.org", 0.9)
    scores = app.scores()
    assert {s["score_type"] for s in scores} == {"brier", "log"}
    assert all("PASS" not in str(s) and "FAIL" not in str(s) for s in scores)
    assert any(s["score_frozen_t0"] != s["score_reconciled"] for s in scores if s["question_id"] ==
               upd["question"]["question_id"])
    dash = app.dashboard()
    assert dash["toutes"]["n"] == 9 and dash["toutes"]["log"]["update_value"]["n"] == 1
    tour = app.tournament()
    assert set(tour["mcs"]["mcs"]) <= set(tour["summary"])
    with pytest.raises(Exception):
        app.resolve(upd["question"]["question_id"], 0, "x", 0.5)  # déjà résolue
    assert app.verify()["ok"]


def test_shadow_mode_roundtrip(tmp_path):
    markets = [{"id": "m1", "question": "Will X happen by 2027?", "outcomeType": "BINARY", "probability": 0.7,
                "isResolved": False, "closeTime": 1798761600000, "url": "https://manifold.markets/x"},
               {"id": "m2", "question": "Free response", "outcomeType": "FREE_RESPONSE"}]
    items = normalize_manifold(markets)
    assert len(items) == 1
    app = OracleApp(tmp_path)
    rec = app.shadow_forecast(items[0], now=NOW)
    assert rec["shadow"]["community_p_at_forecast"] == 0.7
    assert all(c["component"] != "market_anchor" for c in rec["models_pipeline"]["component_forecasts"])
    assert app.replay(rec["forecast_id"])["max_abs_diff"] < 1e-9
    resolved = normalize_manifold([dict(markets[0], isResolved=True, resolution="YES")])
    app.shadow_sync(resolved)
    rep = app.shadow_report()
    assert rep["n_resolved"] == 1 and rep["rows"][0]["y"] == 1
    assert normalize_metaculus([{"id": 5, "title": "T", "question": {"type": "binary", "resolution": None}}])
