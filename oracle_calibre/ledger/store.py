"""Registre en ajout seul, chaîné par hash.

Chaque entrée est {seq, record_type, record, entry_hash} avec
    record.hash_previous = entry_hash de l'entrée précédente
    entry_hash = sha256(JSON canonique de {seq, record_type, record})
Il n'existe aucune méthode de modification ni de suppression. Une mise à
jour de prévision est un nouveau ForecastRecord portant
supersedes_forecast_id. Toute altération du fichier est détectée par
verify() : les hash suivants et la racine de Merkle ancrée ne concordent plus.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from ..canonical import GENESIS_HASH, canonical_json, hash_obj, sha256_hex
from ..schema import ValidationError, validate_forecast_record, validate_resolution_record
from .merkle import ledger_leaves, merkle_root

RECORD_TYPES = ("forecast", "resolution", "snapshot", "reference_batch", "anchor")


class ImmutableRecordError(RuntimeError):
    """Tentative de modifier ou de dupliquer un enregistrement figé."""


def entry_hash(seq: int, record_type: str, record: dict) -> str:
    return hash_obj({"seq": seq, "record_type": record_type, "record": record})


class Ledger:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self._entries: list[dict] = []
        if self.path and self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        self._entries.append(json.loads(line))

    # --- lecture (copies : rien de ce qui est renvoyé ne peut altérer le registre)
    @property
    def entries(self) -> list[dict]:
        return copy.deepcopy(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def head_hash(self) -> str:
        return self._entries[-1]["entry_hash"] if self._entries else GENESIS_HASH

    def forecasts_for(self, question_id: str) -> list[dict]:
        return [copy.deepcopy(e["record"]) for e in self._entries
                if e["record_type"] == "forecast" and e["record"]["question"]["question_id"] == question_id]

    def latest_forecast(self, question_id: str) -> dict | None:
        f = self.forecasts_for(question_id)
        return f[-1] if f else None

    def resolution_for(self, question_id: str) -> dict | None:
        r = [e["record"] for e in self._entries
             if e["record_type"] == "resolution" and e["record"]["question_id"] == question_id]
        return copy.deepcopy(r[-1]) if r else None

    def find_forecast(self, forecast_id: str) -> dict | None:
        for e in self._entries:
            if e["record_type"] == "forecast" and e["record"]["forecast_id"] == forecast_id:
                return copy.deepcopy(e)
        return None

    # --- écriture (ajout seul)
    def _append(self, record_type: str, record: dict) -> dict:
        if record_type not in RECORD_TYPES:
            raise ValueError(record_type)
        record = copy.deepcopy(record)
        record["hash_previous"] = self.head_hash()
        seq = len(self._entries)
        entry = {"seq": seq, "record_type": record_type, "record": record,
                 "entry_hash": entry_hash(seq, record_type, record)}
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(canonical_json(entry) + "\n")
                f.flush()
                os.fsync(f.fileno())
        self._entries.append(entry)
        return copy.deepcopy(entry)

    def append_forecast(self, record: dict) -> dict:
        record = copy.deepcopy(record)
        record.setdefault("hash_previous", GENESIS_HASH)
        validate_forecast_record(record)
        fid, qid = record["forecast_id"], record["question"]["question_id"]
        if self.find_forecast(fid) is not None:
            raise ImmutableRecordError(f"forecast_id {fid} existe déjà : un ForecastRecord ne se modifie pas, "
                                       "il se remplace par un nouvel enregistrement (supersedes_forecast_id).")
        if self.resolution_for(qid) is not None:
            raise ImmutableRecordError(f"question {qid} déjà résolue : plus aucune prévision acceptée.")
        latest = self.latest_forecast(qid)
        sup = record.get("supersedes_forecast_id")
        if latest is None and sup is not None:
            raise ValidationError("supersedes_forecast_id pointe vers une question sans prévision.")
        if latest is not None and sup != latest["forecast_id"]:
            raise ImmutableRecordError("une nouvelle prévision sur une question existante doit remplacer la "
                                       f"dernière ({latest['forecast_id']}) via supersedes_forecast_id.")
        return self._append("forecast", record)

    def append_resolution(self, record: dict) -> dict:
        record = copy.deepcopy(record)
        record.setdefault("hash_previous", GENESIS_HASH)
        validate_resolution_record(record)
        qid = record["question_id"]
        if self.latest_forecast(qid) is None:
            raise ValidationError(f"aucune prévision pour la question {qid}")
        if self.resolution_for(qid) is not None:
            raise ImmutableRecordError(f"question {qid} déjà résolue")
        if record["outcome"] not in (0, 1, "cancelled"):
            raise ValidationError("outcome binaire attendu : 1, 0 ou 'cancelled'")
        return self._append("resolution", record)

    def append_snapshot(self, snapshot_hash: str, kind: str, timestamp_utc: str) -> dict:
        return self._append("snapshot", {"snapshot_hash": snapshot_hash, "kind": kind, "timestamp_utc": timestamp_utc})

    def append_reference_batch(self, items: list[dict], source: str, timestamp_utc: str) -> dict:
        for it in items:
            for f in ("question_type", "horizon_days", "outcome", "resolved_at"):
                if f not in it:
                    raise ValidationError(f"élément de référence sans {f}")
        return self._append("reference_batch", {"source": source, "items": items, "timestamp_utc": timestamp_utc})

    def append_anchor(self, anchor: dict) -> dict:
        return self._append("anchor", anchor)

    # --- audit
    def merkle_root(self, upto_seq: int | None = None) -> tuple[str, int]:
        leaves = ledger_leaves(self._entries, upto_seq)
        return merkle_root(leaves), len(leaves)

    def verify(self) -> dict:
        return verify_entries(self._entries)


def verify_entries(entries: list[dict]) -> dict:
    """Recalcule toute la chaîne et toutes les racines ancrées."""
    prev = GENESIS_HASH
    problems = []
    for i, e in enumerate(entries):
        if e["seq"] != i:
            problems.append({"seq": e["seq"], "problem": "numéro de séquence inattendu"})
        if e["record"].get("hash_previous") != prev:
            problems.append({"seq": e["seq"], "problem": "hash_previous ne correspond pas à l'entrée précédente"})
        h = entry_hash(e["seq"], e["record_type"], e["record"])
        if h != e["entry_hash"]:
            problems.append({"seq": e["seq"], "problem": "contenu modifié (entry_hash invalide)"})
        prev = e["entry_hash"]
    anchors = []
    for e in entries:
        if e["record_type"] == "anchor":
            rec = e["record"]
            leaves = ledger_leaves(entries, rec["covered_seq_max"])
            root = merkle_root(leaves)
            ok = root == rec["merkle_root"] and len(leaves) == rec["leaf_count"]
            anchors.append({"seq": e["seq"], "merkle_root": rec["merkle_root"], "ok": ok,
                            "method": rec.get("anchor_method")})
            if not ok:
                problems.append({"seq": e["seq"], "problem": "racine de Merkle ancrée invalide"})
    return {"ok": not problems, "n_entries": len(entries), "problems": problems, "anchors": anchors,
            "head": prev}


class SnapshotStore:
    """Magasin adressé par contenu pour les sorties LLM et les sources récupérées."""

    def __init__(self, directory: str | Path | None = None):
        self.dir = Path(directory) if directory else None
        self._mem: dict[str, dict] = {}

    def put(self, obj: dict) -> str:
        text = canonical_json(obj)
        h = sha256_hex(text)
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)
            p = self.dir / f"{h}.json"
            if not p.exists():
                p.write_text(text, encoding="utf-8")
        self._mem[h] = json.loads(text)
        return h

    def get(self, h: str) -> dict:
        if h in self._mem:
            obj = self._mem[h]
        elif self.dir and (self.dir / f"{h}.json").exists():
            obj = json.loads((self.dir / f"{h}.json").read_text(encoding="utf-8"))
        else:
            raise KeyError(h)
        if hash_obj(obj) != h:
            raise ValueError(f"snapshot {h} altéré")
        return copy.deepcopy(obj)
