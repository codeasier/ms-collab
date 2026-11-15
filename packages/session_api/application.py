"""HTTPS cookie/CSRF WSGI boundary. No default provider, listener or production data."""
from datetime import datetime, timezone
from email.utils import format_datetime
from http import HTTPStatus
import hmac
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

from packages.identity import AuthenticationError, AuthenticationUnavailable, Credential
from packages.login_state import LoginCoordinator
from packages.login_state.core import _secret
from packages.workspaces import PolicyError, StorageError
from packages.workspace_api import WorkspaceApplication
from .store import SessionPolicyStore, SessionRequest

_SESSION = "__Host-ms-collab-session"
_CSRF = "__Host-ms-collab-csrf"
_BROWSER = "__Host-ms-collab-login"
_COOKIE_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}\Z")


def _https_url(value, *, origin=False):
    if type(value) is not str or not 1 <= len(value) <= 2048 or any(not 33 <= ord(c) <= 126 for c in value):
        raise ValueError("explicit HTTPS URL required")
    uri = urlsplit(value)
    if (uri.scheme != "https" or not uri.hostname or uri.username is not None
            or uri.password is not None or uri.query or uri.fragment or "\\" in value
            or (origin and (uri.path or uri.netloc != uri.netloc.lower()))):
        raise ValueError("explicit HTTPS URL required")
    _ = uri.port
    return uri


def _cookies(environ):
    raw = environ.get("HTTP_COOKIE", "")
    if type(raw) is not str or len(raw) > 8192 or any(ord(c) < 32 or ord(c) >= 127 for c in raw):
        raise AuthenticationError()
    result = {}
    if not raw:
        return result
    for item in raw.split(";"):
        name, separator, value = item.strip().partition("=")
        if (not separator or not _COOKIE_NAME.fullmatch(name) or name in result or len(value) > 4096
                or any(c in value for c in ('"', "\\", ",", " "))):
            raise AuthenticationError()
        result[name] = value
    return result


def _credential(value):
    try:
        credential = Credential(value)
        _secret(credential)
        return credential
    except (ValueError, TypeError):
        raise AuthenticationError() from None


def _cookie(name, value, *, expires=None, now=None, readable=False):
    # Caller names are fixed constants; values are internally generated tokens.
    if value:
        _secret(value)
        if expires is None or now is None:
            raise AuthenticationUnavailable()
        age = int((expires - now).total_seconds())
        if age <= 0:
            raise AuthenticationError()
        text = value.value
        stamp = format_datetime(expires.astimezone(timezone.utc), usegmt=True)
    else:
        text, age, stamp = "", 0, "Thu, 01 Jan 1970 00:00:00 GMT"
    return ("Set-Cookie", f"{name}={text}; Path=/; Max-Age={age}; Expires={stamp}; Secure; "
            + ("" if readable else "HttpOnly; ") + ("SameSite=Strict" if readable else "SameSite=Lax"))


class _StaleSession(AuthenticationError):
    """Same-origin recovery only: clear unusable cookies, never log in silently."""


def _clear_cookies():
    return [_cookie(_SESSION, None), _cookie(_CSRF, None, readable=True), _cookie(_BROWSER, None)]


