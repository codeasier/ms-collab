"""Server-side OAuth transaction/session primitives, not enabled GitCode login.

No HTTP route, provider implementation or identity-to-workspace authorization is
included. Trusted adapters must prove code binding before returning an assertion.
"""
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import base64
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Protocol
from urllib.parse import quote, urlsplit

from packages.identity import AuthenticationError, AuthenticationUnavailable, Credential, VerifiedIdentity

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_APP = 0x4D534C53
_VERSION = 1
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_SCHEMA = (
    "CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1), binding TEXT NOT NULL, clock INTEGER NOT NULL) STRICT",
    "CREATE TABLE attempts (state TEXT PRIMARY KEY, browser TEXT NOT NULL, verifier TEXT NOT NULL, created INTEGER NOT NULL, expires INTEGER NOT NULL) STRICT",
    "CREATE TABLE sessions (token TEXT PRIMARY KEY, csrf TEXT NOT NULL, subject TEXT NOT NULL, created INTEGER NOT NULL, expires INTEGER NOT NULL) STRICT",
)


def _micros(now):
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("aware time required")
    return (now.astimezone(timezone.utc) - _EPOCH) // timedelta(microseconds=1)


def _datetime(value):
    if type(value) is not int:
        raise ValueError("invalid timestamp")
    return _EPOCH + timedelta(microseconds=value)


def _digest(value):
    return sha256(value.encode("ascii")).hexdigest()


def _secret(value):
    if type(value) is not Credential or _TOKEN.fullmatch(value.value) is None:
        raise AuthenticationError()
    return value.value


def _random():
    return secrets.token_urlsafe(32)


