"""Durable identity-owned retries; old keys never become new mutations."""
from hashlib import sha256
import json
import re
from uuid import UUID

from packages.workspaces import PolicyError, SQLiteWorkspaceService, StorageError
from packages.workspaces.authority import capture_authority, validate_authority, authority_current
from packages.workspaces.model import identifier

_LEGACY = "workspace_admin_requests_v1"
_TABLE = "workspace_admin_requests_v2"
_LEGACY_CREATE = f"""CREATE TABLE {_LEGACY} (
    actor TEXT NOT NULL REFERENCES principals(id),
    action TEXT NOT NULL CHECK(action IN ('workspace.create','module.install')),
    scope TEXT NOT NULL, request_key TEXT NOT NULL, digest TEXT NOT NULL,
    result_id TEXT NOT NULL,
    PRIMARY KEY(actor,action,scope,request_key)) STRICT"""
_CREATE = _LEGACY_CREATE.replace(_LEGACY, _TABLE).replace(
    "result_id TEXT NOT NULL,", "result_id TEXT NOT NULL, authority TEXT,")
_MAX_AUTHORITY = 16384


def _check_table(connection, name, definition):
    row = connection.execute("SELECT type,sql FROM sqlite_schema WHERE name=?", (name,)).fetchone()
    normalize = lambda sql: re.sub(r"\s+", " ", sql).strip()
    family = {r[0] for r in connection.execute("SELECT name FROM sqlite_schema")
              if r[0].startswith("workspace_admin_requests_")}
    if (family != {name} or row is None or row[0] != "table" or type(row[1]) is not str
            or normalize(row[1]) != normalize(definition)
            or connection.execute("SELECT 1 FROM sqlite_schema WHERE tbl_name=? AND "
                 "(type='trigger' OR (type='index' AND sql IS NOT NULL))", (name,)).fetchone()):
        raise StorageError("invalid_administration_schema")


def _check_schema(connection):
    # Check on every lookup, including tables installed/replaced after startup.
    _check_table(connection, _TABLE, _CREATE)


def initialize_administration(store):
    """Explicit fresh v2 setup only, never an old-store upgrade or HTTP action."""
    if type(store) is not SQLiteWorkspaceService:
        raise ValueError("initialized policy store required")
    def initialize(policy, connection):
        family = [r[0] for r in connection.execute("SELECT name FROM sqlite_schema")
                  if r[0].startswith("workspace_admin_requests_")]
        if not family:
            connection.execute(_CREATE)
        _check_schema(connection)
    store._transaction(initialize, write=True, with_connection=True)


def _uuid4(value):
    key = UUID(value)
    if type(value) is not str or key.version != 4 or str(key) != value:
        raise ValueError("invalid key")


def request_key(environ):
    value = environ.get("HTTP_IDEMPOTENCY_KEY")
    try:
        _uuid4(value)
    except (ValueError, TypeError, AttributeError):
        raise PolicyError(400, "idempotency_key_required") from None
    return value


def _record(policy, row, *, reference=True):
    """Validate structure, not current role/activity; preserve lost-authority keys."""
    actor, action, scope, key, digest, result = row
    try:
        if any(type(v) is not str for v in row):
            raise ValueError()
        identifier(actor); identifier(scope); identifier(result); _uuid4(key)
        if actor not in policy._principals or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError()
        if action == "workspace.create":
            _uuid4(result)
            if scope != "platform" or (reference and result not in policy._workspaces):
                raise ValueError()
        elif action == "module.install":
            if reference and (scope, result) not in policy._modules:
                raise ValueError()
        else:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise StorageError("invalid_administration_record") from None


def migrate_administration_v1(store):
    """Explicit OFFLINE format conversion, not authority recovery or deployment.

    Preserve every valid key as an unbound conflict tombstone. Never capture
    today's identities for old records, filter lost-authority actors or rewrite
    runtime manifests. Drain all serving processes and arrange recovery first.
    """
    if type(store) is not SQLiteWorkspaceService:
        raise ValueError("initialized policy store required")
    def migrate(policy, connection):
        _check_table(connection, _LEGACY, _LEGACY_CREATE)
        rows = connection.execute(f"SELECT actor,action,scope,request_key,digest,result_id FROM {_LEGACY}").fetchall()
        for row in rows:
            _record(policy, row)
        connection.execute(_CREATE)
        connection.executemany(f"INSERT INTO {_TABLE} VALUES (?,?,?,?,?,?,NULL)", rows)
        connection.execute(f"DROP TABLE {_LEGACY}")
        _check_schema(connection)
        return len(rows)
    return store._transaction(migrate, write=True, with_connection=True)


def _recipients(actor, action, body):
    return {actor, body["initial_admin"]} if action == "workspace.create" else {actor}


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate authority key")
        result[key] = value
    return result


def _authority(value, recipients):
    try:
        if type(value) is not str or len(value.encode("ascii")) > _MAX_AUTHORITY:
            raise ValueError()
        data = json.loads(value, object_pairs_hook=_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        validate_authority(data, recipients)
        return data
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise StorageError("invalid_administration_record") from None


def remembered(policy, connection, actor, action, scope, key, body):
    _check_schema(connection)
    digest = sha256(json.dumps(body, ensure_ascii=True, sort_keys=True,
                              separators=(",", ":")).encode("ascii")).hexdigest()
    identity = (actor, action, scope, key)
    row = connection.execute(f"SELECT digest,result_id,authority FROM {_TABLE} WHERE "
                             "actor=? AND action=? AND scope=? AND request_key=?", identity).fetchone()
    if row is None:
        return None, identity, digest
    # Permanent old-key conflict, even if its once-valid result later disappears.
    if row[2] is None:
        raise PolicyError(409, "idempotency_conflict")
    _record(policy, (*identity, row[0], row[1]), reference=False)
    if row[0] != digest:
        raise PolicyError(409, "idempotency_conflict")
    recipients = _recipients(actor, action, body)
    authority = _authority(row[2], recipients)
    if any(not authority_current(policy, recipient, authority) for recipient in recipients):
        raise PolicyError(409, "idempotency_conflict")
    _record(policy, (*identity, row[0], row[1]))
    return row[1], identity, digest


def remember(policy, connection, identity, digest, result_id, body):
    # Called only after the policy mutation validated the explicit target.
    authority = json.dumps(capture_authority(policy, _recipients(identity[0], identity[1], body)),
                           ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    _authority(authority, _recipients(identity[0], identity[1], body))
    _record(policy, (*identity, digest, result_id))
    connection.execute(f"INSERT INTO {_TABLE} VALUES (?,?,?,?,?,?,?)", (*identity, digest, result_id, authority))
