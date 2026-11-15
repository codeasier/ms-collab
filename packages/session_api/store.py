"""One SQLite transaction for session state, current identity and workspace data."""
from contextlib import closing
from dataclasses import dataclass, field
import os

from packages.identity import AuthenticationError, AuthenticationUnavailable, Credential
from packages.login_state import SQLiteLoginStore
from packages.login_state.core import _APP, _VERSION
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
        if (db.execute("PRAGMA login.application_id").fetchone()[0] != _APP
                or db.execute("PRAGMA login.user_version").fetchone()[0] != _VERSION):
            raise AuthenticationUnavailable()
        row = db.execute("SELECT binding,clock FROM login.metadata WHERE id=1").fetchone()
        if row is None or row[0] != self.login.binding.fingerprint() or type(row[1]) is not int:
            raise AuthenticationUnavailable()
        return row[1]

    def transaction(self, request: SessionRequest, callback, *, write=False,
                    with_connection=False, before_commit=None):
        """Trusted operation only; callback must not perform network/external I/O.

        Module adapters may use the same connection for their dedicated tables.
        Session validity, CSRF for writes, active identity and operation commit
        share a lock/snapshot. Independent logout/revoke/policy writers serialize
        before or after this transaction, not between verification and mutation.
        """
        if type(request) is not SessionRequest or (write and request.csrf is None):
            raise AuthenticationError()
        if not callable(callback) or (before_commit is not None and not callable(before_commit)):
            raise ValueError("trusted operation required")
        try:
            with closing(self._connect()) as db:
                db.execute("BEGIN IMMEDIATE")
                try:
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
                            session = self.login._session(db, now, request.token, request.csrf, schema="login")
                            principal = next((p for p in policy._principals.values()
                                if (p.issuer, p.subject) == (session.issuer, session.subject)), None)
                            if principal is None or not principal.active:
                                raise AuthenticationError()
                            return principal.id

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
