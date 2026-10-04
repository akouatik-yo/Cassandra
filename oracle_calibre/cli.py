"""Ligne de commande : vérification, ancrage, rejeu, import, rapports.

    python -m oracle_calibre.cli verify --data data/reel
    python -m oracle_calibre.cli anchor --method rfc3161 --data data/reel
    python -m oracle_calibre.cli replay <forecast_id> --data data/reel
    python -m oracle_calibre.cli import-shadow manifold markets.json --data data/reel
    python -m oracle_calibre.cli report --data data/reel
    python -m oracle_calibre.cli verify-file export.json    # registre exporté depuis la version hébergée
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .app import OracleApp
from .ledger import verify_entries
from .ledger.shadow import normalize_manifold, normalize_metaculus


def main(argv=None):
    ap = argparse.ArgumentParser(prog="oracle")
    ap.add_argument("--data", default="data/reel")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify")
    a = sub.add_parser("anchor")
    a.add_argument("--method", choices=["rfc3161", "public_repo", "pending"], default="rfc3161")
    a.add_argument("--tsa", default="https://freetsa.org/tsr")
    r = sub.add_parser("replay")
    r.add_argument("forecast_id")
    s = sub.add_parser("import-shadow")
    s.add_argument("platform", choices=["manifold", "metaculus"])
    s.add_argument("file")
    rf = sub.add_parser("import-reference")
    rf.add_argument("file")
    sub.add_parser("report")
    vf = sub.add_parser("verify-file")
    vf.add_argument("file")
    args = ap.parse_args(argv)

    if args.cmd == "verify-file":
        entries = json.loads(Path(args.file).read_text(encoding="utf-8"))
        out = verify_entries(entries)
    else:
        app = OracleApp(Path(args.data))
        if args.cmd == "verify":
            out = app.verify()
        elif args.cmd == "anchor":
            kw = {"tsa_url": args.tsa} if args.method == "rfc3161" else {}
            out = app.anchor(args.method, **kw)
        elif args.cmd == "replay":
            out = app.replay(args.forecast_id)
        elif args.cmd == "import-shadow":
            raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
            raw = raw.get("results", raw) if isinstance(raw, dict) else raw
            items = normalize_manifold(raw) if args.platform == "manifold" else normalize_metaculus(raw)
            created = [x for x in (app.shadow_forecast(it) for it in items) if x]
            out = {"created": len(created), "resolved": len(app.shadow_sync(items)), "seen": len(items)}
        elif args.cmd == "import-reference":
            items = json.loads(Path(args.file).read_text(encoding="utf-8"))
            out = app.import_reference(items, Path(args.file).name)
        else:
            out = {"dashboard": app.dashboard(), "tournament": app.tournament(), "shadow": app.shadow_report()}
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main()
