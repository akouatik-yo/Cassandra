"""Point d'entrée du moteur : une fonction pure, déterministe, sans appel LLM.

run_forecast(inp, history, cfg) ne lit que ses arguments : mêmes entrées,
même historique, même configuration => mêmes octets en sortie.
"""
from __future__ import annotations

from ..schema import horizon_bucket, iso_duration_days
from .base_rate import base_rate
from .components import bayes_component, evidence_features, fit_evidence_betas, fuse_lineage, market_component
from .history import to_days
from .reconcile import reconcile
from .resolvability import resolvability_score
from .stacking import stack_weights
from .uncertainty import extraction_error_rates, systematic_bias_variance

DEPENDENCY_ASSUMPTIONS = (
    "Sources fusionnées par primary_ancestor_id ; ancêtres distincts supposés conditionnellement "
    "indépendants ; biais commun non mesuré représenté par une variance additive sur le logit, "
    "plus grande quand les ancêtres distincts sont peu nombreux ; sources derived_from_tool_output exclues ; "
    "pas de copule (hors MVP)."
)


def _constraints(inp: dict) -> tuple[list[float], list[dict], list[str]]:
    linked = inp.get("linked") or []
    ids = [inp["question"]["question_id"]] + [l["question_id"] for l in linked]
    index = {qid: i for i, qid in enumerate(ids)}
    q_vals = [l["p"] for l in linked]
    cons: list[dict] = []
    partition_hard: list[int] = []
    partition_soft: list[int] = []
    for l in linked:
        j = index[l["question_id"]]
        rel, hard = l["relation"], bool(l["hard"])
        if rel == "implies":
            cons.append({"relation": "implies", "a": 0, "b": j, "hard": hard})
        elif rel == "implied_by":
            cons.append({"relation": "implies", "a": j, "b": 0, "hard": hard})
        elif rel == "exclusive":
            cons.append({"relation": "exclusive", "a": 0, "b": j, "hard": hard})
        elif rel == "partition_member":
            (partition_hard if hard else partition_soft).append(j)
    if partition_hard:
        cons.append({"relation": "partition", "members": [0] + partition_hard, "hard": True})
    if partition_soft:
        cons.append({"relation": "partition", "members": [0] + partition_soft, "hard": False})
    for c in inp.get("linked_constraints") or []:
        if c["a_id"] in index and c["b_id"] in index:
            rel = c["relation"]
            a, b = index[c["a_id"]], index[c["b_id"]]
            if rel == "implied_by":
                rel, a, b = "implies", b, a
            if rel in ("implies", "exclusive"):
                cons.append({"relation": rel, "a": a, "b": b, "hard": bool(c["hard"])})
    return q_vals, cons, ids


