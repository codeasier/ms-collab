"""Local SQLite adapter for the workspace policy reference.

No authentication, HTTP, external store, migration of existing deployments, or
production bootstrap is provided. This adapter and service.py are one trusted
implementation boundary; never expose a raw actor ID as authentication.
"""
from contextlib import closing
from dataclasses import astuple
import math
import os
import re
from uuid import uuid4
from pathlib import Path
import sqlite3
from urllib.parse import quote

from .model import MAX_REVISION, AuditEvent, Membership, ModuleInstance, Principal, Resource, Workspace, identifier, revision
from .service import PolicyError, WorkspaceService

_SCHEMA_VERSION = 3
_APPLICATION_ID = 0x4D534357  # MSCW: do not interpret unrelated databases as policy stores.
_SCHEMA_V1 = (
    "CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK (id=1), epoch INTEGER NOT NULL CHECK (epoch>=1)) STRICT",
    "CREATE TABLE principals (id TEXT PRIMARY KEY, issuer TEXT NOT NULL, subject TEXT NOT NULL, active INTEGER NOT NULL CHECK (active IN (0,1)), UNIQUE(issuer,subject)) STRICT",
    "CREATE TABLE operators (principal_id TEXT PRIMARY KEY REFERENCES principals(id)) STRICT",
    "CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('product','shared')), state TEXT NOT NULL CHECK (state IN ('active','suspended','archived')), revision INTEGER NOT NULL CHECK (revision>=1)) STRICT",
    "CREATE TABLE memberships (workspace_id TEXT NOT NULL REFERENCES workspaces(id), principal_id TEXT NOT NULL REFERENCES principals(id), role TEXT NOT NULL CHECK (role IN ('viewer','developer','admin')), state TEXT NOT NULL CHECK (state IN ('active','revoked')), revision INTEGER NOT NULL CHECK (revision>=1), PRIMARY KEY(workspace_id,principal_id)) STRICT",
    "CREATE TABLE modules (workspace_id TEXT NOT NULL REFERENCES workspaces(id), key TEXT NOT NULL, can_read INTEGER NOT NULL CHECK (can_read IN (0,1)), can_write INTEGER NOT NULL CHECK (can_write IN (0,1)), state TEXT NOT NULL CHECK (state IN ('enabled','disabling','disabled')), revision INTEGER NOT NULL CHECK (revision>=1), PRIMARY KEY(workspace_id,key)) STRICT",
    "CREATE TABLE resources (workspace_id TEXT NOT NULL, module_key TEXT NOT NULL, id TEXT NOT NULL, text TEXT NOT NULL, revision INTEGER NOT NULL CHECK (revision>=1), PRIMARY KEY(workspace_id,module_key,id), FOREIGN KEY(workspace_id,module_key) REFERENCES modules(workspace_id,key)) STRICT",
    "CREATE TABLE audit (sequence INTEGER PRIMARY KEY CHECK (sequence>=1), actor_id TEXT NOT NULL REFERENCES principals(id), workspace_id TEXT NOT NULL REFERENCES workspaces(id), action TEXT NOT NULL, object_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK (revision>=1)) STRICT",
)


