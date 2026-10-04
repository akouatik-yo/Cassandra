"""Arbre de Merkle (définition RFC 6962 : découpage à la plus grande puissance de 2)."""
from __future__ import annotations

import hashlib

EMPTY_ROOT = hashlib.sha256(b"").hexdigest()


def _leaf(h: str) -> bytes:
    return hashlib.sha256(b"\x00" + bytes.fromhex(h)).digest()


def _node(a: bytes, b: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + a + b).digest()


def _mth(leaves: list[bytes]) -> bytes:
    n = len(leaves)
    if n == 1:
        return leaves[0]
    k = 1
    while k * 2 < n:
        k *= 2
    return _node(_mth(leaves[:k]), _mth(leaves[k:]))


def merkle_root(leaf_hashes: list[str]) -> str:
    if not leaf_hashes:
        return EMPTY_ROOT
    return _mth([_leaf(h) for h in leaf_hashes]).hex()


def ledger_leaves(entries: list[dict], upto_seq: int | None = None) -> list[str]:
    """Feuilles : hash des ForecastRecords, ResolutionRecords, lots de référence
    et hash des snapshots LLM/récupération. Les ancrages ne sont pas des feuilles."""
    out = []
    for e in entries:
        if upto_seq is not None and e["seq"] > upto_seq:
            break
        if e["record_type"] in ("forecast", "resolution", "reference_batch"):
            out.append(e["entry_hash"])
        elif e["record_type"] == "snapshot":
            out.append(e["record"]["snapshot_hash"])
    return out
