"""Local SQLite adapter for the workspace policy reference.

No authentication, HTTP, external store, migration of existing deployments, or
production bootstrap is provided. This adapter and service.py are one trusted
implementation boundary; never expose a raw actor ID as authentication.
"""
from contextlib import closing
from dataclasses import astuple
import math
import os
from pathlib import Path
import sqlite3
from urllib.parse import quote

from .model import AuditEvent, Membership, ModuleInstance, Principal, Resource, Workspace, identifier, revision
from .service import WorkspaceService

_SCHEMA_VERSION = 1
_APPLICATION_ID = 0x4D534357  # MSCW: do not interpret unrelated databases as policy stores.
_SCHEMA = (
    "CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK (id=1), epoch INTEGER NOT NULL CHECK (epoch>=1)) STRICT",
    "CREATE TABLE principals (id TEXT PRIMARY KEY, issuer TEXT NOT NULL, subject TEXT NOT NULL, active INTEGER NOT NULL CHECK (active IN (0,1)), UNIQUE(issuer,subject)) STRICT",
    "CREATE TABLE operators (principal_id TEXT PRIMARY KEY REFERENCES principals(id)) STRICT",
    "CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('product','shared')), state TEXT NOT NULL CHECK (state IN ('active','suspended','archived')), revision INTEGER NOT NULL CHECK (revision>=1)) STRICT",
    "CREATE TABLE memberships (workspace_id TEXT NOT NULL REFERENCES workspaces(id), principal_id TEXT NOT NULL REFERENCES principals(id), role TEXT NOT NULL CHECK (role IN ('viewer','developer','admin')), state TEXT NOT NULL CHECK (state IN ('active','revoked')), revision INTEGER NOT NULL CHECK (revision>=1), PRIMARY KEY(workspace_id,principal_id)) STRICT",
    "CREATE TABLE modules (workspace_id TEXT NOT NULL REFERENCES workspaces(id), key TEXT NOT NULL, can_read INTEGER NOT NULL CHECK (can_read IN (0,1)), can_write INTEGER NOT NULL CHECK (can_write IN (0,1)), state TEXT NOT NULL CHECK (state IN ('enabled','disabling','disabled')), revision INTEGER NOT NULL CHECK (revision>=1), PRIMARY KEY(workspace_id,key)) STRICT",
    "CREATE TABLE resources (workspace_id TEXT NOT NULL, module_key TEXT NOT NULL, id TEXT NOT NULL, text TEXT NOT NULL, revision INTEGER NOT NULL CHECK (revision>=1), PRIMARY KEY(workspace_id,module_key,id), FOREIGN KEY(workspace_id,module_key) REFERENCES modules(workspace_id,key)) STRICT",
    "CREATE TABLE audit (sequence INTEGER PRIMARY KEY CHECK (sequence>=1), actor_id TEXT NOT NULL REFERENCES principals(id), workspace_id TEXT NOT NULL REFERENCES workspaces(id), action TEXT NOT NULL, object_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK (revision>=1)) STRICT",
)


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
                    connection.executemany("INSERT INTO principals VALUES (?,?,?,?)", (astuple(p) for p in service._principals.values()))
                    connection.executemany("INSERT INTO operators VALUES (?)", ((p,) for p in service._operators))
                    connection.execute("INSERT INTO metadata VALUES (1,?)", (service._epoch,))
                    instance._persist(connection, service, audit_start=0)
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
            service = WorkspaceService(
                principals=tuple(Principal(a, b, c, self._bit(d)) for a, b, c, d in self._rows(connection, "SELECT id,issuer,subject,active FROM principals")),
                workspaces=tuple(Workspace(*row) for row in self._rows(connection, "SELECT id,name,kind,state,revision FROM workspaces")),
                memberships=tuple(Membership(*row) for row in self._rows(connection, "SELECT workspace_id,principal_id,role,state,revision FROM memberships")),
                modules=tuple(ModuleInstance(a, b, tuple(cap for cap, on in (("read", self._bit(c)), ("write", self._bit(d))) if on), e, f)
                              for a, b, c, d, e, f in self._rows(connection, "SELECT workspace_id,key,can_read,can_write,state,revision FROM modules")),
                resources=tuple(Resource(*row) for row in self._rows(connection, "SELECT workspace_id,module_key,id,text,revision FROM resources")),
                operators=frozenset(row[0] for row in self._rows(connection, "SELECT principal_id FROM operators")),
            )
            epoch = connection.execute("SELECT epoch FROM metadata WHERE id=1").fetchone()
            if epoch is None or type(epoch[0]) is not int or epoch[0] < 1:
                raise ValueError("invalid policy epoch")
            service._epoch = epoch[0]
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
        connection.executemany("INSERT INTO workspaces VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,kind=excluded.kind,state=excluded.state,revision=excluded.revision", (astuple(w) for w in service._workspaces.values()))
        connection.executemany("INSERT INTO memberships VALUES (?,?,?,?,?) ON CONFLICT(workspace_id,principal_id) DO UPDATE SET role=excluded.role,state=excluded.state,revision=excluded.revision", (astuple(m) for m in service._memberships.values()))
        connection.executemany("INSERT INTO modules VALUES (?,?,?,?,?,?) ON CONFLICT(workspace_id,key) DO UPDATE SET can_read=excluded.can_read,can_write=excluded.can_write,state=excluded.state,revision=excluded.revision", ((m.workspace_id, m.key, "read" in m.capabilities, "write" in m.capabilities, m.state, m.revision) for m in service._modules.values()))
        connection.executemany("INSERT INTO resources VALUES (?,?,?,?,?) ON CONFLICT(workspace_id,module_key,id) DO UPDATE SET text=excluded.text,revision=excluded.revision", (astuple(r) for r in service._resources.values()))
        connection.execute("UPDATE metadata SET epoch=? WHERE id=1", (service._epoch,))
        connection.executemany("INSERT INTO audit VALUES (?,?,?,?,?,?)", (astuple(e) for e in service._audit[audit_start:]))

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

    def create_workspace(self, actor_id, *, name, kind, initial_admin):
        return self._call("create_workspace", actor_id, name=name, kind=kind, initial_admin=initial_admin, write=True)

    def read_resource(self, actor_id, workspace_id, module_key, resource_id):
        return self._call("read_resource", actor_id, workspace_id, module_key, resource_id)

    def list_resources(self, actor_id, workspace_id, module_key, *, offset=0, limit=20):
        return self._call("list_resources", actor_id, workspace_id, module_key, offset=offset, limit=limit)

    def update_resource(self, actor_id, workspace_id, module_key, resource_id, *, text, expected_revision):
        return self._call("update_resource", actor_id, workspace_id, module_key, resource_id, text=text, expected_revision=expected_revision, write=True)

    def audit_events(self, actor_id, workspace_id):
        return self._call("audit_events", actor_id, workspace_id)
