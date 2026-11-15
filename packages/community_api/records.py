# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Dedicated community tables; never accessible through generic resource routes."""
from dataclasses import dataclass, asdict
import hashlib
import json
import re

from packages.community_board.model import Scope, Snapshot, BoardItem
from packages.community_sync import Connection
from packages.workspaces import SQLiteWorkspaceService, StorageError

MAX_BYTES = 1024 * 1024
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,199}\Z")
_SCHEMA = """CREATE TABLE community_connections (
 workspace_id TEXT NOT NULL, module_key TEXT NOT NULL CHECK(module_key='community'),
 connection_id TEXT NOT NULL, repository TEXT NOT NULL, config TEXT NOT NULL,
 snapshot TEXT, approvals TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>=1),
 PRIMARY KEY(workspace_id,connection_id,repository),
 FOREIGN KEY(workspace_id,module_key) REFERENCES modules(workspace_id,key)
) STRICT"""


def encode(value):
    text = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if len(text) > MAX_BYTES:
        raise ValueError("record_too_large")
    return text


def _pairs(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("duplicate_field")
        data[key] = value
    return data


def decode(text):
    if type(text) is not str or len(text) > MAX_BYTES:
        raise ValueError("invalid_record")
    return json.loads(text, object_pairs_hook=_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


@dataclass(frozen=True)
class ConnectionPolicy:
    connection: Connection
    credential_ref: str
    reader_ids: tuple[str, ...]
    refresher_ids: tuple[str, ...]
    approver_ids: tuple[str, ...]

    def __post_init__(self):
        if type(self.connection) is not Connection or type(self.credential_ref) is not str or not _ID.fullmatch(self.credential_ref):
            raise ValueError("invalid_connection_policy")
        for name in ("reader_ids", "refresher_ids", "approver_ids"):
            ids = getattr(self, name)
            if type(ids) is not tuple or len(ids) > 1000 or len(set(ids)) != len(ids):
                raise ValueError("invalid_connection_policy")
            if any(type(value) is not str or not _ID.fullmatch(value) for value in ids):
                raise ValueError("invalid_connection_policy")

    @property
    def scope(self):
        return self.connection.scope

    def as_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        if type(data) is not dict or set(data) != {"connection", "credential_ref", "reader_ids", "refresher_ids", "approver_ids"}:
            raise ValueError("invalid_connection_policy")
        raw = data["connection"]
        if type(raw) is not dict or set(raw) != {"scope", "page_size", "max_pages", "timeout", "max_response_bytes"}:
            raise ValueError("invalid_connection_policy")
        if type(raw["scope"]) is not dict or set(raw["scope"]) != {"workspace_id", "connection_id", "repository"}:
            raise ValueError("invalid_connection_policy")
        if any(type(data[key]) is not list for key in ("reader_ids", "refresher_ids", "approver_ids")):
            raise ValueError("invalid_connection_policy")
        connection = Connection(Scope(**raw["scope"]), **{k:v for k,v in raw.items() if k != "scope"})
        return cls(connection, data["credential_ref"], tuple(data["reader_ids"]), tuple(data["refresher_ids"]), tuple(data["approver_ids"]))


def snapshot_data(snapshot):
    if type(snapshot) is not Snapshot:
        raise ValueError("invalid_snapshot")
    return {"scope": snapshot.scope.as_dict(), "observed_at": snapshot.observed_at,
            "items": [item.as_dict() for item in snapshot.items]}


def load_snapshot(scope, raw):
    if raw is None:
        return None
    if type(raw) is not dict or set(raw) != {"scope", "observed_at", "items"} or raw["scope"] != scope.as_dict() or type(raw["items"]) is not list:
        raise ValueError("invalid_snapshot")
    if any(type(item) is not dict or set(item) != {"kind", "number", "title", "state", "updated_at", "labels"} for item in raw["items"]):
        raise ValueError("invalid_snapshot")
    return Snapshot(scope, raw["observed_at"], tuple(BoardItem(**item) for item in raw["items"]))


def fingerprint(scope, item):
    return hashlib.sha256(encode({"scope":scope.as_dict(), "item":item.as_dict()}).encode("ascii")).hexdigest()


@dataclass(frozen=True)
class State:
    policy: ConnectionPolicy
    snapshot: Snapshot | None
    approvals: frozenset[str]
    revision: int


def read(connection, scope):
    row = connection.execute("SELECT config,snapshot,approvals,revision FROM community_connections WHERE workspace_id=? AND connection_id=? AND repository=?",
                             (scope.workspace_id, scope.connection_id, scope.repository)).fetchone()
    if row is None:
        return None
    try:
        config = ConnectionPolicy.from_dict(decode(row[0]))
        snapshot = load_snapshot(scope, decode(row[1])) if row[1] is not None else None
        approvals = decode(row[2])
        if config.scope != scope or type(row[3]) is not int or row[3] < 1 or type(approvals) is not list:
            raise ValueError()
        if len(approvals) > 200 or any(type(value) is not str or not _HASH.fullmatch(value) for value in approvals) or len(set(approvals)) != len(approvals):
            raise ValueError()
        available = {fingerprint(scope, item) for item in snapshot.items} if snapshot else set()
        if not set(approvals) <= available:
            raise ValueError()
        return State(config, snapshot, frozenset(approvals), row[3])
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise StorageError("invalid_store") from None


def initialize(store, policies):
    """Trusted one-time setup in the policy database; no provider/data import."""
    policies = tuple(policies)
    if type(store) is not SQLiteWorkspaceService or any(type(p) is not ConnectionPolicy for p in policies):
        raise ValueError("invalid_bootstrap")
    def operation(policy, connection):
        connection.execute(_SCHEMA)
        for config in policies:
            if (config.scope.workspace_id, "community") not in policy._modules:
                raise ValueError("missing_community_module")
            if not set(config.reader_ids + config.refresher_ids + config.approver_ids) <= policy._principals.keys():
                raise ValueError("unknown_connection_principal")
            scope = config.scope
            connection.execute("INSERT INTO community_connections VALUES (?,?,?,?,?,?,?,?)",
                               (scope.workspace_id, "community", scope.connection_id, scope.repository,
                                encode(config.as_dict()), None, "[]", 1))
    store._transaction(operation, write=True, with_connection=True)


def replace_policy(store, config, *, expected_revision):
    """Trusted operator reconfiguration; clears snapshots/approvals atomically.

    Not an HTTP endpoint. Changing the approved audience or credential routing
    never implicitly releases old observations to a newly configured audience.
    """
    if type(config) is not ConnectionPolicy or type(expected_revision) is not int or expected_revision < 1:
        raise ValueError("invalid_connection_policy")
    def operation(policy, connection):
        state = read(connection, config.scope)
        if state is None or state.revision != expected_revision:
            raise ValueError("stale_connection")
        if not set(config.reader_ids + config.refresher_ids + config.approver_ids) <= policy._principals.keys():
            raise ValueError("unknown_connection_principal")
        scope = config.scope
        connection.execute("UPDATE community_connections SET config=?,snapshot=NULL,approvals='[]',revision=revision+1 WHERE workspace_id=? AND connection_id=? AND repository=?",
                           (encode(config.as_dict()), scope.workspace_id, scope.connection_id, scope.repository))
    store._transaction(operation, write=True, with_connection=True)
