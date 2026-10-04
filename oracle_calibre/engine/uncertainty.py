"""Incertitude d'extraction et variance « biais systématique »."""
from __future__ import annotations

from .mathx import beta_ppf, sigmoid


def extraction_error_rates(annotations: list[dict], cfg: dict) -> dict:
    """Taux d'erreur par type, avec pooling partiel entre types et IC 95 %.

    annotations : [{"type": "date", "n_checked": 40, "n_errors": 3}, ...]
    (échantillon annoté à la main). Sans annotation, on obtient le prior
    préenregistré avec un intervalle large.
    """
    a, b = cfg["extraction"]["prior_error_rate"]
    m = cfg["extraction"]["pooling_strength"]
    types = cfg["extraction"]["types"]
    tot_n, tot_e = 0, 0
    per = {t: [0, 0] for t in types}
    for ann in annotations or []:
        t = ann["type"]
        if t in per:
            per[t][0] += int(ann["n_checked"])
            per[t][1] += int(ann["n_errors"])
            tot_n += int(ann["n_checked"])
            tot_e += int(ann["n_errors"])
    pooled = (tot_e + a) / (tot_n + a + b)
    out = {}
    for t in types:
        n, e = per[t]
        pa = e + m * pooled
        pb = (n - e) + m * (1.0 - pooled)
        out[t] = {"rate": pa / (pa + pb), "ci95": [beta_ppf(0.025, pa, pb), beta_ppf(0.975, pa, pb)],
                  "n_checked": n, "n_errors": e}
    out["_pooled"] = {"rate": pooled, "n_checked": tot_n, "n_errors": tot_e}
    return out


def evidence_reliability(consistency: float, rates: dict) -> float:
    """Espérance du signe correct d'un indice extrait (entre 0 et 1).

    consistency : accord entre extractions répétées ; l'erreur de relation
    inverse le sens, les autres erreurs annulent l'information.
    """
    m = consistency * (1.0 - 2.0 * rates["relation"]["rate"])
    m *= (1.0 - rates["entity"]["rate"]) * (1.0 - rates["date"]["rate"]) * (1.0 - rates["causality"]["rate"])
    return m


def systematic_bias_variance(observations: list[dict], domain: str, n_ancestors: int, cfg: dict) -> dict:
    """Variance (échelle logit) d'un biais commun non mesuré.

    Si le logit vrai = logit prévu + e, e ~ N(0, s2), alors
    Var(y - p) ~ p(1-p) + (p(1-p))^2 s2. D'où l'estimateur des moments
    s2 = sum[(y-p)^2 - p(1-p)] / sum (p(1-p))^2 sur les résolutions
    journalisées du domaine, rétréci vers le plancher, jamais en dessous.
    Le terme est multiplié par (1 + k / nb d'ancêtres primaires distincts).
    """
    sb = cfg["systematic_bias"]
    floor = sb["floor_logit_var"]
    num, den, n = 0.0, 0.0, 0
    for o in observations:
        if o["domain"] != domain or o.get("mu_logit") is None:
            continue
        p = sigmoid(o["mu_logit"])
        v = p * (1.0 - p)
        d = o["y"] - p
        num += d * d - v
        den += v * v
        n += 1
    raw = num / den if den > 0 else floor
    m = sb["domain_pseudo_obs"]
    shrunk = (n * raw + m * floor) / (n + m)
    domain_var = shrunk if shrunk > floor else floor
    factor = 1.0 + sb["ancestor_k"] / (n_ancestors if n_ancestors > 0 else 1)
    return {"domain": domain, "domain_var": domain_var, "floor": floor, "n_obs": n, "raw_estimate": raw,
            "ancestor_factor": factor, "total": domain_var * factor}