_MAX_AUTHORITY = MAX_REVISION
_REALM_COLUMN = "realm TEXT NOT NULL DEFAULT 'uninitialized'"
_GENERATION_COLUMN = "generation INTEGER NOT NULL DEFAULT 1 CHECK(generation>=1 AND generation<=9007199254740991)"
_EPOCH_COLUMN = "authority_epoch INTEGER NOT NULL DEFAULT 1 CHECK(authority_epoch>=1 AND authority_epoch<=9007199254740991)"
# Runtime identity mutation remains CLOSED until all ID-based grants are migrated.
_PRINCIPAL_GUARDS = (
    "CREATE TRIGGER principal_authority_closed BEFORE UPDATE ON principals WHEN NEW.id!=OLD.id OR NEW.issuer!=OLD.issuer OR NEW.subject!=OLD.subject OR NEW.active!=OLD.active OR NEW.generation!=OLD.generation OR NEW.authority_epoch!=OLD.authority_epoch BEGIN SELECT RAISE(ABORT,'identity_transition_unavailable'); END",
    "CREATE TRIGGER principal_delete_closed BEFORE DELETE ON principals BEGIN SELECT RAISE(ABORT,'identity_transition_unavailable'); END",
    "CREATE TRIGGER principal_replace_closed BEFORE INSERT ON principals WHEN EXISTS(SELECT 1 FROM authority_migrations) OR EXISTS(SELECT 1 FROM principals WHERE id=NEW.id OR (issuer=NEW.issuer AND subject=NEW.subject)) BEGIN SELECT RAISE(ABORT,'identity_transition_unavailable'); END",
)
_MIGRATIONS = "CREATE TABLE authority_migrations (id INTEGER PRIMARY KEY CHECK(id=1), from_version INTEGER NOT NULL CHECK(from_version IN (0,1)), to_version INTEGER NOT NULL CHECK(to_version=2), principal_count INTEGER NOT NULL CHECK(principal_count>=0), realm TEXT NOT NULL) STRICT"
_SCHEMA_V2 = (
    _SCHEMA_V1[0].replace(") STRICT", ", " + _REALM_COLUMN + ") STRICT"),
    _SCHEMA_V1[1].replace(", UNIQUE", ", " + _GENERATION_COLUMN + ", " + _EPOCH_COLUMN + ", UNIQUE"),
    *_SCHEMA_V1[2:], _MIGRATIONS, *_PRINCIPAL_GUARDS,
    "CREATE TRIGGER policy_authority_monotonic BEFORE UPDATE ON metadata WHEN NEW.id!=OLD.id OR NEW.realm!=OLD.realm OR NEW.epoch<OLD.epoch OR NEW.epoch>9007199254740991 BEGIN SELECT RAISE(ABORT,'invalid_authority_epoch'); END",
    "CREATE TRIGGER policy_metadata_delete_closed BEFORE DELETE ON metadata BEGIN SELECT RAISE(ABORT,'invalid_authority_epoch'); END",
    "CREATE TRIGGER policy_metadata_replace_closed BEFORE INSERT ON metadata WHEN EXISTS(SELECT 1 FROM metadata) BEGIN SELECT RAISE(ABORT,'invalid_authority_epoch'); END",
    "CREATE TRIGGER authority_marker_update_closed BEFORE UPDATE ON authority_migrations BEGIN SELECT RAISE(ABORT,'identity_transition_unavailable'); END",
    "CREATE TRIGGER authority_marker_delete_closed BEFORE DELETE ON authority_migrations BEGIN SELECT RAISE(ABORT,'identity_transition_unavailable'); END",
    "CREATE TRIGGER authority_marker_replace_closed BEFORE INSERT ON authority_migrations WHEN EXISTS(SELECT 1 FROM authority_migrations) BEGIN SELECT RAISE(ABORT,'identity_transition_unavailable'); END",
)


# NULL is an explicit legacy ownership tombstone, never a capture-on-load cue.
# Every retained membership/operator has exactly one sidecar, bound or unbound.
_GRANT_COLUMNS = ("realm TEXT, issuer TEXT, subject TEXT, generation INTEGER "
                  "CHECK(generation IS NULL OR (generation>=1 AND generation<=9007199254740991))")
_GRANT_COMPLETENESS = ("CHECK((realm IS NULL AND issuer IS NULL AND subject IS NULL AND generation IS NULL) "
                       "OR (realm IS NOT NULL AND issuer IS NOT NULL AND subject IS NOT NULL AND generation IS NOT NULL))")
