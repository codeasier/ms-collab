"""One SQLite transaction for session state, current identity and workspace data."""
from contextlib import closing
from dataclasses import dataclass, field
import os
import sqlite3

from packages.identity import AuthenticationError, AuthenticationUnavailable, Credential
from packages.login_state import SQLiteLoginStore
from packages.login_state.core import _APP, _VERSION, _digest, _secret
from packages.workspaces import PolicyError, SQLiteWorkspaceService, StorageError


@dataclass(frozen=True)
class SessionRequest:
    token: Credential = field(repr=False)
    csrf: Credential | None = field(default=None, repr=False)


class SessionPolicyStore:
    """Trusted local-file composition. No copied snapshot or bearer assertion.

    Main policy and attached login DB must use DELETE journals on the same local
    filesystem; WAL/memory/other modes fail closed and are never changed here.
    All operations take both writer reservations before reading authorization.
    This intentionally favors correctness over concurrency and is not a remote
    database, network-filesystem or hostile-local-filesystem security boundary.
    """
    def __init__(self, *, policy: SQLiteWorkspaceService, login: SQLiteLoginStore):
        if type(policy) is not SQLiteWorkspaceService or type(login) is not SQLiteLoginStore:
            raise ValueError("explicit initialized policy and login stores required")
        self.policy, self.login = policy, login
        self.login._bind_policy(policy)
        with closing(self._connect()) as db:
            self.policy._check_schema(db)
            self._metadata(db)

    def _connect(self):
        db = None
        try:
            a, b = self.policy._path.stat(), self.login._path.stat()
            if a.st_dev != b.st_dev or os.path.samefile(self.policy._path, self.login._path):
                raise AuthenticationUnavailable()
            db = self.policy._connect()
            db.execute("ATTACH DATABASE ? AS login", (self.login._uri,))
            # Read/validate only. Never silently switch an operator's WAL store.
            self._storage_modes(db)
            return db
        except Exception:
            if db is not None:
                db.close()
            raise AuthenticationUnavailable() from None

    @staticmethod
    def _storage_modes(db):
        for schema in ("main", "login"):
            if (db.execute(f"PRAGMA {schema}.journal_mode").fetchone()[0] != "delete"
                    or db.execute(f"PRAGMA {schema}.synchronous").fetchone()[0] < 2):
                raise AuthenticationUnavailable()

    def _metadata(self, db):
        self.login._schema(db, "login")
        if (db.execute("PRAGMA login.application_id").fetchone()[0] != _APP
                or db.execute("PRAGMA login.user_version").fetchone()[0] != _VERSION):
            raise AuthenticationUnavailable()
        row = db.execute("SELECT binding,clock FROM login.metadata WHERE id=1").fetchone()
        if row is None or row[0] != self.login.binding.fingerprint() or type(row[1]) is not int:
            raise AuthenticationUnavailable()
        return row[1]

    def transaction(self, request: SessionRequest, callback, *, write=False,
                    with_connection=False, before_commit=None, receipt_only=None, storage_admission=None):
        """Trusted operation only; callback must not perform network/external I/O.

        Module adapters may use the same connection for their dedicated tables.
        Session validity, CSRF for writes, active identity and operation commit
        share a lock/snapshot. Independent logout/revoke/policy writers serialize
        before or after this transaction, not between verification and mutation.
        """
        if storage_admission is not None and (not callable(storage_admission)
                or not with_connection or receipt_only is not None):
            raise ValueError("invalid trusted storage admission")
        if type(request) is not SessionRequest or (write and request.csrf is None):
            raise AuthenticationError()
        if not callable(callback) or (before_commit is not None and not callable(before_commit)):
            raise ValueError("trusted operation required")
        if receipt_only is not None:
            from packages.directory_cursors.transaction import run, validate_mode
            validate_mode(receipt_only, write=write, with_connection=with_connection, before_commit=before_commit)
            clock = _ReceiptLoginClock(self, request)
            with closing(self._connect()) as db:
                return run(db, self.policy, receipt_only, callback, clock.current, security_clock=clock)
        try:
            with closing(self._connect()) as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    # Outside the business savepoint/clock-preserving handler:
                    # unsafe clock targets must never be written on rejection.
                    if storage_admission is not None and storage_admission(db) is not None:
                        raise ValueError("invalid trusted storage admission result")
                    # An operator may have changed journal mode after connect.
                    # Recheck under both locks before relying on atomic commit.
                    self._storage_modes(db)
                    floor = self._metadata(db)
                    observed = self.login._now()
                    if observed < floor:
                        raise AuthenticationUnavailable()
                    db.execute("SAVEPOINT business")
                    try:
                        policy = self.policy._load(db)
                        audit_start = len(policy._audit)

                        def current():
                            nonlocal observed
                            now = self.login._now()
                            if now < observed:
                                raise AuthenticationUnavailable()
                            observed = now
                            return self._current_actor(db, policy, request, now)

                        actor = current()
                        result = callback(policy, actor, db) if with_connection else callback(policy, actor)
                        if write:
                            self.policy._persist(db, policy, audit_start=audit_start)
                        if before_commit is not None:
                            before_commit()
                        # This is last, including after persistence and adapter guards.
                        if current() != actor:
                            raise AuthenticationError()
                        db.execute("RELEASE business")
                    except BaseException:
                        db.execute("ROLLBACK TO business")
                        db.execute("RELEASE business")
                        db.execute("UPDATE login.metadata SET clock=? WHERE id=1", (observed,))
                        db.commit()  # Keep security time, never a denied business write.
                        raise
                    db.execute("UPDATE login.metadata SET clock=? WHERE id=1", (observed,))
                    db.commit()
                    return result
                except BaseException:
                    db.rollback()
                    raise
        except (AuthenticationError, AuthenticationUnavailable, PolicyError, StorageError):
            raise
        except Exception:
            raise AuthenticationUnavailable() from None


    def _current_actor(self, db, policy, request, now):
        """Same identity binding for ordinary and auxiliary transactions."""
        session = self.login._session(db, now, request.token, request.csrf, schema="login")
        principal = next((p for p in policy._principals.values()
            if (p.issuer, p.subject) == (session.issuer, session.subject)), None)
        if (principal is None or not principal.active
                or (principal.id, principal.generation, policy._realm) !=
                   (session.principal_id, session.principal_generation, session.policy_realm)):
            raise AuthenticationError()
        return principal.id


