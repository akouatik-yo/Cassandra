"""Fonctions numériques pures, portées à l'identique en JavaScript.

On évite volontairement math.lgamma / math.erf : JavaScript n'en dispose
pas, et utiliser deux implémentations différentes casserait le test de
conformité croisée.
"""
from __future__ import annotations

import math

EPS_P = 1e-6

_LANCZOS = [
    0.99999999999980993,
    676.5203681218851,
    -1259.1392167224028,
    771.32342877765313,
    -176.61502916214059,
    12.507343278686905,
    -0.13857109526572012,
    9.9843695780195716e-6,
    1.5056327351493116e-7,
]


def clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def clip_p(p: float, eps: float = EPS_P) -> float:
    return clip(p, eps, 1.0 - eps)


def logit(p: float) -> float:
    p = clip_p(p)
    return math.log(p / (1.0 - p))


def sigmoid(z: float) -> float:
    if z >= 0:
        e = math.exp(-z)
        return 1.0 / (1.0 + e)
    e = math.exp(z)
    return e / (1.0 + e)


def probit_sigmoid(mu: float, var: float) -> float:
    """Approximation de E[sigmoid(mu + e)], e ~ N(0, var) (MacKay)."""
    return sigmoid(mu / math.sqrt(1.0 + math.pi * var / 8.0))


def lgamma(x: float) -> float:
    if x < 0.5:
        return math.log(math.pi / abs(math.sin(math.pi * x))) - lgamma(1.0 - x)
    x -= 1.0
    a = _LANCZOS[0]
    t = x + 7.5
    for i in range(1, 9):
        a += _LANCZOS[i] / (x + i)
    return 0.5 * math.log(2 * math.pi) + (x + 0.5) * math.log(t) - t + math.log(a)


def _betacf(a: float, b: float, x: float) -> float:
    fpmin = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, 301):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        dl = d * c
        h *= dl
        if abs(dl - 1.0) < 1e-15:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Fonction bêta incomplète régularisée I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbt = lgamma(a + b) - lgamma(a) - lgamma(b) + a * math.log(x) + b * math.log(1.0 - x)
    bt = math.exp(lbt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def beta_ppf(q: float, a: float, b: float) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if betainc(a, b, mid) < q:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def erfc(x: float) -> float:
    """erfc par approximation de Tchebychev (erreur relative < 1.2e-7)."""
    z = abs(x)
    t = 1.0 / (1.0 + 0.5 * z)
    r = t * math.exp(
        -z * z - 1.26551223 + t * (1.00002368 + t * (0.37409196 + t * (0.09678418 + t * (-0.18628806 + t * (
            0.27886807 + t * (-1.13520398 + t * (1.48851587 + t * (-0.82215223 + t * 0.17087277)))))))))
    return r if x >= 0 else 2.0 - r


def norm_cdf(x: float) -> float:
    return 0.5 * erfc(-x / math.sqrt(2.0))


def fsum(xs) -> float:
    """Somme séquentielle simple (même ordre d'opérations qu'en JS)."""
    s = 0.0
    for x in xs:
        s += x
    return s


def mean(xs) -> float:
    xs = list(xs)
    return fsum(xs) / len(xs) if xs else 0.0


def solve_linear(a: list[list[float]], b: list[float]) -> list[float]:
    """Élimination de Gauss avec pivot partiel (petits systèmes)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-300:
            raise ZeroDivisionError("système singulier")
        if piv != col:
            m[col], m[piv] = m[piv], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            if f != 0.0:
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = m[r][n]
        for c in range(r + 1, n):
            s -= m[r][c] * x[c]
        x[r] = s / m[r][r]
    return x


def invert(a: list[list[float]]) -> list[list[float]]:
    n = len(a)
    cols = [solve_linear(a, [1.0 if i == j else 0.0 for i in range(n)]) for j in range(n)]
    return [[cols[j][i] for j in range(n)] for i in range(n)]


def bernoulli_loglik(p: float, y: int) -> float:
    p = clip_p(p)
    return math.log(p) if y == 1 else math.log(1.0 - p)
