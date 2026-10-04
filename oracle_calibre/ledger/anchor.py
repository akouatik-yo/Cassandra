"""Ancrage externe de la racine de Merkle.

Deux méthodes :
  - rfc3161 : horodatage par une autorité tierce (TSA). La requête DER est
    construite ici sans dépendance ; le jeton renvoyé est stocké en base64
    et se vérifie avec `openssl ts -verify` (voir README).
  - public_repo : la racine est ajoutée à anchors/merkle_roots.jsonl, à
    pousser dans un dépôt public ; l'horodatage du commit fait foi.
Sans ancrage externe, l'opérateur pourrait antidater une prévision.
"""
from __future__ import annotations

import base64
import json
import secrets
import urllib.request
from pathlib import Path

SHA256_OID = bytes([0x06, 0x09, 0x60, 0x86, 0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x01])
DEFAULT_TSA = "https://freetsa.org/tsr"


def _der_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    out = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(out)]) + out


def _tlv(tag: int, body: bytes) -> bytes:
    return bytes([tag]) + _der_len(len(body)) + body


def _der_int(v: int) -> bytes:
    raw = v.to_bytes(max(1, (v.bit_length() + 8) // 8), "big", signed=False)
    return _tlv(0x02, raw)


def build_timestamp_request(digest_hex: str, nonce: int | None = None) -> bytes:
    """TimeStampReq (RFC 3161 §2.4.1) pour un condensat SHA-256."""
    digest = bytes.fromhex(digest_hex)
    if len(digest) != 32:
        raise ValueError("condensat SHA-256 attendu")
    algo = _tlv(0x30, SHA256_OID + bytes([0x05, 0x00]))
    imprint = _tlv(0x30, algo + _tlv(0x04, digest))
    nonce = secrets.randbits(63) if nonce is None else nonce
    body = _der_int(1) + imprint + _der_int(nonce) + bytes([0x01, 0x01, 0xFF])
    return _tlv(0x30, body)


def request_rfc3161(digest_hex: str, tsa_url: str = DEFAULT_TSA, timeout: float = 20.0) -> dict:
    req = build_timestamp_request(digest_hex)
    http = urllib.request.Request(tsa_url, data=req, headers={"Content-Type": "application/timestamp-query"})
    with urllib.request.urlopen(http, timeout=timeout) as resp:
        token = resp.read()
    return {"tsa_url": tsa_url, "timestamp_query_b64": base64.b64encode(req).decode(),
            "timestamp_token_b64": base64.b64encode(token).decode()}


def publish_to_repo(root: str, leaf_count: int, covered_seq_max: int, timestamp_utc: str,
                    path: str | Path) -> dict:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"merkle_root": root, "leaf_count": leaf_count, "covered_seq_max": covered_seq_max,
                       "timestamp_utc": timestamp_utc}, sort_keys=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return {"publication_ref": f"{path} (à committer et pousser dans un dépôt public)"}


def make_anchor(ledger, method: str, timestamp_utc: str, tsa_url: str = DEFAULT_TSA,
                repo_file: str | Path | None = None) -> dict:
    """Calcule la racine courante, l'ancre, puis inscrit l'ancrage au registre."""
    if len(ledger) == 0:
        raise ValueError("registre vide : rien à ancrer")
    covered = len(ledger) - 1
    root, n = ledger.merkle_root(covered)
    anchor = {"merkle_root": root, "leaf_count": n, "covered_seq_max": covered, "anchor_method": method,
              "timestamp_utc": timestamp_utc}
    if method == "rfc3161":
        anchor.update(request_rfc3161(root, tsa_url))
    elif method == "public_repo":
        anchor.update(publish_to_repo(root, n, covered, timestamp_utc, repo_file or "anchors/merkle_roots.jsonl"))
    elif method == "pending":
        anchor["publication_ref"] = "racine calculée, ancrage externe à effectuer"
    else:
        raise ValueError(method)
    return ledger.append_anchor(anchor)
