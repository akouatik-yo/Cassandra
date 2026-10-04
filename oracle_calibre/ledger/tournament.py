"""Tournoi des méthodes et Model Confidence Set (Hansen, Lunde & Nason, 2011).

Toutes les méthodes sont recalculées à partir des composants stockés dans
chaque ForecastRecord : elles tournent donc « en parallèle » sur exactement
les mêmes questions, prévues aux mêmes instants. Le moteur officiel n'est
désigné que s'il appartient au MCS ; sinon le tournoi le signale.
"""
from __future__ import annotations

import math

from ..canonical import Mulberry32
from .scoring import block_length, resolved_chains, score

METHODS = ("base_rate_seul", "moyenne_simple", "bayes_hierarchique", "stack_brut", "stack_officiel")


def method_forecasts(rec: dict) -> dict:
    comps = {c["component"]: c["p"] for c in rec["models_pipeline"]["component_forecasts"]}
    s = 0.0
    for c in rec["models_pipeline"]["component_forecasts"]:
        s += c["p"]
    out = {
        "base_rate_seul": comps["base_rate"],
        "moyenne_simple": s / len(comps),
        "bayes_hierarchique": comps["bayes_hierarchical"],
        "stack_brut": rec["final_output"]["p_raw"],
        "stack_officiel": rec["final_output"]["p_reconciled"],
    }
    if "market_anchor" in comps:
        out["marche_seul"] = comps["market_anchor"]
    return out


def _bootstrap_indices(n: int, reps: int, seed: int) -> list[list[int]]:
    L = block_length(n)
    rng = Mulberry32(seed)
    out = []
    for _ in range(reps):
        idx = []
        while len(idx) < n:
            start = rng.randint(n)
            for j in range(L):
                if len(idx) >= n:
                    break
                idx.append((start + j) % n)
        out.append(idx)
    return out


def model_confidence_set(losses: dict[str, list[float]], alpha: float, reps: int, seed: int) -> dict:
    """MCS avec statistique T_max et bootstrap par blocs circulaires."""
    names = sorted(losses)
    n = len(losses[names[0]]) if names else 0
    if n < 5 or len(names) < 2:
        return {"mcs": names, "eliminated": [], "note": "trop peu de questions résolues pour un MCS (n < 5)"}
    idx = _bootstrap_indices(n, reps, seed)
    alive = list(names)
    eliminated = []
    while len(alive) > 1:
        m = len(alive)
        # d_i = L_i - moyenne des L_j (j vivant), par question
        d = {}
        for a in alive:
            row = []
            for t in range(n):
                avg = 0.0
                for b in alive:
                    avg += losses[b][t]
                row.append(losses[a][t] - avg / m)
            d[a] = row
        dbar = {}
        for a in alive:
            s = 0.0
            for v in d[a]:
                s += v
            dbar[a] = s / n
        boot = {a: [] for a in alive}
        for ix in idx:
            for a in alive:
                s = 0.0
                for t in ix:
                    s += d[a][t]
                boot[a].append(s / n)
        tstat, se = {}, {}
        for a in alive:
            var = 0.0
            for v in boot[a]:
                var += (v - dbar[a]) * (v - dbar[a])
            var /= reps
            se[a] = math.sqrt(var) if var > 0 else 1.0
            tstat[a] = dbar[a] / se[a]
        tmax = max(tstat[a] for a in alive)
        count = 0
        for r in range(reps):
            tb = None
            for a in alive:
                v = (boot[a][r] - dbar[a]) / se[a]
                if tb is None or v > tb:
                    tb = v
            if tb >= tmax:
                count += 1
        pval = count / reps
        if pval >= alpha:
            break
        worst = sorted(alive, key=lambda a: (-tstat[a], a))[0]
        eliminated.append({"method": worst, "p_value": pval, "t_stat": tstat[worst]})
        alive.remove(worst)
    return {"mcs": alive, "eliminated": eliminated, "alpha": alpha, "n": n}


def tournament(entries: list[dict], cfg: dict, score_type: str = "log") -> dict:
    ev = cfg["evaluation"]
    chains = resolved_chains(entries)
    losses: dict[str, list[float]] = {m: [] for m in METHODS}
    market_pairs = []
    for ch in chains:
        mf = method_forecasts(ch["chain"][-1])
        for m in METHODS:
            losses[m].append(score(mf[m], ch["y"], score_type))
        if "marche_seul" in mf:
            market_pairs.append(score(mf["marche_seul"], ch["y"], score_type)
                                - score(mf["stack_officiel"], ch["y"], score_type))
    summary = {}
    for m in METHODS:
        s = 0.0
        for v in losses[m]:
            s += v
        summary[m] = {"mean_loss": s / len(losses[m]) if losses[m] else None, "n": len(losses[m])}
    mcs = model_confidence_set(losses, ev["mcs_alpha"], ev["bootstrap_reps"], ev["seed"]) if chains else \
        {"mcs": list(METHODS), "eliminated": [], "note": "aucune question résolue"}
    official_in = "stack_officiel" in mcs["mcs"]
    ms = 0.0
    for v in market_pairs:
        ms += v
    return {"score_type": score_type, "summary": summary, "mcs": mcs,
            "official_engine": "stack_officiel" if official_in else None,
            "official_engine_note": ("stack_officiel appartient au MCS" if official_in else
                                     "stack_officiel exclu du MCS : il ne doit pas rester moteur officiel"),
            "market_subset": {"n": len(market_pairs),
                              "mean_diff_market_minus_official": ms / len(market_pairs) if market_pairs else None}}
