"""Parcours navigateur (Playwright/Chromium) : serveur Python et version hébergée simulée.

Ignorés si node ou le paquet playwright (global) sont absents.
"""
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
E2E = Path(__file__).with_name("e2e")


def _node_path():
    if shutil.which("node") is None:
        return None
    root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip()
    return root if (Path(root) / "playwright").exists() else None


NODE_PATH = _node_path()
pytestmark = pytest.mark.skipif(NODE_PATH is None, reason="playwright (node) indisponible")


def _run(args, tmp_path):
    env = dict(os.environ, NODE_PATH=NODE_PATH)
    out = subprocess.run(["node", *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    assert out.returncode == 0, out.stdout + out.stderr
    return out.stdout


def test_hosted_version_with_simulated_claude(tmp_path):
    subprocess.run([sys.executable, "scripts/build_web.py"], cwd=ROOT, check=True, capture_output=True)
    out = _run([str(E2E / "e2e_local.js"), str(tmp_path)], tmp_path)
    assert "Rejeu identique" in out and "Chaîne altérée" in out and "erreurs : aucune" in out
    from oracle_calibre.ledger import verify_entries
    import json
    assert verify_entries(json.loads((tmp_path / "ledger_from_js.json").read_text()))["ok"]


def test_python_server_in_browser(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    srv = subprocess.Popen([sys.executable, "-m", "oracle_calibre.server", "--data", str(tmp_path / "data"), "--port",
                            str(port)], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.2)
        out = _run([str(E2E / "e2e_server.js"), str(tmp_path), str(port)], tmp_path)
        assert "Rejeu identique" in out and "Chaîne intègre" in out and "débordement horizontal mobile: false" in out
    finally:
        srv.terminate()
        srv.wait(timeout=10)
