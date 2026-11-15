"""No default provider, identity, listener or synthetic authentication."""
from pathlib import Path
from urllib.parse import urlsplit

from .application import ArtifactAdministrationAPI

_ROOT = Path(__file__).resolve().parents[2] / "frontend" / "artifact-admin"
_FILES = {"/artifact-admin/": ("index.html", "text/html"),
          "/artifact-admin/entry.mjs": ("entry.mjs", "text/javascript"),
          "/artifact-admin/browser.mjs": ("browser.mjs", "text/javascript"),
          "/artifact-admin/client.mjs": ("client.mjs", "text/javascript"),
          "/artifact-admin/style.css": ("style.css", "text/css")}


class ArtifactAdministrationUI:
    """Trusted composition only. TLS/proxy/limits and real verifier required.

    `origin` is deployment configuration, never a request Host-derived origin.
    Numeric IPv4 loopback HTTP is permitted solely for isolated acceptance tests.
    No credentials or data are embedded in static files. Cookie auth is unsupported.
    """
    def __init__(self, *, api, origin):
        if type(api) is not ArtifactAdministrationAPI:
            raise ValueError("configured artifact administration API required")
        try:
            parsed = urlsplit(origin)
            port = parsed.port
        except (ValueError, TypeError):
            raise ValueError("explicit HTTPS or loopback origin required") from None
        if (type(origin) is not str or parsed.username or parsed.password or parsed.path
                or parsed.query or parsed.fragment or not parsed.hostname
                or any(c.isspace() for c in origin)
                or origin != f"{parsed.scheme}://{parsed.netloc}"
                or not (parsed.scheme == "https" or
                        (parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and port))):
            raise ValueError("explicit HTTPS or loopback origin required")
        self._origin, self._host, self._scheme = origin, parsed.netloc, parsed.scheme
        self._api = api

    def __call__(self, environ, start_response):
        headers = [("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                   ("Referrer-Policy", "no-referrer"),
                   ("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")]
        def respond(status, body, media="text/plain; charset=utf-8"):
            start_response(status, headers + [("Content-Type", media), ("Content-Length", str(len(body)))])
            return [b"" if environ.get("REQUEST_METHOD") == "HEAD" else body]
        if (environ.get("HTTP_HOST") != self._host or environ.get("wsgi.url_scheme") != self._scheme
                or environ.get("HTTP_ORIGIN", self._origin) != self._origin
                or environ.get("HTTP_SEC_FETCH_SITE") == "cross-site"):
            return respond("403 Forbidden", b"same_origin_required")
        path = environ.get("PATH_INFO", "")
        if path.startswith("/api/"):
            return self._api(environ, start_response)
        if (environ.get("REQUEST_METHOD") not in {"GET", "HEAD"}
                or environ.get("QUERY_STRING") or path not in _FILES):
            return respond("404 Not Found", b"not_found")
        filename, media = _FILES[path]
        return respond("200 OK", (_ROOT / filename).read_bytes(), media + "; charset=utf-8")
