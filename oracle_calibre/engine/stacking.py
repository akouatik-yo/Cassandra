"""Stacking de distributions prédictives avec prior de Dirichlet.

Les poids maximisent le log-score des prévisions composantes déjà notées
(elles ont été émises avant leur résolution : c'est du hors-échantillon
prospectif), plus un log-prior de Dirichlet :

    max_w  sum_i v_i log( sum_k w_k f_ik ) + sum_k alpha_k log w_k

résolu par EM (pseudo-comptes alpha_k ; mode a posteriori d'une
Dirichlet(alpha + 1)). v_i combine la décroissance exponentielle avec l'âge
et la qualité de la résolution (resolvability_score_post).

Pooling hiérarchique : un niveau global (toutes classes) avec le prior
prudent, puis un niveau par classe (type x horizon) dont le prior est
centré sur les poids globaux (concentration kappa). Une classe vide hérite
du global ; sans aucun historique, on retombe sur le prior prudent, qui met
presque tout le poids sur le composant diffus.
"""
from __future__ import annotations

import math

from .mathx import bernoulli_loglik, clip_p


def em_weights(rows: list[tuple[float, int, list[float]]], alpha: list[float], max_iter: int,
               tol: float) -> tuple[list[float], int]:
    k = len(alpha)
    s_alpha = 0.0
    for a in alpha:
        s_alpha += a
    w = [a / s_alpha for a in alpha]
    total_v = 0.0
    for v, _, _ in rows:
        total_v += v
    it = 0
    for it in range(1, max_iter + 1):
        acc = [0.0] * k
        for v, y, ps in rows:
            f = [clip_p(p) if y == 1 else 1.0 - clip_p(p) for p in ps]
            den = 0.0
            for j in range(k):
                den += w[j] * f[j]
            for j in range(k):
                acc[j] += v * w[j] * f[j] / den
        new = [(acc[j] + alpha[j]) / (total_v + s_alpha) for j in range(k)]
        delta = 0.0
        for j in range(k):
            d = abs(new[j] - w[j])
            if d > delta:
                delta = d
        w = new
        if delta < tol:
            break
    return w, it


def page_hinkley(deltas: list[float], delta: float, threshold: float, min_obs: int) -> int | None:
    """Détecte une hausse de la perte relative ; renvoie l'indice de rupture."""
    cum, mn, mn_idx, run = 0.0, 0.0, 0, 0.0
    for i, d in enumerate(deltas):
        run += d
        m = run / (i + 1)
        cum += d - m - delta
        if cum < mn:
            mn, mn_idx = cum, i + 1
        if i + 1 >= min_obs and cum - mn > threshold:
            return mn_idx
    return None


def _rows(obs: list[dict], names: list[str], t_now: float, cfg: dict) -> list[dict]:
    st = cfg["stacking"]
    out = []
    for o in obs:
        if not all(n in o["components"] for n in names):
            continue
        age = t_now - o["t"]
        if age < 0 or age > st["window_days"]:
            continue
        decay = math.pow(0.5, age / st["half_life_days"])
        out.append({"v": decay * o["quality"], "y": o["y"], "ps": [o["components"][n] for n in names],
                    "t": o["t"], "qid": o["question_id"]})
    return out


def _detect_breaks(rows: list[dict], names: list[str], cfg: dict) -> dict:
    """Détecteur de rupture (Page-Hinkley) par composant informatif, sur la
    perte relative au composant diffus. Renvoie {composant: indice de rupture}."""
    cp = cfg["stacking"]["changepoint"]
    breaks = {}
    base_idx = names.index("base_rate")
    for j, n in enumerate(names):
        if j == base_idx:
            continue
        deltas = [-bernoulli_loglik(r["ps"][j], r["y"]) + bernoulli_loglik(r["ps"][base_idx], r["y"]) for r in rows]
        b = page_hinkley(deltas, cp["delta"], cp["threshold"], cp["min_obs"])
        if b is not None:
            breaks[n] = b
    return breaks


def _fit_level(rows: list[dict], names: list[str], alpha: list[float], cfg: dict) -> tuple[list[float], dict, int]:
    """EM sur la fenêtre, puis retour vers la prudence des composants en rupture.

    Pour un composant k en rupture à l'indice b, on réestime les poids sur
    les seules lignes postérieures à b (même prior) ; son poids devient
    min(poids complet, poids après rupture) et la masse retirée va au
    composant diffus, jamais aux autres sources : une rupture peut signaler
    un changement de régime qui touche aussi les sources corrélées.
    """
    st = cfg["stacking"]
    w, it = em_weights([(r["v"], r["y"], r["ps"]) for r in rows], alpha, st["em_max_iter"], st["em_tol"])
    breaks = _detect_breaks(rows, names, cfg)
    base_idx = names.index("base_rate")
    for n in sorted(breaks):
        j = names.index(n)
        post = rows[breaks[n]:]
        wp, _ = em_weights([(r["v"], r["y"], r["ps"]) for r in post], alpha, st["em_max_iter"], st["em_tol"])
        if wp[j] < w[j]:
            w[base_idx] += w[j] - wp[j]
            w[j] = wp[j]
    return w, breaks, it


def stack_weights(observations: list[dict], names: list[str], class_key: str, t_now: float, cfg: dict,
                  alpha_override: dict | None = None) -> dict:
    alpha_map = alpha_override or cfg["dirichlet"]["alpha"]
    alpha = [alpha_map[n] for n in names]
    rows = _rows(observations, names, t_now, cfg)
    wg, breaks_g, it_g = _fit_level(rows, names, alpha, cfg)
    kappa = cfg["dirichlet"]["class_concentration"]
    alpha_c = [kappa * x for x in wg]
    crow = _rows([o for o in observations if o["class_key"] == class_key], names, t_now, cfg)
    wc, breaks_c, it_c = _fit_level(crow, names, alpha_c, cfg)
    n_eff_g = 0.0
    for r in rows:
        n_eff_g += r["v"]
    n_eff_c = 0.0
    for r in crow:
        n_eff_c += r["v"]
    return {
        "names": names,
        "global": dict(zip(names, wg)),
        "class": dict(zip(names, wc)),
        "n_rows_global": len(rows),
        "n_rows_class": len(crow),
        "n_eff_global": n_eff_g,
        "n_eff_class": n_eff_c,
        "breaks_global": breaks_g,
        "breaks_class": breaks_c,
        "em_iterations": [it_g, it_c],
    }
