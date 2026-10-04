# Sensibilité du prior de Dirichlet

w affiché (part du poids donnée aux composants informés), moyenne sur 10 historiques synthétiques.
Résolubilité de la question fixée à 1 ; marché présent dans tous les cas.

## Marché informatif

| Réglage | α (diffus / informés) | N = 0 | N = 25 | N = 50 | N = 100 | N = 200 | N = 400 |
|---|---|---|---|---|---|---|---|
| dir-v2-souple | 4 / 0.1 | 0.04 | 0.29 | 0.45 | 0.66 | 0.77 | 0.84 |
| dir-v1-prudent | 8 / 0.25 | 0.05 | 0.16 | 0.31 | 0.55 | 0.70 | 0.80 |
| dir-v1-tres-prudent | 16 / 0.25 | 0.03 | 0.06 | 0.13 | 0.36 | 0.60 | 0.75 |
| dir-v1-souple | 4 / 0.5 | 0.18 | 0.41 | 0.52 | 0.68 | 0.77 | 0.84 |

## Marché non informatif (bruit)

| Réglage | α (diffus / informés) | N = 0 | N = 25 | N = 50 | N = 100 | N = 200 | N = 400 |
|---|---|---|---|---|---|---|---|
| dir-v2-souple | 4 / 0.1 | 0.04 | 0.05 | 0.05 | 0.05 | 0.05 | 0.05 |
| dir-v1-prudent | 8 / 0.25 | 0.05 | 0.06 | 0.06 | 0.06 | 0.06 | 0.06 |
| dir-v1-tres-prudent | 16 / 0.25 | 0.03 | 0.03 | 0.03 | 0.03 | 0.03 | 0.03 |
| dir-v1-souple | 4 / 0.5 | 0.18 | 0.19 | 0.19 | 0.19 | 0.19 | 0.18 |

