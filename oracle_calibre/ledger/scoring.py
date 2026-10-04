"""Notation et évaluation (ScoreRecord dérivé, recalculable, hors registre figé).

Conventions : les scores sont des pertes (plus bas = meilleur).
  brier = (p - y)^2 ; log = -ln p(y).
Aucun statut PASS/FAIL : on publie des statistiques et des intervalles.
Les comparaisons au taux de base sont des différences APPARIÉES question
par question, avec bootstrap par blocs temporels et test de Diebold-Mariano ;
le chevauchement d'intervalles n'est jamais utilisé comme test.
"""
from __future__ import annotations

import math

from ..canonical import Mulberry32
from ..engine.mathx import beta_ppf, clip_p, norm_cdf

SCORE_TYPES = ("brier", "log")


def brier(p: float, y: int) -> float:
    d = p - y
    return d * d


def log_loss(p: float, y: int) -> float:
    p = clip_p(p)
    return -math.log(p) if y == 1 else -math.log(1.0 - p)


def score(p: float, y: int, score_type: str) -> float:
    return brier(p, y) if score_type == "brier" else log_loss(p, y)


def block_length(n: int) -> int:
    return max(1, int(math.floor(math.pow(n, 1.0 / 3.0) + 0.5)))


def block_bootstrap_ci(x: list[float], reps: int, seed: int, level: float = 0.95) -> list[float] | None:
    """IC percentile de la moyenne par bootstrap circulaire par blocs (ordre temporel)."""
    n = len(x)
    if n < 2:
        return None
    L = block_length(n)
    rng = Mulberry32(seed)
    means = []
    for _ in range(reps):
        s, k = 0.0, 0
        while k < n:
            start = rng.randint(n)
            for j in range(L):
                if k >= n:
                    break
                s += x[(start + j) % n]
                k += 1
        means.append(s / n)
    means.sort()
    lo = means[int(math.floor((1 - level) / 2 * reps))]
    hi = means[min(reps - 1, int(math.floor((1 + level) / 2 * reps)))]
    return [lo, hi]


def diebold_mariano(d: list[float]) -> dict | None:
    """Test DM sur la série des différences de pertes (variance HAC de Newey-West)."""
    n = len(d)
    if n < 3:
        return None
    m = 0.0
    for v in d:
        m += v
    m /= n
    lag = block_length(n)
    gamma0 = 0.0
    for v in d:
        gamma0 += (v - m) * (v - m)
    gamma0 /= n
    lrv = gamma0
    for k in range(1, lag + 1):
        g = 0.0
        for t in range(k, n):
            g += (d[t] - m) * (d[t - k] - m)
        g /= n
        lrv += 2.0 * (1.0 - k / (lag + 1.0)) * g
    if lrv <= 0:
        return {"statistic": 0.0, "p_value": 1.0, "lag": lag}
    stat = m / math.sqrt(lrv / n)
    p = 2.0 * (1.0 - norm_cdf(abs(stat)))
    return {"statistic": stat, "p_value": p, "lag": lag}


def murphy_decomposition(ps: list[float], ys: list[int], bins: int) -> dict:
    n = len(ps)
    if n == 0:
        return {"n": 0}
    obar = 0.0
    for y in ys:
        obar += y
    obar /= n
    groups: dict[int, list[int]] = {}
    for i, p in enumerate(ps):
        b = min(bins - 1, int(p * bins))
        groups.setdefault(b, []).append(i)
    rel, res = 0.0, 0.0
    for b in sorted(groups):
        idx = groups[b]
        fk, ok = 0.0, 0.0
        for i in idx:
            fk += ps[i]
            ok += ys[i]
        fk /= len(idx)
        ok /= len(idx)
        rel += len(idx) * (fk - ok) * (fk - ok)
        res += len(idx) * (ok - obar) * (ok - obar)
    rel /= n
    res /= n
    unc = obar * (1.0 - obar)
    bs = 0.0
    for i in range(n):
        bs += brier(ps[i], ys[i])
    bs /= n
    return {"n": n, "brier": bs, "reliability": rel, "resolution": res, "uncertainty": unc,
            "residual_within_bin": bs - (rel - res + unc)}


def calibration_curve(ps: list[float], ys: list[int], bins: int) -> list[dict]:
    """Par classe de probabilité : fréquence observée et IC de Jeffreys à 95 %."""
    groups: dict[int, list[int]] = {}
    for i, p in enumerate(ps):
        groups.setdefault(min(bins - 1, int(p * bins)), []).append(i)
    out = []
    for b in sorted(groups):
        idx = groups[b]
        k = 0
        mp = 0.0
        for i in idx:
            k += ys[i]
            mp += ps[i]
        n = len(idx)
        out.append({"bin": [b / bins, (b + 1) / bins], "n": n, "mean_forecast": mp / n, "observed": k / n,
                    "ci95": [beta_ppf(0.025, k + 0.5, n - k + 0.5), beta_ppf(0.975, k + 0.5, n - k + 0.5)]})
    return out


