"""Chargement de la configuration statistique versionnée et des prompts."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .canonical import hash_obj, sha256_hex

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "stat_config_v2.json"  # v1 conservée, jamais modifiée
DEFAULT_PROMPTS = ROOT / "prompts" / "prompts_v1.json"
LOCK_FILE = ROOT / "requirements.lock"


def load_config(path: str | Path | None = None) -> dict:
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["_config_hash"] = hash_obj({k: v for k, v in cfg.items() if not k.startswith("_")})
    return cfg


def load_prompts(path: str | Path | None = None) -> dict:
    with open(path or DEFAULT_PROMPTS, encoding="utf-8") as f:
        return json.load(f)


def dependency_lock_hash() -> str:
    try:
        return sha256_hex(LOCK_FILE.read_bytes())
    except OSError:
        return "unlocked"


def code_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "uncommitted"
    except (OSError, subprocess.SubprocessError):
        return "unknown"
