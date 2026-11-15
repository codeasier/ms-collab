"""Server-side OAuth transaction/session primitives, not enabled GitCode login.

No HTTP route or provider implementation is included. Optional policy composition
binds issuance to current principal authority. Trusted adapters must prove code
binding before returning an assertion.
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
_VERSION = 2
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_SCHEMA_V1 = (
    "CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1), binding TEXT NOT NULL, clock INTEGER NOT NULL) STRICT",
    "CREATE TABLE attempts (state TEXT PRIMARY KEY, browser TEXT NOT NULL, verifier TEXT NOT NULL, created INTEGER NOT NULL, expires INTEGER NOT NULL) STRICT",
    "CREATE TABLE sessions (token TEXT PRIMARY KEY, csrf TEXT NOT NULL, subject TEXT NOT NULL, created INTEGER NOT NULL, expires INTEGER NOT NULL) STRICT",
)
_AUTHORITY_COLUMNS = {
    "attempts": ("policy_realm TEXT", "policy_epoch INTEGER"),
    "sessions": ("principal_id TEXT", "principal_generation INTEGER", "policy_realm TEXT"),
}
_SCHEMA = (_SCHEMA_V1[0], *(
    sql.replace(") STRICT", ", " + ", ".join(_AUTHORITY_COLUMNS[name]) + ") STRICT")
    for sql, name in zip(_SCHEMA_V1[1:], ("attempts", "sessions"))))
_MAX_AUTHORITY = 9007199254740991



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
    principal_id: str | None = None
    principal_generation: int | None = None
    policy_realm: str | None = None


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
    """Explicit, private local runtime store; no automatic migration or creation.

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
        self._policy, self._policy_realm = None, None
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

    def _bind_policy(self, policy):
        """Trusted composition only; never adopts old sessions into new authority."""
        from packages.workspaces import SQLiteWorkspaceService
        if type(policy) is not SQLiteWorkspaceService:
            raise ValueError("explicit policy required")
        if (self._path.stat().st_dev != policy._path.stat().st_dev
                or os.path.samefile(self._path, policy._path)):
            raise AuthenticationUnavailable()
        realm = policy._transaction(lambda p: p._realm, write=False)
        if self._policy is not None and (self._policy_realm != realm
                or not os.path.samefile(self._policy._path, policy._path)):
            raise ValueError("login policy binding cannot change")
        self._policy, self._policy_realm = policy, realm

    def _table(self, name):
        if name not in {"metadata", "attempts", "sessions"}:
            raise ValueError("fixed login table required")
        return ("login." if self._policy is not None else "main.") + name

    def _authority_context(self, db):
        if self._policy is None:
            return None
        self._policy._check_schema(db)
        row = db.execute("SELECT realm,epoch FROM main.metadata WHERE id=1").fetchone()
        if (row is None or row[0] != self._policy_realm or type(row[1]) is not int
                or not 1 <= row[1] <= _MAX_AUTHORITY):
            raise AuthenticationUnavailable()
        return tuple(row)

    def _connection(self):
        if self._policy is None:
            return sqlite3.connect(self._uri, uri=True, timeout=self._timeout, isolation_level=None)
        db = self._policy._connect()
        try:
            db.execute("ATTACH DATABASE ? AS login", (self._uri,))
            self._bound_modes(db)
            return db
        except BaseException:
            db.close()
            raise

    def _bound_modes(self, db):
        if self._policy is not None:
            for name in ("main", "login"):
                if (db.execute(f"PRAGMA {name}.journal_mode").fetchone()[0] != "delete"
                        or db.execute(f"PRAGMA {name}.synchronous").fetchone()[0] < 2):
                    raise AuthenticationUnavailable()

    @staticmethod
    def _schema(db, schema="main", *, legacy=False):
        if schema not in {"main", "login"}:
            raise ValueError("fixed schema required")
        if (db.execute(f"PRAGMA {schema}.application_id").fetchone()[0] != _APP
                or db.execute(f"PRAGMA {schema}.user_version").fetchone()[0] != (1 if legacy else _VERSION)):
            raise AuthenticationUnavailable()
        for expected in (_SCHEMA_V1 if legacy else _SCHEMA):
            name = re.match(r"CREATE TABLE ([a-z_]+)", expected).group(1)
            row = db.execute(f"SELECT sql FROM {schema}.sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
            if row is None or type(row[0]) is not str or " ".join(row[0].split()) != " ".join(expected.split()):
                raise AuthenticationUnavailable()

    @classmethod
    def migrate_v1(cls, path, *, binding, clock=lambda: datetime.now(timezone.utc), timeout=5.0):
        """Explicit schema-only upgrade; legacy authority is NULL, never backfilled."""
        if type(binding) is not LoginBinding or not callable(clock) or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 30:
            raise ValueError("explicit configuration required")
        uri = "file:" + quote(str(Path(path).absolute()), safe="/") + "?mode=rw"
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=timeout, isolation_level=None)) as db:
                db.execute("PRAGMA trusted_schema=OFF")
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                try:
                    cls._schema(db, legacy=True)
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' OR (type='index' AND sql IS NOT NULL) OR (type='table' AND name NOT IN ('metadata','attempts','sessions'))").fetchone():
                        raise AuthenticationUnavailable()
                    if db.execute("SELECT count(*) FROM metadata").fetchone()[0] != 1:
                        raise AuthenticationUnavailable()
                    metadata = db.execute("SELECT binding,clock FROM metadata WHERE id=1").fetchone()
                    if metadata is None or metadata[0] != binding.fingerprint() or type(metadata[1]) is not int or _micros(clock()) < metadata[1]:
                        raise AuthenticationUnavailable()
                    _datetime(metadata[1])
                    # Validate retained v1 records without treating them as fresh authority.
                    # Expired rows may remain; corrupted rows must not be blessed by migration.
                    for state, browser, verifier, created, expires in db.execute("SELECT state,browser,verifier,created,expires FROM attempts").fetchall():
                        if (any(type(x) is not str or re.fullmatch(r"[0-9a-f]{64}", x) is None for x in (state, browser))
                                or type(verifier) is not str or _TOKEN.fullmatch(verifier) is None
                                or type(created) is not int or type(expires) is not int
                                or expires - created != binding.transaction_ttl // timedelta(microseconds=1)
                                or created > metadata[1]):
                            raise AuthenticationUnavailable()
                        _datetime(created); _datetime(expires)
                    for token, csrf, subject, created, expires in db.execute("SELECT token,csrf,subject,created,expires FROM sessions").fetchall():
                        if (any(type(x) is not str or re.fullmatch(r"[0-9a-f]{64}", x) is None for x in (token, csrf))
                                or type(created) is not int or type(expires) is not int
                                or not 0 < expires - created <= binding.session_ttl // timedelta(microseconds=1)
                                or created > metadata[1]):
                            raise AuthenticationUnavailable()
                        VerifiedIdentity(binding.issuer, subject, binding.audience, _datetime(created), _datetime(expires))
                    # Legacy records remain intact, but no legacy row gains a policy authority.
                    counts = tuple(db.execute("SELECT count(*) FROM " + table).fetchone()[0] for table in ("attempts", "sessions"))
                    for table, columns in _AUTHORITY_COLUMNS.items():
                        for column in columns:
                            db.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
                    db.execute(f"PRAGMA user_version={_VERSION}")
                    cls._schema(db)
                    db.commit()
                    return {"from_version": 1, "to_version": 2, "retained_attempts": counts[0],
                            "retained_sessions": counts[1], "legacy_authority_upgraded": False}
                except BaseException:
                    db.rollback()
                    raise
        except Exception:
            raise AuthenticationUnavailable() from None

    def _run(self, operation, *, validate=None):
        try:
            with closing(self._connection()) as db:
                db.execute("PRAGMA trusted_schema=OFF")
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                try:
                    self._bound_modes(db)
                    self._schema(db, "login" if self._policy is not None else "main")
                    self._authority_context(db)
                    row = db.execute(f"SELECT binding,clock FROM {self._table('metadata')} WHERE id=1").fetchone()
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
                        db.execute(f"UPDATE {self._table('metadata')} SET clock=? WHERE id=1", (observed,))
                        db.commit()
                        raise
                    db.execute(f"UPDATE {self._table('metadata')} SET clock=? WHERE id=1", (observed,))
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

    def _clean(self, db, now):
        db.execute(f"DELETE FROM {self._table('attempts')} WHERE expires<=?", (now,))
        db.execute(f"DELETE FROM {self._table('sessions')} WHERE expires<=?", (now,))

    def begin(self) -> LoginAttempt:
        """Prepare local state only, does not contact or enable any provider.

        Each attempt gets a fresh browser binding; HTTP integration must rotate
        its pre-login cookie. An older tab's cookie may no longer match.
        """
        def operation(db, now):
            self._clean(db, now)
            if db.execute(f"SELECT COUNT(*) FROM {self._table('attempts')}").fetchone()[0] >= self.binding.max_pending:
                raise AuthenticationUnavailable()
            state, browser, verifier = _random(), _random(), _random()
            expiry = now + self.binding.transaction_ttl // timedelta(microseconds=1)
            context = self._authority_context(db) or (None, None)
            db.execute(f"INSERT INTO {self._table('attempts')} VALUES(?,?,?,?,?,?,?)", (_digest(state), _digest(browser), verifier, now, expiry, *context))
            return LoginAttempt(Credential(state), Credential(browser), s256_challenge(Credential(verifier)), _datetime(expiry))
        return self._run(operation, validate=self._fresh)

    def _consume(self, state, browser, callback_uri):
        state, browser = _secret(state), _secret(browser)
        if type(callback_uri) is not str or callback_uri != self.binding.callback_uri:
            raise AuthenticationError()
        def operation(db, now):
            row = db.execute(f"SELECT browser,verifier,created,expires,policy_realm,policy_epoch FROM {self._table('attempts')} WHERE state=?", (_digest(state),)).fetchone()
            if row is None or type(row[0]) is not str or not hmac.compare_digest(row[0], _digest(browser)):
                raise AuthenticationError()
            verifier, created, expires, realm, epoch = row[1:]
            context = self._authority_context(db)
            if context is None:
                if realm is not None or epoch is not None:
                    raise AuthenticationError()
            elif realm != context[0] or type(epoch) is not int or not 1 <= epoch <= context[1]:
                raise AuthenticationError()
            if (type(verifier) is not str or _TOKEN.fullmatch(verifier) is None
                    or type(created) is not int or type(expires) is not int
                    or not created <= now < expires
                    or expires - created != self.binding.transaction_ttl // timedelta(microseconds=1)):
                raise AuthenticationError()
            db.execute(f"DELETE FROM {self._table('attempts')} WHERE state=?", (_digest(state),))
            return Credential(verifier), created, expires, None if context is None else (realm, epoch)
        def fresh(result, now):
            if not now < result[2]:
                raise AuthenticationError()
        return self._run(operation, validate=fresh)

    def _issue(self, assertion, started, deadline, previous, authority=None):
        if type(assertion) is not VerifiedIdentity or (assertion.issuer, assertion.audience) != (self.binding.issuer, self.binding.audience):
            raise AuthenticationError()
        prior = _digest(_secret(previous)) if previous is not None else None
        def operation(db, now):
            issued, grant_end = _micros(assertion.issued_at), _micros(assertion.expires_at)
            if not started <= issued <= now < min(deadline, grant_end):
                raise AuthenticationError()
            current = self._authority_context(db)
            principal_id, generation, realm = None, None, None
            if current is not None:
                if (type(authority) is not tuple or len(authority) != 2 or authority[0] != current[0]
                        or type(authority[1]) is not int or not 1 <= authority[1] <= current[1]):
                    raise AuthenticationError()
                policy = self._policy._load(db)
                principal = next((p for p in policy._principals.values()
                    if (p.issuer, p.subject) == (assertion.issuer, assertion.subject)), None)
                if principal is None or not principal.active or principal.authority_epoch > authority[1]:
                    raise AuthenticationError()
                principal_id, generation, realm = principal.id, principal.generation, current[0]
            elif authority is not None:
                raise AuthenticationError()
            expiry = min(grant_end, now + self.binding.session_ttl // timedelta(microseconds=1))
            self._clean(db, now)
            if prior is not None:
                db.execute(f"DELETE FROM {self._table('sessions')} WHERE token=?", (prior,))
            if db.execute(f"SELECT COUNT(*) FROM {self._table('sessions')}").fetchone()[0] >= self.binding.max_sessions:
                raise AuthenticationUnavailable()
            token, csrf = _random(), _random()
            db.execute(f"INSERT INTO {self._table('sessions')} VALUES(?,?,?,?,?,?,?,?)", (_digest(token), _digest(csrf), assertion.subject, now, expiry, principal_id, generation, realm))
            return NewSession(Credential(token), Credential(csrf), _datetime(expiry))
        def fresh(result, now):
            self._fresh(result, now)
            if not now < deadline:
                raise AuthenticationError()
        return self._run(operation, validate=fresh)

    def _session(self, db, now, token, csrf=None, *, schema=None):
        schema = schema or ("login" if self._policy is not None else "main")
        # Fixed trusted schema identifiers only, never request-selected SQL.
        if schema not in {"main", "login"}:
            raise ValueError("unsupported session schema")
        row = db.execute(f"SELECT csrf,subject,created,expires,principal_id,principal_generation,policy_realm FROM {schema}.sessions WHERE token=?", (_digest(_secret(token)),)).fetchone()
        if row is None:
            raise AuthenticationError()
        csrf_hash, subject, created, expires, principal_id, generation, realm = row
        if csrf is not None and (type(csrf_hash) is not str or not hmac.compare_digest(csrf_hash, _digest(_secret(csrf)))):
            raise AuthenticationError()
        if (type(created) is not int or type(expires) is not int or not created <= now < expires
                or not 0 < expires - created <= self.binding.session_ttl // timedelta(microseconds=1)):
            raise AuthenticationError()
        # Validate even stored identity fields rather than coercing corrupt data.
        VerifiedIdentity(self.binding.issuer, subject, self.binding.audience, _datetime(created), _datetime(expires))
        if self._policy is not None:
            context = self._authority_context(db)
            if (type(principal_id) is not str or not principal_id or type(generation) is not int
                    or not 1 <= generation <= _MAX_AUTHORITY or realm != context[0]):
                raise AuthenticationError()
            current = db.execute("SELECT issuer,subject,active,generation,authority_epoch FROM main.principals WHERE id=?", (principal_id,)).fetchone()
            if (current is None or current[:4] != (self.binding.issuer, subject, 1, generation)
                    or type(current[4]) is not int or not 1 <= current[4] <= context[1]):
                raise AuthenticationError()
        elif any(value is not None for value in (principal_id, generation, realm)):
            raise AuthenticationError()
        return SessionSnapshot(self.binding.issuer, subject, self.binding.audience, _datetime(created), _datetime(expires), principal_id, generation, realm)

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
            db.execute(f"DELETE FROM {self._table('sessions')} WHERE token=?", (_digest(_secret(token)),))
        self._run(operation)

    def revoke_subject(self, subject: str):
        """Delete existing sessions, not an account-disable or login-generation gate.

        An in-flight/new verified login may create another session. Current
        account activity and workspace policy must be checked separately.
        Trusted administration only; never expose a request-selected subject.
        """
        VerifiedIdentity(self.binding.issuer, subject, self.binding.audience, _EPOCH, _EPOCH + timedelta(seconds=1))
        self._run(lambda db, now: db.execute(f"DELETE FROM {self._table('sessions')} WHERE subject=?", (subject,)).close())


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
        verifier, started, deadline, authority = self._store._consume(state, browser_binding, callback_uri)
        if valid_error:
            raise AuthenticationError()
        try:
            assertion = self._exchange.exchange_bound_code(code=code, verifier=verifier,
                                                           callback_uri=self._store.binding.callback_uri)
        except AuthenticationError:
            raise AuthenticationError() from None
        except Exception:
            raise AuthenticationUnavailable() from None
        return self._store._issue(assertion, started, deadline, previous_session, authority)
