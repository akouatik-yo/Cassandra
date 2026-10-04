"""Serveur web local (bibliothèque standard) : l'interface + une API JSON.

    python -m oracle_calibre.server --data data/reel --port 8000

Le moteur exécuté est le moteur Python de référence. Le LLM n'est utilisé
que si ANTHROPIC_API_KEY (ou un profil `ant auth login`) est disponible ;
sinon seule la formalisation manuelle est proposée.
"""
from __future__ import annotations

import argparse
import json
import os
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .app import OracleApp
from .ledger.shadow import normalize_manifold, normalize_metaculus

ROOT = Path(__file__).resolve().parent.parent


def make_clients(model: str, second_model: str | None):
    try:
        from .ingestion import AnthropicClient
        primary = AnthropicClient(model)
        primary._client.models.retrieve(model)  # vérifie les identifiants sans consommer de jetons
        secondary = AnthropicClient(second_model) if second_model else None
        return primary, secondary
    except Exception as exc:  # pas de SDK ou pas d'identifiants
        print(f"LLM indisponible ({exc}) : mode saisie manuelle uniquement.")
        return None, None


def build_page(mode: str) -> str:
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    from build_web import build_server_page
    return build_server_page()


class Handler(BaseHTTPRequestHandler):
    apps: dict[str, OracleApp] = {}

    def _app(self, ns: str) -> OracleApp:
        return self.apps["demo" if ns == "demo" else "real"]

    def _send(self, code: int, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        if os.environ.get("ORACLE_DEBUG"):
            import time
            print(f"{self.command} {self.path} {code} {len(data)} o {time.time() - getattr(self, '_t0', time.time()):.3f}s", flush=True)

    def log_message(self, fmt, *args):  # journal discret
        pass

    def do_GET(self):
        import time
        self._t0 = time.time()
        path, _, query = self.path.partition("?")
        ns = "demo" if "ns=demo" in query else "real"
        try:
            if path in ("/", "/index.html"):
                return self._send(200, build_page("server"), "text/html; charset=utf-8")
            app = self._app(ns)
            routes = {
                "/api/info": lambda: {"llm": app.primary is not None, "second_model": app.secondary is not None,
                                      "model_id": app.primary.model_id if app.primary else None, "runtime": "python",
                                      "statistical_config_version": app.cfg["statistical_config_version"]},
                "/api/entries": lambda: app.ledger.entries,
                "/api/verify": app.verify,
                "/api/dashboard": app.dashboard,
                "/api/tournament": app.tournament,
                "/api/shadow": app.shadow_report,
                "/api/scores": app.scores,
            }
            if path in routes:
                return self._send(200, routes[path]())
            return self._send(404, {"error": "introuvable"})
        except Exception as exc:
            traceback.print_exc()
            return self._send(500, {"error": str(exc)})

    def do_POST(self):
        path, _, query = self.path.partition("?")
        ns = "demo" if "ns=demo" in query else "real"
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            app = self._app(ns)
            if path == "/api/draft":
                d = app.start_draft(body["text"])
                app.add_sources(d["draft_id"], body.get("sources") or [])
                return self._send(200, d)
            if path == "/api/draft/manual":
                return self._send(200, app.manual_draft(body["fields"]))
            if path == "/api/draft/validate":
                return self._send(200, {"draft": app.validate(body["draft_id"], body.get("edits"), body["validated"])})
            if path == "/api/draft/commit":
                return self._send(200, app.commit(body["draft_id"], market=body.get("market"), links=body.get("links"),
                                                  supersedes=body.get("supersedes"),
                                                  annotations=body.get("annotations")))
            if path == "/api/resolve":
                return self._send(200, app.resolve(body["question_id"], body["outcome"], body["effective_source"],
                                                   body["resolvability_score_post"], body.get("ambiguity_policy_applied")))
            if path == "/api/anchor":
                return self._send(200, app.anchor(body.get("method", "pending")))
            if path == "/api/replay":
                return self._send(200, app.replay(body["forecast_id"]))
            if path == "/api/reference":
                return self._send(200, app.import_reference(body["items"], body.get("source", "import")))
            if path == "/api/shadow/import":
                raw = body["data"]
                items = normalize_manifold(raw) if body.get("platform") == "manifold" else normalize_metaculus(raw)
                created = [r for r in (app.shadow_forecast(it) for it in items) if r]
                resolved = app.shadow_sync(items)
                return self._send(200, {"created": len(created), "resolved": len(resolved), "seen": len(items)})
            if path == "/api/demo":
                if ns != "demo":
                    return self._send(400, {"error": "l'historique synthétique est réservé au bac à sable"})
                return self._send(200, {"created": app.demo_history(int(body.get("n", 40)))})
            return self._send(404, {"error": "introuvable"})
        except Exception as exc:
            traceback.print_exc()
            return self._send(400, {"error": str(exc)})


def main(argv=None):
    ap = argparse.ArgumentParser(description="Oracle Calibré — serveur local")
    ap.add_argument("--data", default=os.environ.get("ORACLE_DATA", "data"))
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--second-model", default="claude-haiku-4-5")
    args = ap.parse_args(argv)
    primary, secondary = make_clients(args.model, args.second_model)
    Handler.apps = {"real": OracleApp(Path(args.data) / "reel", primary, secondary),
                    "demo": OracleApp(Path(args.data) / "bac_a_sable", primary, secondary)}
    print(f"Oracle Calibré : http://127.0.0.1:{args.port}")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
