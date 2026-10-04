"""Score de résolubilité continu (jamais bloquant)."""
from __future__ import annotations


def resolvability_score(stability: float, inter_model: float | None, source_kind: str, class_stats: dict | None,
                        cfg: dict) -> dict:
    """Moyenne géométrique de trois éléments :
    - clarté de la règle (stabilité des reformulations, et accord inter-modèles si disponible) ;
    - stabilité de la source de résolution (selon sa nature) ;
    - fréquence des résolutions non ambiguës (non annulées) dans la classe.
    """
    rc = cfg["resolvability"]
    clarity = stability if inter_model is None else 0.5 * (stability + inter_model)
    src = rc["source_kind_scores"].get(source_kind, rc["source_kind_scores"]["other"])
    a, b = rc["unambiguous_prior"]
    n = class_stats["n"] if class_stats else 0
    canc = class_stats["cancelled"] if class_stats else 0
    unamb = ((n - canc) + a) / (n + a + b)
    prod = max(clarity, 0.0) * src * unamb
    score = prod ** (1.0 / 3.0) if prod > 0 else 0.0
    return {"score": score, "clarity": clarity, "source_stability": src, "unambiguous_frequency": unamb,
            "class_resolutions": n}