class SessionWorkspaceApplication(WorkspaceApplication):
    """Cookie version of existing workspace routes and closed-default login routes.

    Trusted fronting server must normalize HTTPS/Host and reject duplicate raw
    headers. Forwarded headers are never used as authority here. No CORS, default
    principal, PAT login, unsigned claims or session-token response body.
    """
    def __init__(self, *, sessions: SessionPolicyStore, origin: str, exchange=None,
                 authorization_endpoint: str | None = None, authorization_scopes=()):
        if type(sessions) is not SessionPolicyStore:
            raise ValueError("explicit session-policy transaction store required")
        uri = _https_url(origin, origin=True)
        if sessions.login.binding.callback_uri != origin + "/auth/callback":
            raise ValueError("callback must exactly match fixed HTTPS origin/path")
        if (exchange is None) != (authorization_endpoint is None):
            raise ValueError("provider and fixed authorization endpoint are required together")
        if authorization_endpoint is not None:
            _https_url(authorization_endpoint)
            if (type(authorization_scopes) is not tuple or not 1 <= len(authorization_scopes) <= 32
                    or any(type(s) is not str or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", s) is None
                           for s in authorization_scopes)
                    or len(set(authorization_scopes)) != len(authorization_scopes)):
                raise ValueError("explicit authorization scopes required")
        elif authorization_scopes:
            raise ValueError("unconfigured provider cannot select scopes")
        self._authorization_scopes = authorization_scopes
        self._sessions, self._origin, self._host = sessions, origin, uri.netloc
        self._coordinator = LoginCoordinator(sessions.login, exchange=exchange)
        self._authorization_endpoint = authorization_endpoint

    def _transport_security(self, environ, *, callback=False):
        if (environ.get("wsgi.url_scheme") != "https" or environ.get("HTTP_HOST") != self._host
                or environ.get("SCRIPT_NAME", "") or "HTTP_AUTHORIZATION" in environ):
            raise AuthenticationError()
        origin = environ.get("HTTP_ORIGIN")
        site = environ.get("HTTP_SEC_FETCH_SITE")
        if not callback:
            if origin is not None and origin != self._origin:
                raise AuthenticationError()
            if site is not None and site not in {"same-origin", "none"}:
                raise AuthenticationError()
            if environ.get("REQUEST_METHOD") not in {"GET", "HEAD"} and origin != self._origin:
                raise AuthenticationError()

    def _verify_request(self, environ):
        self._transport_security(environ)
        cookies = _cookies(environ)
        token = _credential(cookies.get(_SESSION, ""))
        csrf = None
        if environ.get("REQUEST_METHOD") not in {"GET", "HEAD"}:
            csrf = _credential(environ.get("HTTP_X_CSRF_TOKEN", ""))
            cookie_csrf = _credential(cookies.get(_CSRF, ""))
            if not hmac.compare_digest(csrf.value, cookie_csrf.value):
                raise AuthenticationError()
        return SessionRequest(token, csrf)

    def _authenticated_transaction(self, request, callback, *, write=False,
                                   with_connection=False, before_commit=None):
        return self._sessions.transaction(request, callback, write=write,
            with_connection=with_connection, before_commit=before_commit)

    def _reject_or_clear_stale(self, token):
        # Recovery only, never an authorization snapshot. Do not turn storage
        # failures into anonymous state or clear a valid session on bad CSRF.
        try:
            self._sessions.login.inspect_session(token)
        except AuthenticationError:
            raise _StaleSession() from None
        raise AuthenticationError()

    def _existing_token(self, cookies):
        if _SESSION not in cookies:
            raise AuthenticationError()
        try:
            return _credential(cookies[_SESSION])
        except AuthenticationError:
            raise _StaleSession() from None

    def _auth(self, environ):
        path, method = environ.get("PATH_INFO"), environ.get("REQUEST_METHOD")
        callback = path == "/auth/callback"
        self._transport_security(environ, callback=callback)
        cookies = _cookies(environ)
        query = environ.get("QUERY_STRING", "")
        if path == "/auth/start" and method == "POST" and not query:
            if environ.get("HTTP_X_MS_COLLAB_LOGIN") != "1":
                raise AuthenticationError()
            self._body(environ, set())
            if _SESSION in cookies:
                self._existing_token(cookies)
                request = self._verify_request(environ)
                try:
                    self._sessions.transaction(request, lambda policy, actor: None, write=True)
                except AuthenticationError:
                    self._reject_or_clear_stale(request.token)
            attempt = self._coordinator.begin()  # Unconfigured provider: no attempt or cookie.
            params = {"response_type": "code", "client_id": self._sessions.login.binding.audience,
                      "redirect_uri": self._sessions.login.binding.callback_uri,
                      "state": attempt.state.value, "code_challenge": attempt.code_challenge,
                      "code_challenge_method": "S256", "scope": " ".join(self._authorization_scopes)}
            target = self._authorization_endpoint + "?" + urlencode(params)
            now = self._sessions.login._clock()
            return 200, {"authorization_url": target}, [_cookie(_BROWSER, attempt.browser_binding, expires=attempt.expires_at, now=now)]
        if callback and method == "GET":
            self._coordinator._enabled()  # No GitCode capability bypass through callbacks.
            if type(query) is not str or len(query) > 16384 or re.search(r"%(?![0-9A-Fa-f]{2})", query):
                raise AuthenticationError()
            try:
                params = tuple(parse_qsl(query, keep_blank_values=True, strict_parsing=True,
                                         max_num_fields=4, encoding="utf-8", errors="strict"))
            except (ValueError, UnicodeError):
                raise AuthenticationError() from None
            browser = _credential(cookies.get(_BROWSER, ""))
            old = _credential(cookies[_SESSION]) if _SESSION in cookies else None
            session = self._coordinator.complete(parameters=params, browser_binding=browser,
                callback_uri=self._sessions.login.binding.callback_uri, previous_session=old)
            now = self._sessions.login._clock()
            return 303, {}, [("Location", "/"), _cookie(_BROWSER, None),
                _cookie(_SESSION, session.token, expires=session.expires_at, now=now),
                _cookie(_CSRF, session.csrf, expires=session.expires_at, now=now, readable=True)]
        if path == "/auth/logout" and method == "POST" and not query:
            self._body(environ, set())
            self._existing_token(cookies)
            request = self._verify_request(environ)
            try:
                self._sessions.login.logout(request.token, request.csrf)
            except AuthenticationError:
                self._reject_or_clear_stale(request.token)
            return 200, {"logged_out": True}, _clear_cookies()
        if path == "/auth/session" and method == "GET" and not query:
            request = self._verify_request(environ)
            result = self._sessions.transaction(request, lambda policy, actor: {"principal_id": actor})
            return 200, result, []
        raise PolicyError(404, "not_found")

    def __call__(self, environ, start_response):
        extra = []
        try:
            path = environ.get("PATH_INFO", "")
            if type(path) is str and path.startswith("/auth/"):
                status, result, extra = self._auth(environ)
            else:
                result = super()._dispatch(environ)
                status = 200
            body = {"data": result}
        except _StaleSession:
            status, body, extra = 401, {"error": "authentication_required"}, _clear_cookies()
        except AuthenticationError:
            status, body = 401, {"error": "authentication_required"}
        except AuthenticationUnavailable:
            status, body = 503, {"error": "authentication_unavailable"}
        except StorageError:
            status, body = 503, {"error": "storage_unavailable"}
        except PolicyError as error:
            status, body = error.status, {"error": error.code}
        except Exception:
            status, body = 500, {"error": "internal_error"}
        payload = json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        headers = [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(payload))),
                   ("Cache-Control", "no-store"), ("Pragma", "no-cache"), ("Referrer-Policy", "no-referrer"),
                   ("X-Content-Type-Options", "nosniff"), ("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"),
                   ("Vary", "Cookie, Origin")] + extra
        start_response(f"{status} {HTTPStatus(status).phrase}", headers)
        return [payload]
