"""Analyse de sensibilité préenregistrée du prior de Dirichlet.

    python scripts/sensitivity.py > docs/sensibilite_dirichlet.md

Pour chaque réglage (principal + grille de sensibilité de la config), on mesure
le poids informatif w atteint après N résolutions, sur des historiques
synthétiques où le composant marché est réellement informatif, et sur des
historiques où il ne l'est pas (marché = bruit). Moyenne sur 10 graines.
Données synthétiques : elles décrivent le comportement du mécanisme, pas la
qualité prédictive réelle de l'outil.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from conftest import engine_input, history_from, synthetic_observations  # noqa: E402
from oracle_calibre.canonical import sha256_hex  # noqa: E402
from oracle_calibre.config import load_config  # noqa: E402
from oracle_calibre.engine import run_forecast  # noqa: E402

NS = (0, 25, 50, 100, 200, 400)
SEEDS = range(1, 11)


def observations(n: int, seed: int, informative: bool) -> list[dict]:
    obs = synthetic_observations(n, seed=seed, with_market=True, t0=20730.0, step=-0.05)
    if not informative:
        for o in obs:  # le marché ne sait rien : on le remplace par du bruit autour de 1/2
            o["components"]["market_anchor"] = 0.3 + 0.4 * (int(sha256_hex(o["question_id"])[:6], 16) / 16**6)
            o["components"]["bayes_hierarchical"] = 0.5
    return obs


def w_after(cfg: dict, n: int, informative: bool) -> float:
    total = 0.0
    for seed in SEEDS:
        h = history_from(observations(n, seed, informative))
        total += run_forecast(engine_input(market={"p": 0.7}), h, cfg)["final_output"]["w_displayed"]
    return total / len(SEEDS)


def main() -> None:
    base = load_config()
    settings = [{"id": base["dirichlet"]["id"], "alpha": base["dirichlet"]["alpha"]}] + base["dirichlet"]["sensitivity_grid"]
    print("# Sensibilité du prior de Dirichlet\n")
    print("w affiché (part du poids donnée aux composants informés), moyenne sur 10 historiques synthétiques.")
    print("Résolubilité de la question fixée à 1 ; marché présent dans tous les cas.\n")
    for informative in (True, False):
        print(f"## Marché {'informatif' if informative else 'non informatif (bruit)'}\n")
        print("| Réglage | α (diffus / informés) | " + " | ".join(f"N = {n}" for n in NS) + " |")
        print("|---|---|" + "---|" * len(NS))
        for s in settings:
            cfg = copy.deepcopy(base)
            cfg["dirichlet"]["alpha"] = s["alpha"]
            row = [f"{w_after(cfg, n, informative):.2f}" for n in NS]
            a = s["alpha"]
            print(f"| {s['id']} | {a['base_rate']:g} / {a['bayes_hierarchical']:g} | " + " | ".join(row) + " |")
        print()


if __name__ == "__main__":
    main()