_GRANT_SCHEMA = (
    "CREATE TABLE membership_authority (workspace_id TEXT NOT NULL, principal_id TEXT NOT NULL, "
    + _GRANT_COLUMNS + ", PRIMARY KEY(workspace_id,principal_id), "
    "FOREIGN KEY(workspace_id,principal_id) REFERENCES memberships(workspace_id,principal_id), "
    + _GRANT_COMPLETENESS + ") STRICT",
    "CREATE TABLE operator_authority (principal_id TEXT PRIMARY KEY REFERENCES operators(principal_id), "
    + _GRANT_COLUMNS + ", " + _GRANT_COMPLETENESS + ") STRICT",
)
_SCHEMA = (*_SCHEMA_V2, *_GRANT_SCHEMA)


def _decode_grant(actor, row):
    if row == (None, None, None, None):
        return None
    realm, issuer, subject, generation = row
    # Strict validation belongs to the service's persisted decoder. Partial NULL
    # tuples and malformed bindings reach it unchanged and fail, never default.
    return {"version": 1, "realm": realm, "principals": {
        actor: {"issuer": issuer, "subject": subject, "generation": generation}}}


def _encode_grant(actor, grant):
    if grant is None:
        return (None, None, None, None)
    binding = grant["principals"][actor]
    return (grant["realm"], binding["issuer"], binding["subject"], binding["generation"])


