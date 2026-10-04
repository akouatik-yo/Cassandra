"""Interfaces HORS MVP : déclarées, non implémentées.

Chaque extension devra passer par le tournoi des méthodes avant de pouvoir
influencer le moteur officiel.
"""
from __future__ import annotations

from typing import Protocol


class NotInMVP(NotImplementedError):
    pass


# --- Types non binaires et leurs scores --------------------------------------
def multiclass_brier(probs: list[float], outcome_index: int) -> float:
    raise NotInMVP("score de Brier multiclasse : type categorical hors MVP")


def multiclass_log(probs: list[float], outcome_index: int) -> float:
    raise NotInMVP("log-score multiclasse : type categorical hors MVP")


def crps_quantile(levels: list[float], values: list[float], outcome: float) -> float:
    raise NotInMVP("CRPS (approximation par quantiles) : type continuous hors MVP")


def survival_score(times: list[float], survival: list[float], event_time: float | None, censored: bool) -> float:
    raise NotInMVP("score de survie (log-vraisemblance censurée) : type time_to_event hors MVP")


# --- Dépendance explicite -----------------------------------------------------
class CopulaModel(Protocol):
    """Réservé aux classes à historique joint volumineux (instable sur petits échantillons)."""

    min_joint_history: int

    def fit(self, joint_outcomes: list[list[int]], joint_forecasts: list[list[float]]) -> None: ...

    def joint_probability(self, marginals: list[float], event: list[int]) -> float: ...


# --- Extrémisation --------------------------------------------------------------
def extremize(p: float, dependency_estimate: float) -> float:
    """Facteur d'extrémisation estimé à partir de la dépendance mesurée entre sources."""
    raise NotInMVP("extrémisation hors MVP")


# --- Couche décisionnelle -----------------------------------------------------
class DecisionLayer(Protocol):
    """Séparée de la prévision : prend p (jamais modifiée) et une utilité fournie par l'utilisateur."""

    def expected_utility(self, p: float, utility_if_yes: dict[str, float],
                         utility_if_no: dict[str, float]) -> dict[str, float]: ...
