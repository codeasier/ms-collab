# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Trusted publication and current-authority reads in the policy transaction."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib

from packages.workspaces import PolicyError, SQLiteWorkspaceService, StorageError
from .model import (MAX_REVISION, MAX_TASKS, SmokeScope, decode, encode, exact,
                    identifier, integer, stored_task, text, timestamp)

_SCHEMA = """CREATE TABLE gui_smoke_snapshots (
 workspace_id TEXT NOT NULL, module_key TEXT NOT NULL CHECK(module_key='gui_smoke'),
 connection_id TEXT NOT NULL, repository_id TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision>=1), record TEXT NOT NULL,
 PRIMARY KEY(workspace_id,connection_id,repository_id),
 FOREIGN KEY(workspace_id,module_key) REFERENCES modules(workspace_id,key)) STRICT"""


@dataclass(frozen=True)
class SmokeConnection:
    scope: SmokeScope
    readers: frozenset[str]
    publishers: frozenset[str]

    def __post_init__(self):
        if type(self.scope) is not SmokeScope:
            raise ValueError("explicit_scope_required")
        for people in (self.readers, self.publishers):
            if type(people) is not frozenset or not 1 <= len(people) <= 100:
                raise ValueError("explicit_audience_required")
            for person in people:
                identifier(person)


def validate_schema(connection):
    row = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='gui_smoke_snapshots'").fetchone()
    if row is None or type(row[0]) is not str or " ".join(row[0].split()) != " ".join(_SCHEMA.split()):
        raise StorageError("invalid_gui_smoke_schema")


def initialize(store):
    if type(store) is not SQLiteWorkspaceService:
        raise ValueError("configured_store_required")
    def setup(policy, connection):
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='gui_smoke_snapshots'").fetchone() is None:
            connection.execute(_SCHEMA)
        validate_schema(connection)
    store._transaction(setup, write=True, with_connection=True)


def _binding(policy, actor, scope):
    policy.authorize(actor, scope.workspace_id, "gui_smoke", "read")
    principal = policy._principals[actor]
    text(principal.issuer, 200)
    text(principal.subject, 200)
    return {"issuer": principal.issuer, "subject": principal.subject,
            "membership_revision": policy._memberships[(scope.workspace_id, actor)].revision}


def _now(clock):
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("aware_clock_required")
    return value.astimezone(timezone.utc)


def _load(connection, scope, now):
    validate_schema(connection)
    row = connection.execute("SELECT revision,record FROM gui_smoke_snapshots WHERE workspace_id=? AND connection_id=? AND repository_id=?",
                             tuple(scope.as_dict().values())).fetchone()
    if row is None:
        return None
    try:
        integer(row[0], 1, MAX_REVISION)
        record = decode(row[1])
        exact(record, {"version", "scope", "revision", "observed_at", "workspace_revision",
                       "module_revision", "readers", "tasks"})
        if type(record["version"]) is not int or record["version"] != 1 or record["scope"] != scope.as_dict() or record["revision"] != row[0]:
            raise ValueError("misplaced_snapshot")
        integer(record["revision"], 1, MAX_REVISION)
        integer(record["workspace_revision"], 1, MAX_REVISION)
        integer(record["module_revision"], 1, MAX_REVISION)
        if timestamp(record["observed_at"]) > now:
            raise ValueError("future_observation")
        readers = record["readers"]
        if type(readers) is not dict or not 1 <= len(readers) <= 100:
            raise ValueError("invalid_audience")
        for actor, binding in readers.items():
            identifier(actor)
            exact(binding, {"issuer", "subject", "membership_revision"})
            text(binding["issuer"], 200)
            text(binding["subject"], 200)
            integer(binding["membership_revision"], 1, MAX_REVISION)
        if type(record["tasks"]) is not list or len(record["tasks"]) > MAX_TASKS:
            raise ValueError("invalid_tasks")
        tasks = [stored_task(task, record["observed_at"]) for task in record["tasks"]]
        if len({task["id"] for task in tasks}) != len(tasks):
            raise ValueError("duplicate_task")
        return record
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise StorageError("invalid_gui_smoke_snapshot") from None


def read_snapshot(policy, connection, actor, configured, now):
    if actor not in configured.readers:
        raise PolicyError(404, "not_found")
    scope = configured.scope
    binding = _binding(policy, actor, scope)
    record = _load(connection, scope, now)
    if record is not None:
        if (record["readers"].get(actor) != binding
                or record["workspace_revision"] != policy._workspaces[scope.workspace_id].revision
                or record["module_revision"] != policy._modules[(scope.workspace_id, "gui_smoke")].revision):
            raise PolicyError(404, "not_found")
    return record


def publish_snapshot(store, configured, *, actor, tasks, observed_at, expected_revision,
                     clock=lambda: datetime.now(timezone.utc)):
    """Trusted, sanitized, exact-audience publication. Never an HTTP operation.

    This does not fetch or trust a panel. A future adapter must prove repository
    scope and sanitize content before invoking it with a verified actor.
    """
    if type(store) is not SQLiteWorkspaceService or type(configured) is not SmokeConnection:
        raise ValueError("configured_publication_required")
    identifier(actor)
    if type(tasks) is not tuple or len(tasks) > MAX_TASKS:
        raise ValueError("bounded_tasks_required")
    # Copy at the boundary: caller mutation cannot change the validated batch.
    selected = [stored_task(task, observed_at) for task in tasks]
    if len({task["id"] for task in selected}) != len(selected):
        raise ValueError("duplicate_task")
    scope = configured.scope
    def publish(policy, connection):
        if actor not in configured.publishers:
            raise PolicyError(404, "not_found")
        _binding(policy, actor, scope)
        policy.authorize(actor, scope.workspace_id, "gui_smoke", "write")
        now = _now(clock)
        prior = _load(connection, scope, now)
        revision = prior["revision"] if prior else 0
        if expected_revision is None:
            raise PolicyError(428, "precondition_required")
        if type(expected_revision) is not int or not 0 <= expected_revision <= MAX_REVISION:
            raise PolicyError(400, "invalid_revision")
        if expected_revision != revision:
            raise PolicyError(412, "stale_revision")
        if revision == MAX_REVISION:
            raise PolicyError(409, "snapshot_limit")
        observed = timestamp(observed_at)
        if observed > now or (prior is not None and observed <= timestamp(prior["observed_at"])):
            raise ValueError("observation_must_advance")
        record = {"version": 1, "scope": scope.as_dict(), "revision": revision + 1,
                  "observed_at": observed_at,
                  "workspace_revision": policy._workspaces[scope.workspace_id].revision,
                  "module_revision": policy._modules[(scope.workspace_id, "gui_smoke")].revision,
                  "readers": {reader: _binding(policy, reader, scope) for reader in sorted(configured.readers)},
                  "tasks": selected}
        encoded = encode(record)
        connection.execute("INSERT INTO gui_smoke_snapshots VALUES (?,'gui_smoke',?,?,?,?) ON CONFLICT(workspace_id,connection_id,repository_id) DO UPDATE SET revision=excluded.revision,record=excluded.record",
                           (scope.workspace_id, scope.connection_id, scope.repository_id, revision + 1, encoded))
        identity = hashlib.sha256(encode(scope.as_dict()).encode("ascii")).hexdigest()
        policy._changed(actor, scope.workspace_id, "resource.update", "gui-smoke:" + identity, revision + 1)
        return {"scope": scope.as_dict(), "revision": revision + 1, "task_count": len(selected)}
    return store._transaction(publish, write=True, with_connection=True)
