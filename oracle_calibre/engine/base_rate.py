"""Taux de base par classe de référence, avec rétrécissement hiérarchique.

Trois niveaux : global -> (type x horizon) -> (type x horizon x domaine).
Chaque niveau est rétréci vers le niveau supérieur avec un nombre fixe de
pseudo-observations (config). Seules les issues connues avant l'instant de
prévision sont utilisées : l'estimation est hors échantillon par construction.
"""
from __future__ import annotations


def base_rate(reference: list[dict], question_type: str, bucket: str, domain: str, as_of_days: float,
              cfg: dict) -> dict:
    a0, b0 = cfg["base_rate"]["global_prior"]
    m = cfg["base_rate"]["level_pseudo_obs"]
    w0 = cfg["base_rate"]["widening_pseudo_obs"]
    items = [r for r in reference if r["t"] <= as_of_days and r["question_type"] == question_type]
    n_g = len(items)
    s_g = 0
    for r in items:
        s_g += r["y"]
    g = (s_g + a0) / (n_g + a0 + b0)
    lvl1 = [r for r in items if r["bucket"] == bucket]
    s1 = 0
    for r in lvl1:
        s1 += r["y"]
    r1 = (s1 + m * g) / (len(lvl1) + m)
    lvl2 = [r for r in lvl1 if r["domain"] == domain]
    s2 = 0
    for r in lvl2:
        s2 += r["y"]
    r2 = (s2 + m * r1) / (len(lvl2) + m)
    conc = len(lvl2) + m
    # Composant diffus : taux de base élargi (rétréci vers 1/2).
    p_diffuse = (r2 * conc + 0.5 * w0) / (conc + w0)
    var = p_diffuse * (1.0 - p_diffuse) / (conc + w0 + 1.0)
    return {
        "reference_class": f"{question_type}|{bucket}|{domain}",
        "p": r2,
        "p_diffuse": p_diffuse,
        "variance": var,
        "n": {"global": n_g, "type_horizon": len(lvl1), "domain": len(lvl2)},
        "levels": {"global": g, "type_horizon": r1, "domain": r2},
    }
