# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Static HTTPS shell over the configured unified session host, without fixtures."""
from pathlib import Path

from packages.application_host import ApplicationHost
from packages.identity import AuthenticationError

_ROOT = Path(__file__).resolve().parents[2] / "frontend" / "gui-smoke"
_FILES = {"/": ("index.html", "text/html"), "/gui-smoke/": ("index.html", "text/html"),
          "/gui-smoke/app.mjs": ("app.mjs", "text/javascript"),
          "/gui-smoke/client.mjs": ("client.mjs", "text/javascript"),
          "/gui-smoke/style.css": ("style.css", "text/css")}


class GuiSmokeUI:
    """Explicit composition only. Static shell is public; every data read is not.

    The fixed host supplies HTTPS origin, reviewed login configuration and the
    exact same session/policy transaction boundary. No token field, bearer bridge,
    default user, mock provider, listener or external runner exists here.
    """
    def __init__(self, *, host):
        if type(host) is not ApplicationHost or "gui-smoke" not in host._config.modules:
            raise ValueError("configured unified GUI smoke session host required")
        self._host = host

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if type(path) is str and (path.startswith("/auth/") or path.startswith("/api/")):
            # Includes the deliberate cross-site OAuth callback exception. Do not
            # pre-filter it as a static request or change/strip any session headers.
            return self._host(environ, start_response)
        headers = [("Cache-Control", "no-store"), ("Pragma", "no-cache"),
                   ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
                   ("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"),
                   ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
                   ("Cross-Origin-Opener-Policy", "same-origin"), ("Vary", "Cookie, Origin")]
        def respond(status, body, media="text/plain"):
            start_response(status, headers + [("Content-Type", media + "; charset=utf-8"),
                                              ("Content-Length", str(len(body)))])
            return [b"" if environ.get("REQUEST_METHOD") == "HEAD" else body]
        try:
            # OAuth's redirect chain retains cross-site Fetch Metadata at `/`.
            # Only non-sensitive top-level HTML navigation may cross origins;
            # iframe/subresource/API reads retain the normal stricter boundary.
            public_navigation = (type(path) is str and path in {"/", "/gui-smoke/"}
                and environ.get("REQUEST_METHOD") == "GET"
                and not environ.get("QUERY_STRING", "")
                and environ.get("HTTP_SEC_FETCH_MODE") == "navigate"
                and environ.get("HTTP_SEC_FETCH_DEST") == "document")
            self._host._boundary._transport_security(environ, callback=public_navigation)
        except AuthenticationError:
            return respond("401 Unauthorized", b"same_origin_https_required")
        if (type(path) is not str or path not in _FILES or environ.get("QUERY_STRING", "")
                or environ.get("REQUEST_METHOD") not in {"GET", "HEAD"}):
            return respond("404 Not Found", b"not_found")
        filename, media = _FILES[path]
        return respond("200 OK", (_ROOT / filename).read_bytes(), media)