def s256_challenge(verifier: Credential) -> str:
    """RFC 7636 S256 calculation, not proof of any provider's PKCE support."""
    if type(verifier) is not Credential or re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier.value) is None:
        raise ValueError("invalid PKCE verifier")
    return base64.urlsafe_b64encode(sha256(verifier.value.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class LoginBinding:
    issuer: str
    audience: str
    callback_uri: str
    transaction_ttl: timedelta = timedelta(minutes=5)
    session_ttl: timedelta = timedelta(minutes=30)
    max_pending: int = 1024
    max_sessions: int = 4096

    def __post_init__(self):
        # Reuse the identity boundary's strict issuer/audience validation.
        VerifiedIdentity(self.issuer, "binding-validation", self.audience, _EPOCH, _EPOCH + timedelta(seconds=1))
        if (type(self.callback_uri) is not str or len(self.callback_uri) > 2048
                or any(not 33 <= ord(c) <= 126 for c in self.callback_uri)):
            raise ValueError("invalid callback")
        uri = urlsplit(self.callback_uri)
        if (uri.scheme != "https" or not uri.hostname or uri.username is not None
                or uri.password is not None or uri.query or uri.fragment or "\\" in self.callback_uri):
            raise ValueError("explicit HTTPS callback required")
        _ = uri.port
        for value, limit in ((self.transaction_ttl, timedelta(minutes=10)),
                             (self.session_ttl, timedelta(hours=1))):
            if type(value) is not timedelta or not timedelta(seconds=1) <= value <= limit:
                raise ValueError("invalid lifetime")
        for value in (self.max_pending, self.max_sessions):
            if type(value) is not int or not 1 <= value <= 100000:
                raise ValueError("invalid capacity")

    def fingerprint(self):
        return sha256(json.dumps([self.issuer, self.audience, self.callback_uri,
            self.transaction_ttl.total_seconds(), self.session_ttl.total_seconds(),
            self.max_pending, self.max_sessions], separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class LoginAttempt:
    state: Credential = field(repr=False)
    browser_binding: Credential = field(repr=False)
    code_challenge: str
    expires_at: datetime
    code_challenge_method: str = "S256"


@dataclass(frozen=True)
class NewSession:
    token: Credential = field(repr=False)
    csrf: Credential = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True)
class SessionSnapshot:
    """Current identity snapshot, never authority for a workspace operation."""
    issuer: str
    subject: str
    audience: str
    created_at: datetime
    expires_at: datetime


class BoundCodeExchange(Protocol):
    def exchange_bound_code(self, *, code: Credential, verifier: Credential,
                            callback_uri: str) -> VerifiedIdentity:
        """Trusted adapter must prove S256-bound code, issuer/client/callback/time.

        Must reject missing/wrong verifier and unbound-code downgrade, not merely
        send optional parameters that an upstream may silently ignore. Assertion
        issue time represents fresh verification. No implementation ships here.
        """
        ...


class SQLiteLoginStore:
    """Explicit, private local runtime store; no migration or automatic creation.

    All changes use BEGIN IMMEDIATE. Pending codes are consumed before external
    exchange, and failed exchanges are not retryable. State/browser/session/CSRF
    credentials are stored as hashes; the short-lived PKCE verifier is plaintext.
    DB, journal and backups therefore remain sensitive. No erasure is promised.
    """
    def __init__(self, path, *, binding: LoginBinding, clock=lambda: datetime.now(timezone.utc), timeout=5.0):
        if type(binding) is not LoginBinding or not callable(clock):
            raise ValueError("trusted binding and clock required")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 30:
            raise ValueError("invalid timeout")
        self.binding, self._clock, self._timeout = binding, clock, timeout
        self._path = Path(path).absolute()
        self._uri = "file:" + quote(str(self._path), safe="/") + "?mode=rw"
        self._run(lambda db, now: None)

    @classmethod
    def initialize(cls, path, *, binding, clock=lambda: datetime.now(timezone.utc), timeout=5.0):
        # Validate all inputs before allocating a private new file.
        if type(binding) is not LoginBinding or not callable(clock):
            raise ValueError("trusted binding and clock required")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 30:
            raise ValueError("invalid timeout")
        try:
            now = _micros(clock())
            path = Path(path).absolute()
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            uri = "file:" + quote(str(path), safe="/") + "?mode=rw"
            with closing(sqlite3.connect(uri, uri=True, timeout=timeout)) as db:
                db.execute("BEGIN IMMEDIATE")
                for sql in _SCHEMA:
                    db.execute(sql)
                db.execute(f"PRAGMA application_id={_APP}")
                db.execute(f"PRAGMA user_version={_VERSION}")
                db.execute("INSERT INTO metadata VALUES(1,?,?)", (binding.fingerprint(), now))
                db.commit()
        except Exception:
            # Keep a failed new file for diagnosis; never replace/delete old data.
            raise AuthenticationUnavailable() from None
        return cls(path, binding=binding, clock=clock, timeout=timeout)

    def _now(self):
        try:
            return _micros(self._clock())
        except Exception:
            raise AuthenticationUnavailable() from None

    def _run(self, operation, *, validate=None):
        try:
            with closing(sqlite3.connect(self._uri, uri=True, timeout=self._timeout, isolation_level=None)) as db:
                db.execute("PRAGMA trusted_schema=OFF")
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                try:
                    if (db.execute("PRAGMA application_id").fetchone()[0] != _APP
                            or db.execute("PRAGMA user_version").fetchone()[0] != _VERSION):
                        raise AuthenticationUnavailable()
                    row = db.execute("SELECT binding,clock FROM metadata WHERE id=1").fetchone()
                    now = self._now()
                    if row is None or row[0] != self.binding.fingerprint() or type(row[1]) is not int or now < row[1]:
                        raise AuthenticationUnavailable()
                    # Security time is not business data: a denied operation must
                    # not erase an observed expiry and later revive a session.
                    observed = now
                    db.execute("SAVEPOINT business")
                    try:
                        result = operation(db, now)
                        ended = self._now()
                        observed = max(observed, ended)
                        if ended < now:
                            raise AuthenticationUnavailable()
                        if validate is not None:
                            validate(result, ended)
                        db.execute("RELEASE business")
                    except BaseException:
                        db.execute("ROLLBACK TO business")
                        db.execute("RELEASE business")
                        db.execute("UPDATE metadata SET clock=? WHERE id=1", (observed,))
                        db.commit()
                        raise
                    db.execute("UPDATE metadata SET clock=? WHERE id=1", (observed,))
                    db.commit()
                    return result
                except BaseException:
                    db.rollback()
                    raise
        except AuthenticationError:
            raise AuthenticationError() from None
        except Exception:
            raise AuthenticationUnavailable() from None

    @staticmethod
    def _fresh(result, now):
        if not now < _micros(result.expires_at):
            raise AuthenticationError()

    @staticmethod
    def _clean(db, now):
        db.execute("DELETE FROM attempts WHERE expires<=?", (now,))
        db.execute("DELETE FROM sessions WHERE expires<=?", (now,))

    def begin(self) -> LoginAttempt:
        """Prepare local state only, does not contact or enable any provider.

        Each attempt gets a fresh browser binding; HTTP integration must rotate
        its pre-login cookie. An older tab's cookie may no longer match.
        """
        def operation(db, now):
            self._clean(db, now)
            if db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] >= self.binding.max_pending:
                raise AuthenticationUnavailable()
            state, browser, verifier = _random(), _random(), _random()
            expiry = now + self.binding.transaction_ttl // timedelta(microseconds=1)
            db.execute("INSERT INTO attempts VALUES(?,?,?,?,?)", (_digest(state), _digest(browser), verifier, now, expiry))
            return LoginAttempt(Credential(state), Credential(browser), s256_challenge(Credential(verifier)), _datetime(expiry))
        return self._run(operation, validate=self._fresh)

    def _consume(self, state, browser, callback_uri):
        state, browser = _secret(state), _secret(browser)
        if type(callback_uri) is not str or callback_uri != self.binding.callback_uri:
            raise AuthenticationError()
        def operation(db, now):
            row = db.execute("SELECT browser,verifier,created,expires FROM attempts WHERE state=?", (_digest(state),)).fetchone()
            if row is None or type(row[0]) is not str or not hmac.compare_digest(row[0], _digest(browser)):
                raise AuthenticationError()
            verifier, created, expires = row[1:]
            if (type(verifier) is not str or _TOKEN.fullmatch(verifier) is None
                    or type(created) is not int or type(expires) is not int
                    or not created <= now < expires
                    or expires - created != self.binding.transaction_ttl // timedelta(microseconds=1)):
                raise AuthenticationError()
            db.execute("DELETE FROM attempts WHERE state=?", (_digest(state),))
            return Credential(verifier), created, expires
        def fresh(result, now):
            if not now < result[2]:
                raise AuthenticationError()
        return self._run(operation, validate=fresh)

    def _issue(self, assertion, started, deadline, previous):
        if type(assertion) is not VerifiedIdentity or (assertion.issuer, assertion.audience) != (self.binding.issuer, self.binding.audience):
            raise AuthenticationError()
        prior = _digest(_secret(previous)) if previous is not None else None
        def operation(db, now):
            issued, grant_end = _micros(assertion.issued_at), _micros(assertion.expires_at)
            if not started <= issued <= now < min(deadline, grant_end):
                raise AuthenticationError()
            expiry = min(grant_end, now + self.binding.session_ttl // timedelta(microseconds=1))
            self._clean(db, now)
            if prior is not None:
                db.execute("DELETE FROM sessions WHERE token=?", (prior,))
            if db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] >= self.binding.max_sessions:
                raise AuthenticationUnavailable()
            token, csrf = _random(), _random()
            db.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (_digest(token), _digest(csrf), assertion.subject, now, expiry))
            return NewSession(Credential(token), Credential(csrf), _datetime(expiry))
        def fresh(result, now):
            self._fresh(result, now)
            if not now < deadline:
                raise AuthenticationError()
        return self._run(operation, validate=fresh)

    def _session(self, db, now, token, csrf=None, *, schema="main"):
        # Fixed trusted schema identifiers only, never request-selected SQL.
        if schema not in {"main", "login"}:
            raise ValueError("unsupported session schema")
        row = db.execute(f"SELECT csrf,subject,created,expires FROM {schema}.sessions WHERE token=?", (_digest(_secret(token)),)).fetchone()
        if row is None:
            raise AuthenticationError()
        csrf_hash, subject, created, expires = row
        if csrf is not None and (type(csrf_hash) is not str or not hmac.compare_digest(csrf_hash, _digest(_secret(csrf)))):
            raise AuthenticationError()
        if (type(created) is not int or type(expires) is not int or not created <= now < expires
                or not 0 < expires - created <= self.binding.session_ttl // timedelta(microseconds=1)):
            raise AuthenticationError()
        # Validate even stored identity fields rather than coercing corrupt data.
        VerifiedIdentity(self.binding.issuer, subject, self.binding.audience, _datetime(created), _datetime(expires))
        return SessionSnapshot(self.binding.issuer, subject, self.binding.audience, _datetime(created), _datetime(expires))

    def inspect_session(self, token: Credential) -> SessionSnapshot:
        """Fresh snapshot only. No workspace authorization or cached authority."""
        return self._run(lambda db, now: self._session(db, now, token), validate=self._fresh)

    def check_csrf(self, token: Credential, csrf: Credential) -> SessionSnapshot:
        """Check token pair, not HTTP method/origin and not permission to mutate."""
        _secret(csrf)
        return self._run(lambda db, now: self._session(db, now, token, csrf), validate=self._fresh)

    def logout(self, token: Credential, csrf: Credential):
        _secret(csrf)
        def operation(db, now):
            self._session(db, now, token, csrf)
            db.execute("DELETE FROM sessions WHERE token=?", (_digest(_secret(token)),))
        self._run(operation)

    def revoke_subject(self, subject: str):
        """Delete existing sessions, not an account-disable or login-generation gate.

        An in-flight/new verified login may create another session. Current
        account activity and workspace policy must be checked separately.
        Trusted administration only; never expose a request-selected subject.
        """
        VerifiedIdentity(self.binding.issuer, subject, self.binding.audience, _EPOCH, _EPOCH + timedelta(seconds=1))
        self._run(lambda db, now: db.execute("DELETE FROM sessions WHERE subject=?", (subject,)).close())


class LoginCoordinator:
    """Closed without a separately reviewed bound-code provider adapter.

    GitCodeOAuthClient intentionally cannot satisfy this interface. No config
    boolean certifies provider capability; only a real adapter can prove it.
    """
    def __init__(self, store: SQLiteLoginStore, *, exchange: BoundCodeExchange | None = None):
        if type(store) is not SQLiteLoginStore:
            raise ValueError("explicit initialized login store required")
        if exchange is not None and not callable(getattr(exchange, "exchange_bound_code", None)):
            raise ValueError("bound-code adapter required")
        self._store, self._exchange = store, exchange

    def _enabled(self):
        if self._exchange is None:
            raise AuthenticationUnavailable()

    def begin(self):
        self._enabled()
        return self._store.begin()

    def complete(self, *, parameters, browser_binding: Credential, callback_uri: str,
                 previous_session: Credential | None = None) -> NewSession:
        self._enabled()
        # Preserve raw parsed pairs. Dict conversion would erase duplicates.
        if type(parameters) is not tuple or not 1 <= len(parameters) <= 4:
            raise AuthenticationError()
        fields = {}
        for pair in parameters:
            if (type(pair) is not tuple or len(pair) != 2 or type(pair[0]) is not str
                    or type(pair[1]) is not str or len(pair[1]) > 8192 or pair[0] in fields):
                raise AuthenticationError()
            fields[pair[0]] = pair[1]
        valid_code = set(fields) == {"state", "code"}
        valid_error = (set(fields) <= {"state", "error", "error_description", "error_uri"}
                       and {"state", "error"} <= set(fields))
        if not valid_code and not valid_error:
            raise AuthenticationError()
        try:
            state = Credential(fields["state"])
            code = Credential(fields["code"]) if valid_code else None
        except ValueError:
            raise AuthenticationError() from None
        # Consume before exchange; provider errors/failure/timeouts cannot replay.
        verifier, started, deadline = self._store._consume(state, browser_binding, callback_uri)
        if valid_error:
            raise AuthenticationError()
        try:
            assertion = self._exchange.exchange_bound_code(code=code, verifier=verifier,
                                                           callback_uri=self._store.binding.callback_uri)
        except AuthenticationError:
            raise AuthenticationError() from None
        except Exception:
            raise AuthenticationUnavailable() from None
        return self._store._issue(assertion, started, deadline, previous_session)
