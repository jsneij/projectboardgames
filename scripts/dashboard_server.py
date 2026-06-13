"""Local HTTP server for the BGG dashboard editor.

Serves the existing single-file dashboard plus `data/` as static assets and
exposes two endpoints:

  GET  /api/status   → {"mode": "edit"}
  POST /api/save     → validate + write + git add/commit/push

Bind 127.0.0.1 only — single-user local-only tool.
"""

from __future__ import annotations

import http.server
import json
import os
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent

# Allow the test suite or other tools to import the core helpers directly
sys.path.insert(0, str(REPO_ROOT))
from scripts.dashboard_server_core import (  # noqa: E402
    atomic_write_json,
    commit_and_push,
    validate_scores,
)

SCORES_PATH = REPO_ROOT / "data" / "bgg_collection_scores.json"
DASHBOARD_HTML = REPO_ROOT / "dashboard" / "dshb_bgg_collection.html"
ALLOWED_DIRS = ("dashboard", "data")
HOST = "127.0.0.1"
PORT = int(os.environ.get("DASHBOARD_PORT", "8765"))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(REPO_ROOT), **kwargs)

    # ── Logging: keep server output tidy ────────────────────────────────
    def log_message(self, format, *args):  # noqa: A002 — matches BaseHTTPRequestHandler signature
        sys.stderr.write("[dashboard_server] " + format % args + "\n")

    # ── Helpers ─────────────────────────────────────────────────────────
    def _send_json(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _safe_static_path(self, raw_path: str) -> bool:
        path = urlparse(raw_path).path.lstrip("/")
        if not path:
            return False
        # Block directory listings — require a non-empty filename after the dir
        if path.endswith("/"):
            return False
        if ".." in path.split("/"):
            return False
        top = path.split("/", 1)[0]
        return top in ALLOWED_DIRS

    # ── Routes ──────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in ("/", ""):
            self._send_dashboard()
            return
        if parsed.path == "/api/status":
            self._send_json(200, {"mode": "edit"})
            return
        if self._safe_static_path(self.path):
            super().do_GET()
            return
        self.send_error(404, "Not Found")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/save":
            self.send_error(404, "Not Found")
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json(400, {
                "status": "validation_error",
                "errors": [{"field": "<root>", "problem": "invalid JSON"}],
                "message": "Invalid JSON.",
            })
            return

        scores = body.get("scores")
        changed_games = body.get("changed_games") or []

        with open(SCORES_PATH, encoding="utf-8") as f:
            existing = json.load(f)

        errors = validate_scores(scores, existing)
        if errors:
            self._send_json(400, {
                "status": "validation_error",
                "errors": errors,
                "message": "Validation failed.",
            })
            return

        atomic_write_json(SCORES_PATH, scores)
        result = commit_and_push(list(changed_games), SCORES_PATH, REPO_ROOT)
        code = 200 if result["status"] in ("ok", "committed_not_pushed", "noop") else 500
        self._send_json(code, result)

    def _send_dashboard(self) -> None:
        if not DASHBOARD_HTML.exists():
            self.send_error(404, "dashboard HTML missing")
            return
        data = DASHBOARD_HTML.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    httpd = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    print(f"[dashboard_server] Serving on {url}  (Ctrl+C to stop)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard_server] shutting down")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
