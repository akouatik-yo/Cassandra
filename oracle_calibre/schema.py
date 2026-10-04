"""Schéma des trois objets (ForecastRecord, ResolutionRecord, ScoreRecord).

Le MVP ne produit que des questions binaires, mais le schéma accepte déjà
les quatre types de questions et les quatre formes de distribution
prédictive (objet discriminé par le champ "type").
"""
from __future__ import annotations

from typing import Any

QUESTION_TYPES = ("binary", "categorical", "continuous", "time_to_event")
MVP_QUESTION_TYPES = ("binary",)
AMBIGUITY_POLICIES = ("cancel", "freeze_old", "adopt_new")
RELATIONS = ("implies", "implied_by", "exclusive", "partition_member")
COMPONENTS = ("base_rate", "bayes_hierarchical", "market_anchor")
INFORMATIVE_COMPONENTS = ("bayes_hierarchical", "market_anchor")
DISTRIBUTION_TYPES = {
    "bernoulli": ("p",),
    "categorical": ("probs",),
    "quantile": ("levels", "values"),
    "survival": ("times", "survival"),
}
DISTRIBUTION_FOR_QUESTION = {
    "binary": "bernoulli",
    "categorical": "categorical",
    "continuous": "quantile",
    "time_to_event": "survival",
}

# Champs de résolution obligatoires : sans eux, aucune prévision n'est notable.
MANDATORY_RESOLUTION_FIELDS = (
    "question_type",
    "question_definition_version",
    "resolution_rule",
    "resolution_source",
    "resolution_deadline",
    "ambiguity_policy",
)

FORECAST_TOP_FIELDS = (
    "forecast_id", "supersedes_forecast_id", "hash_previous", "timestamp_utc",
    "question", "reproducibility", "information_lineage", "extraction_error_rates",
    "models_pipeline", "final_output",
)
QUESTION_FIELDS = (
    "question_id", "text", "question_type", "question_definition_version", "horizon",
    "resolution_rule", "resolution_source", "resolution_deadline", "ambiguity_policy",
    "resolvability_score", "resolution_stability_score", "resolution_inter_model_agreement",
    "resolution_human_validated", "rule_validation_protocol", "linked_questions", "decomposition",
)
REPRO_FIELDS = (
    "llm_provider", "model_id", "model_version", "prompt_hash", "temperature", "seed_if_supported",
    "llm_snapshots_hash", "retrieval_snapshot_hash", "code_commit", "dependency_lock_hash",
    "statistical_config_version",
)
LINEAGE_FIELDS = ("source_id", "primary_ancestor_id", "timestamp", "method", "derived_from_tool_output",
                  "self_consistency_score")
PIPELINE_FIELDS = ("base_rate", "component_forecasts", "stacking_weights", "dirichlet_hyperparameter_id",
                   "systematic_bias_variance", "dependency_assumptions")
FINAL_FIELDS = ("p_raw", "p_reconciled", "predictive_distribution", "epistemic_uncertainty", "w_displayed",
                "self_negating_risk")
RESOLUTION_FIELDS = (
    "resolution_id", "question_id", "hash_previous", "timestamp_utc", "outcome", "effective_source",
    "definition_version_applied", "ambiguity_policy_applied", "resolvability_score_post",
)
SCORE_FIELDS = ("forecast_id", "score_type", "score_raw", "score_reconciled", "score_frozen_t0", "class",
                "vs_base_rate_paired_diff")


class ValidationError(ValueError):
    """Enregistrement refusé : le message liste les champs fautifs."""


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def missing_resolution_fields(question: dict) -> list[str]:
    return [f for f in MANDATORY_RESOLUTION_FIELDS if _blank(question.get(f))]