def resolved_chains(entries: list[dict]) -> list[dict]:
    """Questions résolues (non annulées), dans l'ordre de résolution, avec leur chaîne de prévisions."""
    forecasts: dict[str, list[dict]] = {}
    out = []
    for e in entries:
        if e["record_type"] == "forecast":
            forecasts.setdefault(e["record"]["question"]["question_id"], []).append(e["record"])
        elif e["record_type"] == "resolution":
            r = e["record"]
            if r["outcome"] == "cancelled" or r["question_id"] not in forecasts:
                continue
            chain = forecasts[r["question_id"]]
            if chain[0]["question"]["question_type"] != "binary":
                continue
            out.append({"question_id": r["question_id"], "y": int(r["outcome"]), "chain": chain,
                        "class": chain[0]["models_pipeline"].get("class_key", "binary|?"),
                        "resolution": r})
    return out


def _base_p(rec: dict) -> float:
    for c in rec["models_pipeline"]["component_forecasts"]:
        if c["component"] == "base_rate":
            return c["p"]
    return rec["models_pipeline"]["base_rate"]["p"]


def score_records(entries: list[dict], cfg: dict) -> list[dict]:
    ev = cfg["evaluation"]
    chains = resolved_chains(entries)
    class_diffs: dict[tuple[str, str], list[float]] = {}
    for st in SCORE_TYPES:
        for ch in chains:
            last = ch["chain"][-1]
            d = score(last["final_output"]["p_reconciled"], ch["y"], st) - score(_base_p(last), ch["y"], st)
            class_diffs.setdefault((ch["class"], st), []).append(d)
    cis = {k: block_bootstrap_ci(v, ev["bootstrap_reps"], ev["seed"]) for k, v in class_diffs.items()}
    out = []
    for ch in chains:
        t0 = ch["chain"][0]
        for rec in ch["chain"]:
            for st in SCORE_TYPES:
                fo = rec["final_output"]
                s_rec = score(fo["p_reconciled"], ch["y"], st)
                out.append({
                    "forecast_id": rec["forecast_id"],
                    "question_id": ch["question_id"],
                    "score_type": st,
                    "score_raw": score(fo["p_raw"], ch["y"], st),
                    "score_reconciled": s_rec,
                    "score_frozen_t0": score(t0["final_output"]["p_reconciled"], ch["y"], st),
                    "class": {"question_type": "binary", "horizon_bucket": ch["class"].split("|")[1]},
                    "vs_base_rate_paired_diff": {
                        "value": s_rec - score(_base_p(rec), ch["y"], st),
                        "ci95": cis.get((ch["class"], st)),
                        "method": "block_bootstrap_circulaire (différence moyenne de la classe, dernière prévision)",
                    },
                })
    return out


def dashboard(entries: list[dict], cfg: dict) -> dict:
    ev = cfg["evaluation"]
    chains = resolved_chains(entries)
    by_class: dict[str, list[dict]] = {}
    for ch in chains:
        by_class.setdefault(ch["class"], []).append(ch)
    by_class["toutes"] = chains
    out = {}
    for ck in sorted(by_class):
        chs = by_class[ck]
        ps = [c["chain"][-1]["final_output"]["p_reconciled"] for c in chs]
        ys = [c["y"] for c in chs]
        block: dict = {"n": len(chs), "murphy": murphy_decomposition(ps, ys, ev["calibration_bins"]),
                       "calibration": calibration_curve(ps, ys, ev["calibration_bins"])}
        for st in SCORE_TYPES:
            diffs = [score(ps[i], ys[i], st) - score(_base_p(chs[i]["chain"][-1]), ys[i], st) for i in range(len(chs))]
            mean_s = 0.0
            for i in range(len(chs)):
                mean_s += score(ps[i], ys[i], st)
            upd = [score(c["chain"][-1]["final_output"]["p_reconciled"], c["y"], st)
                   - score(c["chain"][0]["final_output"]["p_reconciled"], c["y"], st)
                   for c in chs if len(c["chain"]) > 1]
            md = 0.0
            for v in diffs:
                md += v
            mu = 0.0
            for v in upd:
                mu += v
            block[st] = {
                "mean_score": mean_s / len(chs) if chs else None,
                "vs_base_rate": {"mean_diff": md / len(diffs) if diffs else None,
                                 "ci95": block_bootstrap_ci(diffs, ev["bootstrap_reps"], ev["seed"]),
                                 "diebold_mariano": diebold_mariano(diffs)},
                "update_value": {"n": len(upd), "mean_diff_vs_t0": mu / len(upd) if upd else None,
                                 "ci95": block_bootstrap_ci(upd, ev["bootstrap_reps"], ev["seed"])},
            }
        out[ck] = block
    return out
