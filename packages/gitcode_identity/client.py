"""Documented GitCode OAuth wire operations; deliberately not a login verifier.

Only trusted server code may call exchange_identity after validating its callback
transaction. No arbitrary access token can be promoted to an application identity.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.client import HTTPSConnection
import json
import math
import re
import secrets
import ssl
from typing import Callable, Protocol
from urllib.parse import urlencode, urlsplit

from packages.identity import AuthenticationError, AuthenticationUnavailable, Credential

AUTHORIZE_URL = "https://gitcode.com/oauth/authorize"
TOKEN_URL = "https://gitcode.com/oauth/token"
USER_URL = "https://api.gitcode.com/api/v5/user"
MAX_BODY = 65536


def _ascii(value, limit=8192):
    if (type(value) is not str or not value or len(value) > limit
            or any(not 33 <= ord(char) <= 126 for char in value)):
        raise ValueError("invalid protocol field")


def _instant(value):
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware instant required")


@dataclass(frozen=True)
class GitCodeConfig:
    """Explicit trusted configuration, never supplied by an HTTP request."""
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str
    scopes: tuple[str, ...]
    max_token_lifetime: timedelta = timedelta(days=31)

    def __post_init__(self):
        _ascii(self.client_id, 256)
        _ascii(self.client_secret)
        _ascii(self.redirect_uri, 2048)
        uri = urlsplit(self.redirect_uri)
        if (uri.scheme != "https" or not uri.hostname or uri.username is not None
                or uri.password is not None or uri.query or uri.fragment
                or "\\" in self.redirect_uri):
            raise ValueError("explicit HTTPS callback without query is required")
        # Accessing port also rejects malformed/out-of-range ports.
        _ = uri.port
        if (type(self.scopes) is not tuple or not 1 <= len(self.scopes) <= 32
                or any(type(s) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", s) is None
                       for s in self.scopes)
                or len(set(self.scopes)) != len(self.scopes)):
            raise ValueError("explicit unique scopes are required")
        if (type(self.max_token_lifetime) is not timedelta
                or not timedelta(0) < self.max_token_lifetime <= timedelta(days=366)):
            raise ValueError("invalid lifetime limit")


@dataclass(frozen=True)
class Request:
    """Secret-bearing request. Never serialize, log or inspect exception locals."""
    method: str
    url: str = field(repr=False)
    headers: dict[str, str] = field(repr=False)
    body: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Response:
    status: int
    content_type: str
    body: bytes = field(repr=False)


class Transport(Protocol):
    def send(self, request: Request) -> Response: ...


class HTTPSOnlyTransport:
    """Direct system-CA TLS, fixed hosts, no redirects/proxies/retries.

    Timeout is per socket operation, not an end-to-end deadline. Deployment must
    also enforce a request deadline, concurrency limit and outbound firewall.
    """
    def __init__(self, *, timeout: float = 5.0):
        if type(timeout) not in (float, int) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("invalid timeout")
        self._timeout = float(timeout)

    def send(self, request: Request) -> Response:
        connection = None
        try:
            uri = urlsplit(request.url)
            if (uri.scheme != "https" or uri.port is not None or uri.username is not None
                    or uri.password is not None or uri.fragment):
                raise ValueError("untrusted endpoint")
            if not ((uri.hostname == "gitcode.com" and uri.path == "/oauth/token"
                     and request.method == "POST") or
                    (uri.hostname == "api.gitcode.com" and uri.path == "/api/v5/user"
                     and request.method == "GET" and not uri.query)):
                raise ValueError("untrusted endpoint")
            connection = HTTPSConnection(uri.hostname, timeout=self._timeout,
                                         context=ssl.create_default_context())
            target = uri.path + ("?" + uri.query if uri.query else "")
            connection.request(request.method, target, body=request.body, headers=request.headers)
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                # Do not read, follow or include error bodies/Location in errors.
                return Response(response.status, "", b"")
            body = response.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise ValueError("response exceeds limit")
            return Response(response.status, response.getheader("Content-Type", ""), body)
        except Exception:
            raise AuthenticationUnavailable() from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


@dataclass(frozen=True)
class GitCodeIdentityEvidence:
    """Observed resource owner, NOT VerifiedIdentity, a session or authorization.

    This carries no issuer/audience proof. The documented OAuth response does not
    provide OIDC claims or introspection; a login coordinator still owns binding.
    """
    subject: str
    observed_at: datetime
    grant_expires_at: datetime


class GitCodeOAuthClient:
    """Protocol client for a single explicitly configured OAuth application.

    NO verify(Credential) method: this cannot be plugged into Provider/WSGI as an
    arbitrary bearer verifier. A callback coordinator with one-use transaction
    and browser binding must wrap it, after provider security gaps are resolved.
    """
    def __init__(self, config: GitCodeConfig, *, transport: Transport | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        if type(config) is not GitCodeConfig or not callable(clock):
            raise ValueError("explicit configuration and clock required")
        self._config = config
        self._transport = HTTPSOnlyTransport() if transport is None else transport
        if not callable(getattr(self._transport, "send", None)):
            raise ValueError("trusted transport required")
        self._clock = clock

    def authorization_url(self, state: str, *, prompt: str | None = None) -> str:
        """Format only: caller must generate, store and bind unguessable state."""
        if type(state) is not str or re.fullmatch(r"[A-Za-z0-9_-]{32,256}", state) is None:
            raise ValueError("opaque high-entropy state required")
        if prompt not in (None, "login", "consent"):
            raise ValueError("unsupported prompt")
        parameters = {"client_id": self._config.client_id,
                      "redirect_uri": self._config.redirect_uri, "response_type": "code",
                      "scope": " ".join(self._config.scopes), "state": state}
        if prompt is not None:
            parameters["prompt"] = prompt
        return AUTHORIZE_URL + "?" + urlencode(parameters)

    def _now(self):
        try:
            now = self._clock()
            _instant(now)
            return now
        except Exception:
            raise AuthenticationUnavailable() from None

    def _json(self, request: Request, *, token_endpoint=False):
        try:
            response = self._transport.send(request)
            if (type(response) is not Response or type(response.status) is not int
                    or type(response.body) is not bytes or len(response.body) > MAX_BODY):
                raise ValueError("invalid transport response")
            if response.status in ((400, 401, 403) if token_endpoint else (401, 403)):
                raise AuthenticationError()
            if response.status != 200:
                raise ValueError("unexpected status")
            if (type(response.content_type) is not str or
                    response.content_type.split(";", 1)[0].strip().lower() != "application/json"):
                raise ValueError("invalid content type")

            def pairs(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise ValueError("duplicate JSON key")
                    result[key] = value
                return result

            def constant(_):
                raise ValueError("nonfinite JSON constant")

            payload = json.loads(response.body.decode("utf-8"), object_pairs_hook=pairs,
                                 parse_constant=constant)
            if type(payload) is not dict:
                raise ValueError("object required")
            return payload
        except AuthenticationError:
            raise AuthenticationError() from None
        except Exception:
            raise AuthenticationUnavailable() from None

    def exchange_identity(self, code: Credential) -> GitCodeIdentityEvidence:
        """Exchange a validated callback's code; do not expose directly as login.

        Callback state/redirect/browser binding and single-use handling MUST be
        completed outside this protocol client. No credential or profile is kept.
        """
        if type(code) is not Credential:
            raise AuthenticationError()
        try:
            _ascii(code.value)
        except ValueError:
            raise AuthenticationError() from None
        started = self._now()
        query = urlencode({"grant_type": "authorization_code", "code": code.value,
                           "client_id": self._config.client_id})
        boundary = "mscollab" + secrets.token_hex(24)
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="client_secret"\r\n'
                f'\r\n{self._config.client_secret}\r\n--{boundary}--\r\n').encode("ascii")
        payload = self._json(Request("POST", TOKEN_URL + "?" + query,
                                    {"Accept": "application/json", "Content-Type":
                                     "multipart/form-data; boundary=" + boundary}, body),
                             token_endpoint=True)
        try:
            token = payload["access_token"]
            _ascii(token)
            ttl = payload["expires_in"]
            if (type(ttl) is not int or not 0 < ttl <= self._config.max_token_lifetime.total_seconds()):
                raise ValueError("invalid lifetime")
            created = payload["created_at"]
            if type(created) is not str:
                raise ValueError("invalid grant creation time")
            issued = datetime.fromisoformat(created.replace("Z", "+00:00"))
            _instant(issued)
            scope = payload["scope"]
            if type(scope) is not str or len(scope) > 4096:
                raise ValueError("missing scopes")
            if not set(self._config.scopes).issubset(scope.split()):
                raise AuthenticationError()
            now = self._now()
            expires = issued + timedelta(seconds=ttl)
            if not started <= now or not issued <= now < expires:
                raise AuthenticationError()
        except AuthenticationError:
            raise AuthenticationError() from None
        except Exception:
            raise AuthenticationUnavailable() from None
        user = self._json(Request("GET", USER_URL,
                                  {"Authorization": "Bearer " + token, "Accept": "application/json"}))
        try:
            subject = user["id"]
            if type(subject) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", subject) is None:
                raise ValueError("invalid stable subject")
            observed = self._now()
            if not now <= observed < expires:
                raise AuthenticationError()
            return GitCodeIdentityEvidence(subject, observed, expires)
        except AuthenticationError:
            raise AuthenticationError() from None
        except Exception:
            raise AuthenticationUnavailable() from None
