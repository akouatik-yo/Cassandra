"""Oracle Calibré — prévision probabiliste auditable.

Trois modules isolés :
  ingestion/ : seul module autorisé à appeler un LLM ;
  engine/    : moteur statistique déterministe, sans LLM ;
  ledger/    : registre en ajout seul, notation et évaluation.
"""
__version__ = "0.1.0"