_RECEIPT_PRINCIPAL_SQL = """SELECT substr(CAST(issuer AS BLOB),1,801),substr(CAST(subject AS BLOB),1,801),
    active,generation,authority_epoch FROM main.principals WHERE id=?"""


class _ReceiptLoginClock:
    """Clock-only attached-login participant; no old business savepoint handler."""
    def __init__(self, owner, request):
        self.owner, self.request = owner, request
        self.floor = self.observed = None
        self.actor = None  # Set only after real current-session authentication.

    def check(self, db):
        self.owner._storage_modes(db)
        floor = self.owner._metadata(db)
        if (db.execute("SELECT 1 FROM login.sqlite_schema WHERE tbl_name='metadata' AND (type='trigger' OR (type='index' AND sql IS NOT NULL)) LIMIT 1").fetchone()
                or db.execute('SELECT 1 FROM temp.sqlite_schema LIMIT 1').fetchone()
                or db.execute('SELECT id FROM login.metadata LIMIT 2').fetchall() != [(1,)]):
            raise AuthenticationUnavailable()
        return floor

    def observe(self):
        now = self.owner.login._now()
        previous = self.floor if self.observed is None else self.observed
        if now < previous:
            raise AuthenticationUnavailable()
        self.observed = now
        return now

    def begin(self, db):
        self.floor = self.check(db)
        self.observe()

    def current(self, policy, db):
        actor = self.owner._current_actor(db, policy, self.request, self.observe())
        if self.actor is None:
            self.actor = actor
        return actor

    def projection(self, db):
        # Fixed single-session / principal projection, never a full sessions scan.
        if self.observed is None or type(self.request.token) is not Credential:
            return None
        try:
            token = _digest(_secret(self.request.token))
        except AuthenticationError:
            if self.actor is not None:
                raise
            return None
        row = db.execute("""SELECT substr(CAST(csrf AS BLOB),1,65),substr(CAST(subject AS BLOB),1,801),
            created,expires,substr(CAST(principal_id AS BLOB),1,801),principal_generation,
            substr(CAST(policy_realm AS BLOB),1,33) FROM login.sessions WHERE token=?""", (token,)).fetchone()
        # Use the authenticated actor and its existing PK, not an expression
        # predicate over every principal or an identity inferred from raw bytes.
        principal = None if self.actor is None else db.execute(_RECEIPT_PRINCIPAL_SQL, (self.actor,)).fetchone()
        return row, principal

    def persist(self, db, sql):
        if self.observed is None:
            return
        if self.check(db) != self.floor:
            raise AuthenticationUnavailable()
        projection = self.projection(db)
        with sql.permit(('login', 'metadata', sqlite3.SQLITE_UPDATE)):
            count = db.execute('UPDATE login.metadata SET clock=? WHERE id=1 AND binding=? AND clock=?',
                (self.observed, self.owner.login.binding.fingerprint(), self.floor)).rowcount
        if count != 1 or self.check(db) != self.observed or self.projection(db) != projection:
            raise AuthenticationUnavailable()
