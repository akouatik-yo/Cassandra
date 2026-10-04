"""Réconciliation logique entre questions liées.

Contraintes dures : projection du vecteur de probabilités sur l'ensemble
cohérent (enveloppe convexe des mondes possibles compatibles avec les
contraintes) en minimisant la divergence de Bregman de la règle de score :
KL binaire pour le log-score, distance euclidienne au carré pour Brier.
D'après Predd et al. (2009), la projection ne dégrade le score dans aucun
état du monde. Contraintes molles : simple pénalité quadratique.

Résolution par Frank-Wolfe « par paires » sur les poids des mondes
(convergence linéaire sur un polytope), déterministe.
"""
from __future__ import annotations

import math


def _div_grad(x: float, q: float, rule: str, eps: float) -> float:
    if rule == "brier":
        return 2.0 * (x - q)
    x = min(max(x, eps), 1.0 - eps)
    q = min(max(q, eps), 1.0 - eps)
    return math.log(x / q) - math.log((1.0 - x) / (1.0 - q))


def _div(x: float, q: float, rule: str, eps: float) -> float:
    if rule == "brier":
        return (x - q) * (x - q)
    x = min(max(x, eps), 1.0 - eps)
    q = min(max(q, eps), 1.0 - eps)
    return x * math.log(x / q) + (1.0 - x) * math.log((1.0 - x) / (1.0 - q))


def _world_ok(bits: list[int], hard: list[dict]) -> bool:
    for c in hard:
        if c["relation"] == "implies" and bits[c["a"]] == 1 and bits[c["b"]] == 0:
            return False
        if c["relation"] == "exclusive" and bits[c["a"]] == 1 and bits[c["b"]] == 1:
            return False
        if c["relation"] == "partition":
            s = 0
            for i in c["members"]:
                s += bits[i]
            if s != 1:
                return False
    return True


def violation(x: list[float], c: dict) -> float:
    if c["relation"] == "implies":
        return max(0.0, x[c["a"]] - x[c["b"]])
    if c["relation"] == "exclusive":
        return max(0.0, x[c["a"]] + x[c["b"]] - 1.0)
    s = 0.0
    for i in c["members"]:
        s += x[i]
    return abs(s - 1.0)


def _soft_grad(x: list[float], soft: list[dict], mu: float) -> list[float]:
    g = [0.0] * len(x)
    for c in soft:
        if c["relation"] == "implies":
            v = x[c["a"]] - x[c["b"]]
            if v > 0:
                g[c["a"]] += 2 * mu * v
                g[c["b"]] -= 2 * mu * v
        elif c["relation"] == "exclusive":
            v = x[c["a"]] + x[c["b"]] - 1.0
            if v > 0:
                g[c["a"]] += 2 * mu * v
                g[c["b"]] += 2 * mu * v
        else:
            v = -1.0
            for i in c["members"]:
                v += x[i]
            for i in c["members"]:
                g[i] += 2 * mu * v
    return g


def _objective(x: list[float], q: list[float], soft: list[dict], rule: str, mu: float, eps: float) -> float:
    f = 0.0
    for i in range(len(x)):
        f += _div(x[i], q[i], rule, eps)
    for c in soft:
        v = violation(x, c)
        f += mu * v * v
    return f


def reconcile(q: list[float], constraints: list[dict], cfg: dict) -> dict:
    """q : probabilités brutes ; constraints : [{relation, a, b | members, hard}]."""
    rc = cfg["reconciliation"]
    rule, mu, eps = rc["scoring_rule"], rc["soft_penalty"], rc["eps"]
    n = len(q)
    hard = [c for c in constraints if c["hard"]]
    soft = [c for c in constraints if not c["hard"]]
    before = [violation(q, c) for c in constraints]
    if not constraints:
        return {"x": list(q), "iterations": 0, "gap": 0.0, "violations_before": [], "violations_after": [],
                "method": "none", "consistent": True}
    worlds = []
    for mask in range(1 << n):
        bits = [(mask >> i) & 1 for i in range(n)]
        if _world_ok(bits, hard):
            worlds.append(bits)
    consistent = True
    if not worlds:
        consistent = False
        worlds = [[(mask >> i) & 1 for i in range(n)] for mask in range(1 << n)]
    lam = {w: 1.0 / len(worlds) for w in range(len(worlds))}
    x = [0.0] * n
    for w, l in lam.items():
        for i in range(n):
            x[i] += l * worlds[w][i]

    def grad(xv: list[float]) -> list[float]:
        g = _soft_grad(xv, soft, mu)
        for i in range(n):
            g[i] += _div_grad(xv[i], q[i], rule, eps)
        return g

    it, gap = 0, 0.0
    for it in range(1, rc["max_iter"] + 1):
        g = grad(x)
        best_s, best_val = 0, None
        for w in range(len(worlds)):
            val = 0.0
            for i in range(n):
                val += g[i] * worlds[w][i]
            if best_val is None or val < best_val:
                best_s, best_val = w, val
        gx = 0.0
        for i in range(n):
            gx += g[i] * x[i]
        gap = gx - best_val
        if gap < rc["tol"]:
            break
        away, away_val = None, None
        for w in sorted(lam):
            val = 0.0
            for i in range(n):
                val += g[i] * worlds[w][i]
            if away_val is None or val > away_val:
                away, away_val = w, val
        if away == best_s:
            break
        d = [worlds[best_s][i] - worlds[away][i] for i in range(n)]
        gmax = lam[away]

        def dphi(gam: float) -> float:
            xx = [x[i] + gam * d[i] for i in range(n)]
            gg = grad(xx)
            s = 0.0
            for i in range(n):
                s += gg[i] * d[i]
            return s

        if dphi(gmax) <= 0.0:
            gam = gmax
        else:
            lo, hi = 0.0, gmax
            for _ in range(100):
                mid = 0.5 * (lo + hi)
                if dphi(mid) > 0.0:
                    hi = mid
                else:
                    lo = mid
            gam = 0.5 * (lo + hi)
        if gam <= 0.0:
            break
        lam[best_s] = lam.get(best_s, 0.0) + gam
        if gam >= gmax:
            del lam[away]
        else:
            lam[away] -= gam
        x = [0.0] * n
        for w in sorted(lam):
            for i in range(n):
                x[i] += lam[w] * worlds[w][i]
    return {
        "x": x,
        "iterations": it,
        "gap": gap,
        "objective": _objective(x, q, soft, rule, mu, eps),
        "violations_before": before,
        "violations_after": [violation(x, c) for c in constraints],
        "method": f"bregman_{'kl' if rule == 'log' else 'euclid'}_pairwise_frank_wolfe",
        "consistent": consistent,
    }