def validate_distribution(dist: dict) -> None:
    t = dist.get("type")
    if t not in DISTRIBUTION_TYPES:
        raise ValidationError(f"predictive_distribution.type inconnu : {t!r}")
    for f in DISTRIBUTION_TYPES[t]:
        if f not in dist:
            raise ValidationError(f"predictive_distribution.{f} manquant pour le type {t}")
    if t == "bernoulli" and not (0.0 <= dist["p"] <= 1.0):
        raise ValidationError("p hors de [0, 1]")


def validate_forecast_record(rec: dict) -> None:
    errors = [f for f in FORECAST_TOP_FIELDS if f not in rec]
    q = rec.get("question") or {}
    missing = missing_resolution_fields(q)
    if missing:
        errors.append("champs de résolution obligatoires manquants : " + ", ".join(missing))
    errors += [f"question.{f}" for f in QUESTION_FIELDS if f not in q]
    if q.get("question_type") not in QUESTION_TYPES and "question_type" not in missing:
        errors.append(f"question_type invalide : {q.get('question_type')!r}")
    if q.get("ambiguity_policy") not in AMBIGUITY_POLICIES and "ambiguity_policy" not in missing:
        errors.append(f"ambiguity_policy invalide : {q.get('ambiguity_policy')!r}")
    for link in q.get("linked_questions") or []:
        if link.get("relation") not in RELATIONS or not isinstance(link.get("hard"), bool):
            errors.append(f"lien invalide : {link!r}")
    errors += [f"reproducibility.{f}" for f in REPRO_FIELDS if f not in (rec.get("reproducibility") or {})]
    for i, src in enumerate(rec.get("information_lineage") or []):
        errors += [f"information_lineage[{i}].{f}" for f in LINEAGE_FIELDS if f not in src]
    errors += [f"models_pipeline.{f}" for f in PIPELINE_FIELDS if f not in (rec.get("models_pipeline") or {})]
    fo = rec.get("final_output") or {}
    errors += [f"final_output.{f}" for f in FINAL_FIELDS if f not in fo]
    if errors:
        raise ValidationError("ForecastRecord refusé — " + " ; ".join(errors))
    validate_distribution(fo["predictive_distribution"])
    expected = DISTRIBUTION_FOR_QUESTION[q["question_type"]]
    if fo["predictive_distribution"]["type"] != expected:
        raise ValidationError(f"distribution {fo['predictive_distribution']['type']} incompatible avec {q['question_type']}")


def validate_resolution_record(rec: dict) -> None:
    errors = [f for f in RESOLUTION_FIELDS if f not in rec or (f != "hash_previous" and _blank(rec.get(f)))]
    if rec.get("ambiguity_policy_applied") not in AMBIGUITY_POLICIES:
        errors.append("ambiguity_policy_applied invalide")
    if errors:
        raise ValidationError("ResolutionRecord refusé — " + ", ".join(errors))


def horizon_bucket(horizon_days: float, buckets: list[float]) -> str:
    """Classe d'horizon : 'le30', 'le90', ... ou 'gt1825'."""
    for b in buckets:
        if horizon_days <= b:
            return f"le{int(b)}"
    return f"gt{int(buckets[-1])}"


def iso_duration_days(dur: str) -> float:
    """Convertit une durée ISO-8601 simple (P1Y2M10D, P3W, PT12H) en jours."""
    s = (dur or "").strip().upper()
    if not s.startswith("P"):
        raise ValueError(f"durée ISO-8601 invalide : {dur!r}")
    days, num, in_time = 0.0, "", False
    units_date = {"Y": 365.25, "M": 30.4375, "W": 7.0, "D": 1.0}
    units_time = {"H": 1 / 24, "M": 1 / 1440, "S": 1 / 86400}
    for ch in s[1:]:
        if ch == "T":
            in_time = True
        elif ch.isdigit() or ch == ".":
            num += ch
        else:
            table = units_time if in_time else units_date
            if ch not in table or not num:
                raise ValueError(f"durée ISO-8601 invalide : {dur!r}")
            days += float(num) * table[ch]
            num = ""
    return days
