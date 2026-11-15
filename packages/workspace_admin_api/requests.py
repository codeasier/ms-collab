"""Durable, actor/action/scope-bound retries in the policy transaction."""
from hashlib import sha256
import json
import re
from uuid import UUID

from packages.workspaces import PolicyError, SQLiteWorkspaceService, StorageError

_TABLE = "workspace_admin_requests_v1"
_CREATE = f"""CREATE TABLE {_TABLE} (
    actor TEXT NOT NULL REFERENCES principals(id),
    action TEXT NOT NULL CHECK(action IN ('workspace.create','module.install')),
    scope TEXT NOT NULL, request_key TEXT NOT NULL, digest TEXT NOT NULL,
    result_id TEXT NOT NULL,
    PRIMARY KEY(actor,action,scope,request_key)) STRICT"""


def _check_schema(connection):
    # This is a versioned additive table, not a migration heuristic. Reject an
    # unknown definition, including compatible-looking columns without its
    # unique key, FK, checks or STRICT typing. Check in the same transaction as
    # each retry operation so a later schema replacement cannot go unnoticed.
    row = connection.execute("SELECT type,sql FROM sqlite_schema WHERE name=?", (_TABLE,)).fetchone()
    normalize = lambda sql: re.sub(r"\s+", " ", sql).strip()
    if row is None or row[0] != "table" or type(row[1]) is not str or normalize(row[1]) != normalize(_CREATE):
        raise StorageError("invalid_administration_schema")


def initialize_administration(store):
    """Explicit trusted schema setup. Never called by an HTTP handler."""
    if type(store) is not SQLiteWorkspaceService:
        raise ValueError("initialized policy store required")
    def initialize(policy, connection):
        connection.execute(_CREATE.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
        _check_schema(connection)
    store._transaction(initialize, write=True, with_connection=True)


def request_key(environ):
    value = environ.get("HTTP_IDEMPOTENCY_KEY")
    try:
        key = UUID(value)
        if key.version != 4 or str(key) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise PolicyError(400, "idempotency_key_required") from None
    return value


def remembered(connection, actor, action, scope, key, body):
    _check_schema(connection)
    digest = sha256(json.dumps(body, ensure_ascii=True, sort_keys=True,
                              separators=(",", ":")).encode("ascii")).hexdigest()
    identity = (actor, action, scope, key)
    row = connection.execute(f"SELECT digest,result_id FROM {_TABLE} WHERE "
                             "actor=? AND action=? AND scope=? AND request_key=?", identity).fetchone()
    if row is not None and row[0] != digest:
        raise PolicyError(409, "idempotency_conflict")
    return (row[1] if row is not None else None), identity, digest


def remember(connection, identity, digest, result_id):
    connection.execute(f"INSERT INTO {_TABLE} VALUES (?,?,?,?,?,?)", (*identity, digest, result_id))
