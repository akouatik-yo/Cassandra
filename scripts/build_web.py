"""Assemble l'interface en une seule page HTML.

    python scripts/build_web.py            # écrit web/dist/index.html (version hébergée, mode "local")

Le serveur Python appelle build_html("server") à chaque requête. La version
hébergée embarque : la configuration préenregistrée, les prompts, le moteur
JavaScript et l'empreinte de ce code (inscrite dans chaque ForecastRecord).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from oracle_calibre import __version__  # noqa: E402
from oracle_calibre.canonical import sha256_hex  # noqa: E402
from oracle_calibre.config import load_config, load_prompts  # noqa: E402

WEB = ROOT / "web"
FONTS = ("https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600"
         "&family=IBM+Plex+Serif:wght@500;600&display=swap")


def _commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5)
        dirty = subprocess.run(["git", "status", "--porcelain", "web", "oracle_calibre", "config", "prompts"], cwd=ROOT,
                               capture_output=True, text=True, timeout=5).stdout.strip()
        return (out.stdout.strip() or "inconnu") + ("+modifications" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return "inconnu"


def _safe_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def build_html(mode: str = "local") -> str:
    cfg = load_config()
    config_hash = cfg.pop("_config_hash")
    prompts = load_prompts()
    engine = (WEB / "oracle_engine.js").read_text(encoding="utf-8")
    backend = (WEB / "backend.js").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    build = {"code_commit": _commit(), "code_version": __version__, "config_hash": config_hash,
             "dependency_lock_hash": "js-engine-sha256:" + sha256_hex(engine + backend)}
    boot = {"mode": mode, "cfg": cfg, "prompts": prompts, "build": build}
    return f"""<title>Oracle Calibré</title>
<meta name="description" content="Prévisions probabilistes auditables : règle de résolution validée, probabilité calibrée, registre chaîné.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="{FONTS}">
<style>
{css}
</style>
<div id="app"><p style="padding:24px">Chargement d'Oracle Calibré…</p></div>
<script>window.ORACLE_BOOT = {_safe_json(boot)};</script>
<script>
{engine}
</script>
<script>
{backend}
</script>
<script>
{app}
</script>
"""


def build_server_page() -> str:
    return "<!doctype html><html lang=\"fr\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" " \
           "content=\"width=device-width,initial-scale=1,viewport-fit=cover\"></head><body>" + build_html("server") + "</body></html>"


if __name__ == "__main__":
    out = WEB / "dist" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_html("local"), encoding="utf-8")
    print(f"{out} ({out.stat().st_size // 1024} Kio)")
