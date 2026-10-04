"""Sérialisation canonique, hachage et générateur pseudo-aléatoire portables.

Ces trois briques doivent produire exactement les mêmes octets en Python et
dans le portage JavaScript (web/oracle_engine.js) : c'est ce qui permet de
vérifier un registre ou de rejouer une prévision indifféremment avec l'un ou
l'autre. Aucune dépendance externe.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any

GENESIS_HASH = "0" * 64


def canon_num(x: Any) -> str:
    """Formate un nombre de façon identique en Python et en JavaScript.

    Règle : un nombre entier de valeur absolue < 1e16 s'écrit sans décimale
    (3.0 -> "3") ; sinon on prend la représentation la plus courte qui
    redonne le même flottant, avec la convention d'exposant de Python
    (1e-05, 1.5e+16).
    """
    if isinstance(x, bool):
        raise TypeError("booléen inattendu")
    if isinstance(x, int):
        if abs(x) < 10**16:
            return str(x)
        x = float(x)
    if not math.isfinite(x):
        raise ValueError("nombre non fini interdit dans un enregistrement")
    if x == int(x) and abs(x) < 1e16:
        return str(int(x))
    return repr(float(x))


def _canon_str(s: str) -> str:
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\f":
            out.append("\\f")
        elif o < 0x20:
            out.append("\\u%04x" % o)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def canonical_json(obj: Any) -> str:
    """JSON canonique : clés triées, pas d'espaces, nombres via canon_num."""
    if obj is None:
        return "null"
    if obj is True:
        return "true"
    if obj is False:
        return "false"
    if isinstance(obj, (int, float)):
        return canon_num(obj)
    if isinstance(obj, str):
        return _canon_str(obj)
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(canonical_json(v) for v in obj) + "]"
    if isinstance(obj, dict):
        items = sorted(obj.items(), key=lambda kv: kv[0])
        return "{" + ",".join(_canon_str(str(k)) + ":" + canonical_json(v) for k, v in items) + "}"
    raise TypeError(f"type non sérialisable : {type(obj)!r}")


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def hash_obj(obj: Any) -> str:
    return sha256_hex(canonical_json(obj))


class Mulberry32:
    """PRNG 32 bits, identique bit à bit à l'implémentation JavaScript."""

    def __init__(self, seed: int):
        self.state = seed & 0xFFFFFFFF

    def next_u32(self) -> int:
        self.state = (self.state + 0x6D2B79F5) & 0xFFFFFFFF
        t = self.state
        t = _imul(t ^ (t >> 15), t | 1)
        t ^= (t + _imul(t ^ (t >> 7), t | 61)) & 0xFFFFFFFF
        return (t ^ (t >> 14)) & 0xFFFFFFFF

    def random(self) -> float:
        return self.next_u32() / 4294967296.0

    def randint(self, n: int) -> int:
        """Entier uniforme dans [0, n)."""
        return int(self.random() * n)


def _imul(a: int, b: int) -> int:
    return (a * b) & 0xFFFFFFFF