def run_forecast(inp: dict, history: dict, cfg: dict) -> dict:
    q = inp["question"]
    if q["question_type"] != "binary":
        raise NotImplementedError("MVP : seul le type binary est prévu ; voir oracle_calibre/extensions.")
    t = to_days(inp["as_of"])
    hd = iso_duration_days(q["horizon"])
    bucket = horizon_bucket(hd, cfg["horizon_buckets_days"])
    ck = f"{q['question_type']}|{bucket}"
    domain = q.get("domain") or "general"
    obs = [o for o in history["observations"] if o["t"] <= t]

    rates = extraction_error_rates(inp.get("annotations") or [], cfg)
    fusion = fuse_lineage(inp.get("lineage") or [], t, to_days)
    br = base_rate(history["reference"], q["question_type"], bucket, domain, t, cfg)
    betas = fit_evidence_betas(obs, cfg)
    x, detail = evidence_features(fusion["fused"], rates, cfg["bayes"]["evidence_types"])
    bias = systematic_bias_variance(obs, domain, fusion["n_ancestors"], cfg)
    bayes = bayes_component(br["p"], x, detail, betas, bias["total"])
    market = market_component(inp.get("market"), cfg)

    comps = [("base_rate", br["p_diffuse"], br["variance"]), ("bayes_hierarchical", bayes["p"], bayes["variance"])]
    if market is not None:
        comps.append(("market_anchor", market["p"], market["variance"]))
    names = [c[0] for c in comps]
    sw = stack_weights(obs, names, ck, t, cfg)

    res = resolvability_score(q.get("resolution_stability_score", 0.0), q.get("resolution_inter_model_agreement"),
                              q.get("source_kind", "other"), history["resolution_stats"].get(ck), cfg)
    r = res["score"]
    # Un seul rétrécissement : la résolubilité réduit les poids informatifs
    # appris pour la classe, la masse retirée va au composant diffus.
    eff = {}
    inf_total = 0.0
    for n in names:
        if n != "base_rate":
            eff[n] = sw["class"][n] * r
            inf_total += eff[n]
    eff["base_rate"] = 1.0 - inf_total
    p_raw = 0.0
    for n, p, _ in comps:
        p_raw += eff[n] * p
    w_displayed = 1.0 - eff["base_rate"]

    q_link, cons, ids = _constraints(inp)
    rec = reconcile([p_raw] + q_link, cons, cfg)
    p_rec = rec["x"][0]

    second = 0.0
    for n, p, v in comps:
        second += eff[n] * (v + p * p)
    var = second - p_raw * p_raw
    if var < 1e-12:
        var = 1e-12
    kappa = p_raw * (1.0 - p_raw) / var - 1.0
    if kappa < 0.5:
        kappa = 0.5
    alpha_b, beta_b = p_rec * kappa, (1.0 - p_rec) * kappa

    contribs = sorted(bayes["contributions"], key=lambda c: (-c["logit_contribution"], c["primary_ancestor_id"]))
    bullish = [c for c in contribs if c["logit_contribution"] > 0]
    bearish = [c for c in reversed(contribs) if c["logit_contribution"] < 0]

    models_pipeline = {
        "base_rate": {"reference_class": br["reference_class"], "p": br["p"], "p_diffuse": br["p_diffuse"],
                      "n": br["n"], "levels": br["levels"]},
        "component_forecasts": [{"component": n, "p": p} for n, p, _ in comps],
        "stacking_weights": {n: eff[n] for n in names},
        "stacking_weights_class": sw["class"],
        "stacking_weights_global": sw["global"],
        "stacking_diagnostics": {k: sw[k] for k in ("n_rows_global", "n_rows_class", "n_eff_global", "n_eff_class",
                                                    "breaks_global", "breaks_class", "em_iterations")},
        "dirichlet_hyperparameter_id": cfg["dirichlet"]["id"],
        "systematic_bias_variance": bias["total"],
        "systematic_bias_detail": bias,
        "dependency_assumptions": DEPENDENCY_ASSUMPTIONS,
        "class_key": ck,
        "domain": domain,
        "evidence_features": x,
        "evidence_betas": {"beta": betas["beta"], "mu": betas["mu"], "n_obs": betas["n_obs"]},
        "bayes_detail": {k: bayes[k] for k in ("mu_logit", "var_param", "var_extraction", "var_bias", "var_total")},
        "lineage_fusion": {"n_ancestors": fusion["n_ancestors"], "excluded": fusion["excluded"]},
        "resolvability_detail": res,
        "reconciliation": {k: rec[k] for k in ("method", "iterations", "gap", "violations_before",
                                               "violations_after", "consistent")},
        "reconciliation_vector": {"ids": ids, "raw": [p_raw] + q_link, "reconciled": rec["x"]},
    }
    final_output = {
        "p_raw": p_raw,
        "p_reconciled": p_rec,
        "predictive_distribution": {"type": "bernoulli", "p": p_rec},
        "epistemic_uncertainty": {"type": "beta", "alpha": alpha_b, "beta": beta_b, "variance": var},
        "w_displayed": w_displayed,
        "self_negating_risk": bool(q.get("self_negating_risk", False)),
        "factors": {"bullish": bullish[:5], "bearish": bearish[:5]},
    }
    return {"models_pipeline": models_pipeline, "final_output": final_output, "extraction_error_rates": rates,
            "resolvability_score": r, "class_key": ck}
