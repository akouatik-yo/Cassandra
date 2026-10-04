"""Clients LLM. Ce fichier est le SEUL point d'appel d'un LLM dans tout le projet.

Garde-fou d'isolation : LLMClient.complete() vérifie que l'appelant direct
appartient au paquet oracle_calibre.ingestion ; tout autre appelant lève
LLMIsolationError. Le test n°2 vérifie aussi statiquement qu'aucun module
hors ingestion/ n'importe ce fichier ni un SDK de LLM.
"""
from __future__ import annotations

import inspect
import json
from dataclasses import dataclass

from ..canonical import sha256_hex

ALLOWED_CALLER_PREFIX = "oracle_calibre.ingestion"


class LLMIsolationError(RuntimeError):
    pass


class MissingLLMResponse(KeyError):
    """Rejeu : aucune réponse figée ne correspond à cette requête."""


@dataclass(frozen=True)
class LLMRequest:
    prompt_id: str
    prompt: str
    role: str = "primary"  # "primary" | "secondary" (second modèle, accord inter-modèles)
    sample_index: int = 0

    def key(self) -> str:
        return f"{self.prompt_id}|{self.role}|{self.sample_index}|{sha256_hex(self.prompt)}"


class LLMClient:
    provider = "none"
    model_id = "none"
    model_version = "none"
    temperature = None  # None = paramètre non supporté / non transmis
    seed = None

    def info(self) -> dict:
        return {"llm_provider": self.provider, "model_id": self.model_id, "model_version": self.model_version,
                "temperature": self.temperature, "seed_if_supported": self.seed}

    def complete(self, req: LLMRequest) -> str:
        caller = inspect.stack()[1].frame.f_globals.get("__name__", "")
        if not caller.startswith(ALLOWED_CALLER_PREFIX):
            raise LLMIsolationError(f"appel LLM refusé depuis {caller!r} : seul ingestion/ peut appeler un LLM")
        return self._complete(req)

    def _complete(self, req: LLMRequest) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class AnthropicClient(LLMClient):
    """Claude via le SDK officiel `anthropic` (importé paresseusement).

    Les modèles Claude actuels refusent `temperature` et n'exposent pas de
    graine : la variabilité entre reformulations vient de l'échantillonnage
    par défaut, et les sorties sont figées en snapshots pour le rejeu.
    """

    provider = "anthropic"

    def __init__(self, model_id: str = "claude-opus-5-5", effort: str = "medium", max_tokens: int = 16000):
        import anthropic  # noqa: PLC0415 - dépendance réservée à ingestion/

        self._client = anthropic.Anthropic()
        self.model_id = model_id
        self.model_version = model_id
        self.effort = effort
        self.max_tokens = max_tokens

    def _complete(self, req: LLMRequest) -> str:
        resp = self._client.beta.messages.create(
            model=self.model_id,
            max_tokens=self.max_tokens,
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default", "output_config": {"effort": self.effort}},
            messages=[{"role": "user", "content": req.prompt}],
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("le modèle a refusé la requête (stop_reason=refusal)")
        self.model_version = getattr(resp, "model", self.model_id)
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


class ReplayClient(LLMClient):
    """Rejoue des réponses figées (snapshots) sans aucun appel réseau."""

    provider = "replay"

    def __init__(self, snapshots: list[dict], info: dict | None = None):
        self._by_key = {s["request_key"]: s["response_text"] for s in snapshots}
        if info:
            self.provider = info.get("llm_provider", "replay")
            self.model_id = info.get("model_id", "replay")
            self.model_version = info.get("model_version", "replay")

    def _complete(self, req: LLMRequest) -> str:
        k = req.key()
        if k not in self._by_key:
            raise MissingLLMResponse(k)
        return self._by_key[k]


class ScriptedClient(LLMClient):
    """Client de test : une fonction déterministe produit la réponse."""

    provider = "scripted"
    model_id = "scripted-test-model"
    model_version = "1"

    def __init__(self, fn):
        self.fn = fn

    def _complete(self, req: LLMRequest) -> str:
        out = self.fn(req)
        return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)


def parse_json_reply(text: str):
    """Lecture tolérante : JSON entier, sinon du premier '{' au dernier '}'."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("\n") + 1:] if "\n" in t else t
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        a, b = t.find("{"), t.rfind("}")
        if a < 0 or b <= a:
            raise ValueError("réponse LLM sans JSON exploitable") from None
        return json.loads(t[a:b + 1])
