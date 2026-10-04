"""Fusion du lignage et prévisionnistes composants (bayes_hierarchical, market_anchor)."""
from __future__ import annotations

from .mathx import clip, invert, logit, probit_sigmoid, sigmoid, solve_linear
from .uncertainty import evidence_reliability

DIRECTIONS = {"yes": 1, "no": -1, "neutral": 0}


def fuse_lineage(lineage: list[dict], as_of_days: float, to_days) -> dict:
    """Fusionne les sources par primary_ancestor_id.

    - Les sources derived_from_tool_output sont exclues (boucle réflexive).
    - Les sources postérieures à l'instant de prévision sont exclues (filtre
      de date strict, appliqué une seconde fois ici par défense en profondeur).
    - Plusieurs sources qui partagent un ancêtre ne comptent qu'une fois :
      leur sens est la moyenne pondérée par la self-consistency.
    """
    groups: dict[str, list[dict]] = {}
    excluded = []
    for src in lineage:
        if src.get("derived_from_tool_output"):
            excluded.append({"source_id": src["source_id"], "reason": "derived_from_tool_output"})
            continue
        if to_days(src["timestamp"]) > as_of_days:
            excluded.append({"source_id": src["source_id"], "reason": "posterior_to_forecast"})
            continue
        groups.setdefault(src["primary_ancestor_id"], []).append(src)
    fused = []
    for anc in sorted(groups):
        members = sorted(groups[anc], key=lambda s: s["source_id"])
        num, den = 0.0, 0.0
        types: dict[str, int] = {}
        for s in members:
            c = float(s.get("self_consistency_score", 0.0))
            num += c * DIRECTIONS.get(s.get("direction", "neutral"), 0)
            den += c
            t = s.get("evidence_type") or "other"
            types[t] = types.get(t, 0) + 1
        score = num / den if den > 0 else 0.0
        direction = 1 if score > 0 else -1 if score < 0 else 0
        consistency = den / len(members)
        etype = sorted(types.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        fused.append({
            "primary_ancestor_id": anc,
            "direction": direction,
            "agreement": abs(score),
            "consistency": consistency * abs(score),
            "evidence_type": etype,
            "n_sources": len(members),
            "facts": [s.get("fact", "") for s in members][:3],
        })
    return {"fused": fused, "excluded": excluded, "n_ancestors": len(fused)}


def evidence_features(fused: list[dict], rates: dict, types: list[str]) -> tuple[dict, list[dict]]:
    """x_t = somme sur les indices de type t de direction * fiabilité."""
    x = {t: 0.0 for t in types}
    detail = []
    for ev in fused:
        t = ev["evidence_type"] if ev["evidence_type"] in x else "other"
        m = evidence_reliability(ev["consistency"], rates)
        x[t] += ev["direction"] * m
        detail.append({**ev, "evidence_type": t, "reliability": m})
    return x, detail


def fit_evidence_betas(observations: list[dict], cfg: dict) -> dict:
    """Régression logistique bayésienne hiérarchique (MAP + Laplace).

    y_i ~ Bern(sigmoid(logit(base_i) + sum_t beta_t x_it))
    beta_t ~ N(mu, tau^2) (pooling partiel entre types d'indices)
    mu ~ N(mu0, mu0_sd^2)
    Sans historique, beta_t = mu0 avec l'incertitude du prior.
    """
    bc = cfg["bayes"]
    types = bc["evidence_types"]
    T = len(types)
    tau2, s02, mu0 = bc["tau"] * bc["tau"], bc["mu0_sd"] * bc["mu0_sd"], bc["mu0"]
    rows = []
    for o in observations:
        feats = o.get("features") or {}
        x = [float(feats.get(t, 0.0)) for t in types]
        rows.append((o["quality"], o["y"], logit(o["base_rate_p"]), x))
    theta = [mu0] * T + [mu0]
    hess = None
    for _ in range(bc["newton_max_iter"]):
        mu = theta[T]
        grad = [0.0] * (T + 1)
        hess = [[0.0] * (T + 1) for _ in range(T + 1)]
        for t in range(T):
            grad[t] += (theta[t] - mu) / tau2
            grad[T] -= (theta[t] - mu) / tau2
            hess[t][t] += 1.0 / tau2
            hess[t][T] -= 1.0 / tau2
            hess[T][t] -= 1.0 / tau2
            hess[T][T] += 1.0 / tau2
        grad[T] += (mu - mu0) / s02
        hess[T][T] += 1.0 / s02
        for v, y, off, x in rows:
            z = off
            for t in range(T):
                z += theta[t] * x[t]
            p = sigmoid(z)
            r = v * (p - y)
            wgt = v * p * (1.0 - p)
            for a in range(T):
                if x[a] == 0.0:
                    continue
                grad[a] += r * x[a]
                for b in range(T):
                    if x[b] != 0.0:
                        hess[a][b] += wgt * x[a] * x[b]
        step = solve_linear(hess, grad)
        mx = 0.0
        for i in range(T + 1):
            theta[i] -= step[i]
            if abs(step[i]) > mx:
                mx = abs(step[i])
        if mx < bc["newton_tol"]:
            break
    cov = invert(hess)
    return {"types": types, "beta": dict(zip(types, theta[:T])), "mu": theta[T],
            "cov": [row[:T] for row in cov[:T]], "n_obs": len(rows)}


def bayes_component(base_p: float, x: dict, detail: list[dict], betas: dict, bias_var: float) -> dict:
    types = betas["types"]
    xs = [x[t] for t in types]
    mu_logit = logit(base_p)
    for i, t in enumerate(types):
        mu_logit += betas["beta"][t] * xs[i]
    var_param = 0.0
    for i in range(len(types)):
        for j in range(len(types)):
            var_param += xs[i] * betas["cov"][i][j] * xs[j]
    var_extr = 0.0
    contributions = []
    for ev in detail:
        bt = betas["beta"][ev["evidence_type"]]
        m = ev["reliability"]
        var_extr += bt * bt * (1.0 - m * m) if ev["direction"] != 0 else 0.0
        contributions.append({"primary_ancestor_id": ev["primary_ancestor_id"], "evidence_type": ev["evidence_type"],
                              "direction": ev["direction"], "logit_contribution": bt * ev["direction"] * m,
                              "n_sources": ev["n_sources"], "facts": ev["facts"]})
    var_total = var_param + var_extr + bias_var
    p = probit_sigmoid(mu_logit, var_total)
    v = p * (1.0 - p)
    return {"p": p, "mu_logit": mu_logit, "var_param": var_param, "var_extraction": var_extr,
            "var_bias": bias_var, "var_total": var_total, "variance": v * v * var_total,
            "contributions": contributions}


def market_component(market: dict | None, cfg: dict) -> dict | None:
    if not market or market.get("p") is None:
        return None
    lo, hi = cfg["market"]["clip"]
    p = clip(float(market["p"]), lo, hi)
    v = p * (1.0 - p)
    return {"p": p, "variance": v * v * cfg["market"]["logit_var"], "source": market.get("source", "")}