def _schema_objects(connection, definitions):
    for expected in definitions:
        kind, name = re.match(r"CREATE (TABLE|TRIGGER) ([a-z_]+)", expected).groups()
        row = connection.execute("SELECT type,sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
        if (row is None or row[0] != kind.lower() or type(row[1]) is not str
                or " ".join(row[1].split()) != " ".join(expected.split())):
            raise StorageError("unsupported_schema")


class StorageError(Exception):
    """A storage failure, without paths/SQL/data. Not an authorization decision."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


class SQLiteWorkspaceService:
    """Same operation API as WorkspaceService, persisted on one local filesystem.

    Every operation opens a fresh connection and reloads current state. Mutations
    acquire BEGIN IMMEDIATE before reading any authorization or revision state,
    then commit policy/resource/audit changes together. No process-local policy
    cache is authoritative. Reads use one consistent transaction snapshot.

    The constructor only opens an existing initialized database. initialize()
    explicitly creates a new file and refuses to replace any existing file.
    This small reference adapter materializes all rows per operation, not a
    scalable query backend or a filesystem security boundary.
    """

    def __init__(self, path, *, timeout=5.0):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 <= timeout <= 30:
            raise ValueError("timeout must be between 0 and 30 seconds")
        self._path = Path(path).absolute()
        self._timeout = timeout
        # mode=rw is important: normal opens must never create a missing store.
        self._uri = f"file:{quote(str(self._path), safe='/')}?mode=rw"
        try:
            with closing(self._connect()) as connection:
                self._check_schema(connection)
        except sqlite3.Error as error:
            raise self._storage_error(error) from None

    @classmethod
    def initialize(cls, path, *, principals, workspaces, memberships, modules,
                   resources=(), operators=frozenset(), timeout=5.0):
        """Trusted one-time bootstrap; does not import/migrate any old database.

        Parent directories must already exist. An unsuccessful initialization
        retains the newly allocated file for operator inspection; it is not
        silently reused or deleted. Caller must supply synthetic/trusted records.
        """
        service = WorkspaceService(principals=principals, workspaces=workspaces,
                                   memberships=memberships, modules=modules,
                                   resources=resources, operators=operators)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 <= timeout <= 30:
            raise ValueError("timeout must be between 0 and 30 seconds")
        if any(p.generation != 1 or p.authority_epoch != 1 for p in service._principals.values()):
            raise ValueError("new authority must start at baseline one")
        path = Path(path).absolute()
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise StorageError("store_exists") from None
        except OSError:
            raise StorageError("store_unavailable") from None
        os.close(descriptor)
        instance = object.__new__(cls)
        instance._path, instance._timeout = path, timeout
        instance._uri = f"file:{quote(str(path), safe='/')}?mode=rw"
        try:
            with closing(instance._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    for statement in _SCHEMA:
                        connection.execute(statement)
                    connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                    connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
                    connection.executemany("INSERT INTO principals VALUES (?,?,?,?,?,?)", (astuple(p) for p in service._principals.values()))
                    connection.executemany("INSERT INTO operators VALUES (?)", ((p,) for p in service._operators))
                    connection.executemany("INSERT INTO operator_authority VALUES (?,?,?,?,?)",
                        ((actor, *_encode_grant(actor, grant))
                         for actor, grant in service._operator_authority.items()))
                    connection.execute("INSERT INTO metadata VALUES (1,?,?)", (service._epoch, service._realm))
                    connection.execute("INSERT INTO authority_migrations VALUES (1,0,2,?,?)", (len(service._principals), service._realm))
                    instance._persist(connection, service, audit_start=0)
                    instance._load(connection)
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        except sqlite3.Error as error:
            raise instance._storage_error(error) from None
        return cls(path, timeout=timeout)

    @staticmethod
    def _storage_error(error):
        code = getattr(error, "sqlite_errorcode", 0) & 0xFF
        return StorageError("store_busy" if code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                            else "store_unavailable")

    def _connect(self):
        connection = None
        try:
            connection = sqlite3.connect(self._uri, uri=True, timeout=self._timeout, isolation_level=None)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA synchronous=FULL")
            return connection
        except sqlite3.Error as error:
            if connection is not None:
                connection.close()
            raise self._storage_error(error) from None

    @staticmethod
    def _check_schema(connection):
        if (connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
                or connection.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA_VERSION):
            raise StorageError("unsupported_schema")
        _schema_objects(connection, _SCHEMA)
        # No sidecar trigger or custom index is part of v3. In particular an
        # unknown trigger must not rewrite a pin after persistence compares it.
        # Unrelated module extension tables retain their separate contracts.
        if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE tbl_name IN ('membership_authority','operator_authority') "
                "AND (type='trigger' OR (type='index' AND sql IS NOT NULL))").fetchone():
            raise StorageError("unsupported_schema")

    @staticmethod
    def _bit(value):
        # Database files can be malformed even when this connection enforces
        # CHECK constraints. Never coerce an unexpected flag into authority.
        if type(value) is not int or value not in (0, 1):
            raise ValueError("invalid boolean flag")
        return bool(value)

    @staticmethod
    def _rows(connection, sql):
        # Finalize statements before validation can raise. Otherwise exception
        # tracebacks can retain a half-consumed cursor and a SQLite read lock.
        with closing(connection.execute(sql)) as cursor:
            return cursor.fetchall()

    def _load(self, connection):
        self._check_schema(connection)
        try:
            if self._rows(connection, "PRAGMA foreign_key_check"):
                raise ValueError("invalid foreign reference")
            epoch = connection.execute("SELECT epoch,realm FROM metadata WHERE id=1").fetchone()
            if (epoch is None or type(epoch[0]) is not int or not 1 <= epoch[0] <= _MAX_AUTHORITY
                    or type(epoch[1]) is not str or re.fullmatch(r"[0-9a-f]{32}", epoch[1]) is None):
                raise ValueError("invalid policy epoch")
            service = WorkspaceService._from_persisted(
                principals=tuple(Principal(a, b, c, self._bit(d), e, f) for a, b, c, d, e, f in self._rows(connection, "SELECT id,issuer,subject,active,generation,authority_epoch FROM principals")),
                workspaces=tuple(Workspace(*row) for row in self._rows(connection, "SELECT id,name,kind,state,revision FROM workspaces")),
                memberships=tuple(Membership(*row) for row in self._rows(connection, "SELECT workspace_id,principal_id,role,state,revision FROM memberships")),
                modules=tuple(ModuleInstance(a, b, tuple(cap for cap, on in (("read", self._bit(c)), ("write", self._bit(d))) if on), e, f)
                              for a, b, c, d, e, f in self._rows(connection, "SELECT workspace_id,key,can_read,can_write,state,revision FROM modules")),
                resources=tuple(Resource(*row) for row in self._rows(connection, "SELECT workspace_id,module_key,id,text,revision FROM resources")),
                operators=frozenset(row[0] for row in self._rows(connection, "SELECT principal_id FROM operators")),
                realm=epoch[1], epoch=epoch[0],
                membership_authority={(workspace, actor): _decode_grant(actor, tuple(pin))
                    for workspace, actor, *pin in self._rows(connection,
                        "SELECT workspace_id,principal_id,realm,issuer,subject,generation FROM membership_authority")},
                operator_authority={actor: _decode_grant(actor, tuple(pin))
                    for actor, *pin in self._rows(connection,
                        "SELECT principal_id,realm,issuer,subject,generation FROM operator_authority")},
            )
            if any(p.authority_epoch > epoch[0] for p in service._principals.values()):
                raise ValueError("invalid policy epoch")
            migration = connection.execute("SELECT from_version,to_version,principal_count,realm FROM authority_migrations WHERE id=1").fetchone()
            if (migration is None or migration[0] not in (0,1) or migration[1] != 2
                    or type(migration[2]) is not int or migration[2] < 0 or migration[3] != service._realm):
                raise ValueError("invalid authority migration marker")
            service._audit = [AuditEvent(*row) for row in self._rows(connection, "SELECT sequence,actor_id,workspace_id,action,object_id,revision FROM audit ORDER BY sequence")]
            for event in service._audit:
                for value in (event.actor_id, event.workspace_id, event.object_id):
                    identifier(value)
                revision(event.revision)
                if event.action not in {"workspace.create", "workspace.state", "membership.update",
                                        "membership.promote", "membership.demote", "membership.join",
                                        "invitation.create", "invitation.accepted", "invitation.declined", "invitation.revoked", "module.state", "module.install", "resource.update"}:
                    raise ValueError("invalid audit action")
            if [event.sequence for event in service._audit] != list(range(1, len(service._audit) + 1)):
                raise ValueError("invalid audit sequence")
            return service
        except (ValueError, TypeError, KeyError):
            raise StorageError("invalid_store") from None

    @staticmethod
    def _persist(connection, service, *, audit_start):
        # No delete or replace: retain foreign keys, history, unrelated scopes.
        try:
            revision(service._epoch)
            service._validate_authority_grants()
            for records in (service._workspaces, service._memberships, service._modules, service._resources):
                for record in records.values():
                    revision(record.revision)
            if type(audit_start) is not int or not 0 <= audit_start <= len(service._audit) <= MAX_REVISION:
                raise ValueError()
            for sequence, event in enumerate(service._audit, 1):
                revision(event.revision)
                revision(event.sequence)
                if event.sequence != sequence:
                    raise ValueError()
        except (ValueError, TypeError):
            raise StorageError("invalid_store") from None
        membership_authority = {
            key: _encode_grant(key[1], grant)
            for key, grant in service._membership_authority.items()}
        operator_authority = {
            actor: _encode_grant(actor, grant)
            for actor, grant in service._operator_authority.items()}
        existing_memberships = SQLiteWorkspaceService._rows(connection,
            "SELECT workspace_id,principal_id,realm,issuer,subject,generation FROM membership_authority")
        existing_operators = SQLiteWorkspaceService._rows(connection,
            "SELECT principal_id,realm,issuer,subject,generation FROM operator_authority")
        # Missing sidecars are corruption, not an invitation to repair ownership
        # from the in-memory principal directory. New memberships have no prior
        # core row; fresh bootstrap explicitly installs operator grants first.
        if ({(workspace, actor) for workspace, actor, *_ in existing_memberships}
                != set(SQLiteWorkspaceService._rows(connection,
                    "SELECT workspace_id,principal_id FROM memberships"))
                or {actor for actor, *_ in existing_operators}
                != {row[0] for row in SQLiteWorkspaceService._rows(connection,
                    "SELECT principal_id FROM operators")}):
            raise StorageError("invalid_store")
        if (any(membership_authority.get((workspace, actor)) != tuple(pin)
                for workspace, actor, *pin in existing_memberships)
                or any(operator_authority.get(actor) != tuple(pin)
                       for actor, *pin in existing_operators)):
            raise StorageError("invalid_store")
        connection.executemany("INSERT INTO workspaces VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,kind=excluded.kind,state=excluded.state,revision=excluded.revision", (astuple(w) for w in service._workspaces.values()))
        connection.executemany("INSERT INTO memberships VALUES (?,?,?,?,?) ON CONFLICT(workspace_id,principal_id) DO UPDATE SET role=excluded.role,state=excluded.state,revision=excluded.revision", (astuple(m) for m in service._memberships.values()))
        connection.executemany(
            "INSERT INTO membership_authority VALUES (?,?,?,?,?,?) ON CONFLICT(workspace_id,principal_id) DO NOTHING",
            ((*key, *pin) for key, pin in membership_authority.items()))
        connection.executemany(
            "INSERT INTO operator_authority VALUES (?,?,?,?,?) ON CONFLICT(principal_id) DO NOTHING",
            ((actor, *pin) for actor, pin in operator_authority.items()))
        connection.executemany("INSERT INTO modules VALUES (?,?,?,?,?,?) ON CONFLICT(workspace_id,key) DO UPDATE SET can_read=excluded.can_read,can_write=excluded.can_write,state=excluded.state,revision=excluded.revision", ((m.workspace_id, m.key, "read" in m.capabilities, "write" in m.capabilities, m.state, m.revision) for m in service._modules.values()))
        connection.executemany("INSERT INTO resources VALUES (?,?,?,?,?) ON CONFLICT(workspace_id,module_key,id) DO UPDATE SET text=excluded.text,revision=excluded.revision", (astuple(r) for r in service._resources.values()))
        connection.execute("UPDATE metadata SET epoch=? WHERE id=1", (service._epoch,))
        connection.executemany("INSERT INTO audit VALUES (?,?,?,?,?,?)", (astuple(e) for e in service._audit[audit_start:]))
        # A trigger on another table must not alter grant ownership after the
        # pre-write comparison either. Verify the complete persisted sidecars
        # after ALL DML (including audit), while the same writer lock is held.
        saved_memberships = {(workspace, actor): tuple(pin)
            for workspace, actor, *pin in SQLiteWorkspaceService._rows(connection,
                "SELECT workspace_id,principal_id,realm,issuer,subject,generation FROM membership_authority")}
        saved_operators = {actor: tuple(pin)
            for actor, *pin in SQLiteWorkspaceService._rows(connection,
                "SELECT principal_id,realm,issuer,subject,generation FROM operator_authority")}
        if (saved_memberships != membership_authority or saved_operators != operator_authority
                or set(saved_memberships) != set(SQLiteWorkspaceService._rows(connection,
                    "SELECT workspace_id,principal_id FROM memberships"))
                or set(saved_operators) != {row[0] for row in SQLiteWorkspaceService._rows(connection,
                    "SELECT principal_id FROM operators")}):
            raise StorageError("invalid_store")

    def _transaction(self, operation, *, write, before_commit=None, with_connection=False):
        """Internal trusted adapter seam, not a request-selected callback.

        Identity-aware services may bind an already protocol-verified assertion
        to this snapshot before invoking a protected policy operation.
        with_connection exposes the SAME transaction only to trusted adapter
        code for dedicated module tables, never to a request-selected callback.
        """
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                try:
                    service = self._load(connection)
                    audit_start = len(service._audit)
                    result = operation(service, connection) if with_connection else operation(service)
                    if write:
                        self._persist(connection, service, audit_start=audit_start)
                    if before_commit is not None:
                        before_commit()
                    connection.commit()
                    return result
                except BaseException:
                    connection.rollback()
                    raise
        except sqlite3.Error as error:
            raise self._storage_error(error) from None

    @staticmethod
    def _migrate_grant_tombstones(connection):
        """Schema conversion only; no old local-ID grant acquires an owner."""
        for sql in _GRANT_SCHEMA:
            connection.execute(sql)
        connection.execute("INSERT INTO membership_authority(workspace_id,principal_id) SELECT workspace_id,principal_id FROM memberships")
        connection.execute("INSERT INTO operator_authority(principal_id) SELECT principal_id FROM operators")

    @classmethod
    def migrate_v2(cls, path, *, timeout=5.0):
        """Explicit offline v2-to-v3 conversion, retaining unbound legacy grants.

        This is not grant restoration. All old membership/operator rows receive
        NULL ownership tombstones and cannot authorize after conversion. Keep a
        protected recovery copy and drain old request-serving processes first.
        """
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 30:
            raise ValueError("invalid timeout")
        instance = object.__new__(cls)
        instance._path, instance._timeout = Path(path).absolute(), timeout
        instance._uri = f"file:{quote(str(instance._path), safe='/')}?mode=rw"
        try:
            with closing(instance._connect()) as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    if (db.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
                            or db.execute("PRAGMA user_version").fetchone()[0] != 2):
                        raise StorageError("unsupported_schema")
                    _schema_objects(db, _SCHEMA_V2)
                    core = tuple(re.match(r"CREATE TABLE ([a-z_]+)", sql).group(1)
                                 for sql in _SCHEMA_V2 if sql.startswith("CREATE TABLE "))
                    guards = {re.match(r"CREATE TRIGGER ([a-z_]+)", sql).group(1)
                              for sql in _SCHEMA_V2 if sql.startswith("CREATE TRIGGER ")}
                    placeholders = ",".join("?" for _ in core)
                    extensions = db.execute(
                        f"SELECT type,name FROM sqlite_master WHERE tbl_name IN ({placeholders}) "
                        "AND (type='trigger' OR (type='index' AND sql IS NOT NULL))", core).fetchall()
                    if any(kind != "trigger" or name not in guards for kind, name in extensions):
                        raise StorageError("unsupported_schema")
                    instance._migrate_grant_tombstones(db)
                    db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
                    service = instance._load(db)
                    db.commit()
                    return {"from_version": 2, "to_version": 3, "principals": len(service._principals),
                            "identity_mutation_enabled": False, "legacy_grants_upgraded": False}
                except BaseException:
                    db.rollback()
                    raise
        except sqlite3.Error as error:
            raise instance._storage_error(error) from None

    @classmethod
    def migrate_v1(cls, path, *, timeout=5.0):
        """Explicit local schema migration only; no identity mutation or login grant.

        Drain old processes and secure a recovery copy first. No constructor or
        HTTP route calls this. Unknown/partial schemas fail without changes.
        """
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 30:
            raise ValueError("invalid timeout")
        instance = object.__new__(cls)
        instance._path, instance._timeout = Path(path).absolute(), timeout
        instance._uri = f"file:{quote(str(instance._path), safe='/')}?mode=rw"
        try:
            with closing(instance._connect()) as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    if (db.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
                            or db.execute("PRAGMA user_version").fetchone()[0] != 1):
                        raise StorageError("unsupported_schema")
                    _schema_objects(db, _SCHEMA_V1)
                    core = tuple(re.match(r"CREATE TABLE ([a-z_]+)", sql).group(1) for sql in _SCHEMA_V1)
                    placeholders = ",".join("?" for _ in core)
                    if db.execute(f"SELECT 1 FROM sqlite_master WHERE tbl_name IN ({placeholders}) AND (type='trigger' OR (type='index' AND sql IS NOT NULL))", core).fetchone():
                        raise StorageError("unsupported_schema")
                    for table, column in (("principals", _GENERATION_COLUMN), ("principals", _EPOCH_COLUMN), ("metadata", _REALM_COLUMN)):
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
                    realm = uuid4().hex
                    db.execute("UPDATE metadata SET realm=? WHERE id=1", (realm,))
                    db.execute(_MIGRATIONS)
                    count = db.execute("SELECT count(*) FROM principals").fetchone()[0]
                    db.execute("INSERT INTO authority_migrations VALUES (1,1,2,?,?)", (count, realm))
                    for sql in _SCHEMA:
                        if sql.startswith("CREATE TRIGGER "):
                            db.execute(sql)
                    instance._migrate_grant_tombstones(db)
                    db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
                    instance._load(db)  # Data and all foreign keys validate before COMMIT.
                    db.commit()
                    return {"from_version": 1, "to_version": 3, "principals": count,
                            "identity_mutation_enabled": False, "legacy_grants_upgraded": False}
                except BaseException:
                    db.rollback()
                    raise
        except sqlite3.Error as error:
            raise instance._storage_error(error) from None

    def set_principal(self, *args, **kwargs):
        """Closed even to trusted callers; no hidden mutation/activation switch."""
        raise PolicyError(503, "identity_transition_unavailable")

    def _call(self, method, *args, write=False, **kwargs):
        return self._transaction(lambda service: getattr(service, method)(*args, **kwargs), write=write)

    def authorize(self, actor_id, workspace_id, module_key, action):
        return self._call("authorize", actor_id, workspace_id, module_key, action)

    def list_workspaces(self, actor_id):
        return self._call("list_workspaces", actor_id)

    def list_memberships(self, actor_id, workspace_id):
        return self._call("list_memberships", actor_id, workspace_id)

    def set_membership(self, actor_id, workspace_id, principal_id, *, role, state, expected_revision):
        return self._call("set_membership", actor_id, workspace_id, principal_id, role=role, state=state, expected_revision=expected_revision, write=True)

    def transfer_admin(self, actor_id, workspace_id, replacement_id, *, expected_revision, replacement_revision):
        return self._call("transfer_admin", actor_id, workspace_id, replacement_id, expected_revision=expected_revision, replacement_revision=replacement_revision, write=True)

    def list_control_workspaces(self, actor_id):
        return self._call("list_control_workspaces", actor_id)

    def list_modules(self, actor_id, workspace_id):
        return self._call("list_modules", actor_id, workspace_id)

    def install_module(self, actor_id, workspace_id, module_key, *, capabilities):
        return self._call("install_module", actor_id, workspace_id, module_key,
                          capabilities=capabilities, write=True)

    def set_module_state(self, actor_id, workspace_id, module_key, *, state, expected_revision):
        return self._call("set_module_state", actor_id, workspace_id, module_key, state=state, expected_revision=expected_revision, write=True)

    def get_module(self, actor_id, workspace_id, module_key):
        return self._call("get_module", actor_id, workspace_id, module_key)

    def set_workspace_state(self, actor_id, workspace_id, *, state, expected_revision):
        return self._call("set_workspace_state", actor_id, workspace_id, state=state, expected_revision=expected_revision, write=True)

    def initial_admin_selection(self, actor_id, initial_admin):
        return self._call("initial_admin_selection", actor_id, initial_admin)

    def create_workspace(self, actor_id, *, name, kind, initial_admin,
                         expected_initial_admin_selection=None):
        return self._call("create_workspace", actor_id, name=name, kind=kind,
                          initial_admin=initial_admin,
                          expected_initial_admin_selection=expected_initial_admin_selection, write=True)

    def read_resource(self, actor_id, workspace_id, module_key, resource_id):
        return self._call("read_resource", actor_id, workspace_id, module_key, resource_id)

    def list_resources(self, actor_id, workspace_id, module_key, *, offset=0, limit=20):
        return self._call("list_resources", actor_id, workspace_id, module_key, offset=offset, limit=limit)

    def update_resource(self, actor_id, workspace_id, module_key, resource_id, *, text, expected_revision):
        return self._call("update_resource", actor_id, workspace_id, module_key, resource_id, text=text, expected_revision=expected_revision, write=True)

    def audit_events(self, actor_id, workspace_id):
        return self._call("audit_events", actor_id, workspace_id)
