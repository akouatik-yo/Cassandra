"""Ingestion : SEUL module autorisé à appeler un LLM.

Le LLM formalise, reformule, décompose et extrait des triplets
(fait, date, source primaire). Il ne produit jamais de probabilité ni de poids.
"""
from .llm import AnthropicClient, LLMClient, ReplayClient, ScriptedClient
from .pipeline import Session, apply_human_validation, extract_sources, formalize, prompts_hash, utc_now

__all__ = ["AnthropicClient", "LLMClient", "ReplayClient", "ScriptedClient", "Session", "formalize",
           "apply_human_validation", "extract_sources", "prompts_hash", "utc_now"]
