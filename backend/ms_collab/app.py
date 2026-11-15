"""Loopback-only, read-only demonstration of the future composition boundary.

No provider credentials, production databases, filesystem download API or auth
provider are supported. Do not deploy this http.server-based demo on a network.
"""
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .catalog import DEMO_USER, MEMBERSHIPS, OVERVIEWS, WORKSPACES, visible_modules, visible_workspaces

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.mjs": ("app.mjs", "text/javascript; charset=utf-8"),
    "/navigation.mjs": ("navigation.mjs", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "ms-collab-demo"
    sys_version = ""

    def log_message(self, _format, *_args):
        # Do not echo paths/headers that a caller could populate with secrets.
        pass

    def _reply(self, status, data, content_type="application/json; charset=utf-8"):
        body = json.dumps(data, ensure_ascii=False).encode() if isinstance(data, dict) else data
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _local_request(self):
        # Exact numeric host and port prevent DNS-rebinding / remote-site access.
        expected = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get_all("Host", []) != [expected]:
            self._reply(HTTPStatus.FORBIDDEN, {"error": "local_demo_only"})
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{expected}":
            self._reply(HTTPStatus.FORBIDDEN, {"error": "local_demo_only"})
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self._reply(HTTPStatus.FORBIDDEN, {"error": "local_demo_only"})
            return False
        return True

    def do_GET(self):
        if not self._local_request():
            return
        try:
            target = urlsplit(self.path)
        except ValueError:
            self._reply(HTTPStatus.BAD_REQUEST, {"error": "unsupported_request"})
            return
        # No URL-provided credentials, mode switches or hidden file serving.
        if target.scheme or target.netloc or target.query or target.fragment:
            self._reply(HTTPStatus.BAD_REQUEST, {"error": "unsupported_request"})
            return
        path = target.path
        if path in STATIC_FILES:
            filename, media_type = STATIC_FILES[path]
            self._reply(HTTPStatus.OK, (FRONTEND / filename).read_bytes(), media_type)
            return
        if path == "/healthz":
            self._reply(HTTPStatus.OK, {"status": "ok", "mode": "synthetic-demo"})
            return
        if path == "/api/workspaces":
            self._reply(HTTPStatus.OK, {"mode": "synthetic-demo", "user": DEMO_USER,
                                        "items": visible_workspaces()})
            return
        parts = path.split("/")
        if len(parts) == 5 and parts[1:3] == ["api", "workspaces"]:
            workspace_id, resource = parts[3:]
            # Membership is enforced before any resource resolution. Return the
            # same response for an inaccessible and a nonexistent workspace.
            if workspace_id not in WORKSPACES or workspace_id not in MEMBERSHIPS:
                self._reply(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            if resource == "modules":
                self._reply(HTTPStatus.OK, {"workspace_id": workspace_id,
                                            "items": visible_modules(workspace_id)})
                return
            if resource == "overview" and "overview.read" in MEMBERSHIPS[workspace_id]:
                if "overview" in WORKSPACES[workspace_id].modules:
                    self._reply(HTTPStatus.OK, {"workspace_id": workspace_id,
                                                "data": OVERVIEWS[workspace_id]})
                    return
        self._reply(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    do_HEAD = do_GET

    def _read_only(self):
        if self._local_request():
            self._reply(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "read_only_demo"})

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _read_only


def create_server(port=8000):
    """Bind only IPv4 loopback; port 0 is useful for isolated tests."""
    if not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    return ThreadingHTTPServer(("127.0.0.1", port), DemoHandler)
