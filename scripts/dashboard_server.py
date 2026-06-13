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
import time
import webbrowser
from pathlib import Path
from urllib.parse import unquote, urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent

# Allow the test suite or other tools to import the core helpers directly
sys.path.insert(0, str(REPO_ROOT))
from scripts.dashboard_server_core import (  # noqa: E402
    atomic_write_json,
    commit_and_push,
    validate_scores,
)

SCORES_PATH = REPO_ROOT / "data" / "bgg_collection_scores.json"
EXPLORER_SOURCES_PATH = REPO_ROOT / "data" / "explorer_sources.json"
EXPLORER_DISCOVER_SCRIPT = REPO_ROOT / "scripts" / "explorer_discover.py"
DASHBOARD_HTML = REPO_ROOT / "dashboard" / "dshb_bgg_collection.html"
ALLOWED_DIRS = ("dashboard", "data")
HOST = "127.0.0.1"
PORT = int(os.environ.get("DASHBOARD_PORT", "8765"))
KNOWN_SCRAPER_KEYS = {"bgg_hot", "bgg_geeklists", "polyhedron_collider", "solitaire_times"}


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
        # Decode percent-encoding first so encoded slashes like %2F can't smuggle
        # `..` segments past the split-and-check below.
        path = unquote(urlparse(raw_path).path).lstrip("/")
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
        if self.path == "/api/explorer/refresh":
            self._handle_explorer_refresh()
            return
        if self.path == "/api/explorer/sources":
            self._handle_explorer_sources()
            return
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

        try:
            with open(SCORES_PATH, encoding="utf-8") as f:
                existing = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self._send_json(500, {
                "status": "error",
                "commit": None,
                "pushed": False,
                "message": f"Could not read scores file: {e}",
            })
            return

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

    # ── Explorer endpoints ─────────────────────────────────────────────────
    def _handle_explorer_refresh(self) -> None:
        """Fire scripts/explorer_discover.py in a background subprocess.

        The discovery pipeline takes minutes (BGG rate-limit + Claude API),
        so we don't block the response. The user reloads after a bit to see
        new candidates. stdout/err go to /tmp for inspection.
        """
        import subprocess as _sp
        if not EXPLORER_DISCOVER_SCRIPT.exists():
            self._send_json(500, {"status": "error",
                                  "message": "explorer_discover.py missing"})
            return
        log_path = REPO_ROOT / "data" / ".explorer_discover.log"
        try:
            log_fh = open(log_path, "a", encoding="utf-8")
            log_fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} run started ===\n")
            log_fh.flush()
            _sp.Popen(
                [sys.executable, str(EXPLORER_DISCOVER_SCRIPT)],
                cwd=str(REPO_ROOT),
                stdout=log_fh,
                stderr=_sp.STDOUT,
            )
        except OSError as e:
            self._send_json(500, {"status": "error",
                                  "message": f"Could not start subprocess: {e}"})
            return
        self._send_json(200, {
            "status": "started",
            "message": f"Discovery started in background. Log: {log_path.name}",
        })

    def _handle_explorer_sources(self) -> None:
        """POST /api/explorer/sources — validate + write + commit + push."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json(400, {"status": "validation_error",
                                  "message": "Invalid JSON."})
            return

        # Light validation
        if not isinstance(payload, dict):
            self._send_json(400, {"status": "validation_error",
                                  "message": "must be an object"})
            return
        gl = payload.get("bgg_geeklists", [])
        if not isinstance(gl, list) or any(
                not isinstance(x, int) or x < 1 for x in gl):
            self._send_json(400, {"status": "validation_error",
                                  "message": "bgg_geeklists must be a list of positive ints"})
            return
        enabled = payload.get("enabled", {})
        if not isinstance(enabled, dict):
            self._send_json(400, {"status": "validation_error",
                                  "message": "enabled must be an object"})
            return
        for k, v in enabled.items():
            if k not in KNOWN_SCRAPER_KEYS:
                self._send_json(400, {"status": "validation_error",
                                      "message": f"unknown scraper key: {k}"})
                return
            if not isinstance(v, bool):
                self._send_json(400, {"status": "validation_error",
                                      "message": f"{k} must be a bool"})
                return

        atomic_write_json(EXPLORER_SOURCES_PATH, payload)
        result = commit_and_push([], EXPLORER_SOURCES_PATH, REPO_ROOT,
                                 commit_message="Update Explorer sources")
        if result.get("status") == "ok":
            result["message"] = "Sources saved & pushed — next discovery run uses them."
        code = 200 if result.get("status") in ("ok", "committed_not_pushed", "noop") else 500
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
