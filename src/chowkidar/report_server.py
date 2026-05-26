"""Lightweight localhost report server using standard library http.server."""

from __future__ import annotations

import http.server
import json
import logging
import socketserver
import threading
import urllib.parse
from pathlib import Path
from typing import Any

from .editor import open_in_editor

logger = logging.getLogger(__name__)


def _is_safe_path(target_path: Path) -> bool:
    """Ensure the path is under CHOWKIDAR_HOME or watched project roots to prevent traversal / arbitrary reads."""
    try:
        resolved_target = target_path.resolve()
        from .config import CHOWKIDAR_HOME
        from .registry.db import Registry
        
        allowed_parents = [CHOWKIDAR_HOME.resolve()]
        
        # Check current working directory as a safe default parent
        allowed_parents.append(Path.cwd().resolve())

        try:
            registry = Registry()
            registry.init_db()
            projects = registry.get_watched_projects()
            registry.close()
            for proj in projects:
                allowed_parents.append(Path(proj).resolve())
        except Exception:
            pass

        for parent in allowed_parents:
            try:
                resolved_target.relative_to(parent)
                return True
            except ValueError:
                continue
    except Exception:
        pass
    return False


class ReportHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    report_content: str = ""
    report_path: Path | None = None

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "X-Requested-With, Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query = urllib.parse.parse_qs(parsed_url.query)

        if path in ("/", "/index.html", "/report"):
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            content = self.report_content
            # If path query param is provided, read that report from disk (safely)
            if "path" in query:
                try:
                    rep_path = Path(query["path"][0]).resolve()
                    # Check safety: only allow reading .html files from within allowed parents
                    if rep_path.exists() and rep_path.suffix == ".html" and _is_safe_path(rep_path):
                        content = rep_path.read_text(encoding="utf-8")
                    else:
                        content = "<h1>Access Denied: HTML file path is outside allowed boundaries.</h1>"
                except Exception as e:
                    logger.error("Error reading report from query path: %s", e)

            self.wfile.write(content.encode("utf-8"))

        elif path == "/open-editor":
            target_path_str = query.get("path", [""])[0]
            success = False
            message = ""
            if target_path_str:
                try:
                    target_path = Path(target_path_str).resolve()
                    if _is_safe_path(target_path):
                        success = open_in_editor(str(target_path))
                        if not success:
                            message = "All opening attempts failed. Ensure your editor is on PATH or set CHOWKIDAR_EDITOR."
                    else:
                        message = f"Access Denied: Path '{target_path_str}' is outside allowed workspace boundaries."
                except Exception as e:
                    message = str(e)
            else:
                message = "No path parameter provided."

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"success": success, "message": message}).encode("utf-8"))

        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def log_message(self, format: str, *args: Any) -> None:
        # Override to suppress noisy requests in CLI output unless error
        pass


def start_report_server(report_content: str, report_path: Path | None = None, port: int = 51731) -> int:
    """Start the lightweight report server on an available port, returning the port number."""
    ReportHTTPRequestHandler.report_content = report_content
    ReportHTTPRequestHandler.report_path = report_path

    # Try the requested port, or find an available one
    for p in range(port, port + 100):
        try:
            socketserver.TCPServer.allow_reuse_address = True
            server = socketserver.TCPServer(("127.0.0.1", p), ReportHTTPRequestHandler)

            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            logger.info("Report server started at http://127.0.0.1:%d", p)
            return p
        except OSError:
            continue

    raise OSError("Could not find an available port to start the report server.")
