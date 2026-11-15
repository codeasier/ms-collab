# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Trusted publication and current-authority reads in the policy transaction."""
from datetime import datetime, timezone
import re
import hashlib

from packages.workspaces import PolicyError, SQLiteWorkspaceService, StorageError
from packages.workspaces.authority import capture_authority, authority_current
from . import grants
from .grants import SmokeConnection, load_grant, save_grant
from .model import (MAX_REVISION, MAX_TASKS, SmokeScope, decode, encode, exact,
                    identifier, integer, stored_task, text, timestamp)

_SCHEMA = """CREATE TABLE gui_smoke_snapshots (
 workspace_id TEXT NOT NULL, module_key TEXT NOT NULL CHECK(module_key='gui_smoke'),
 connection_id TEXT NOT NULL, repository_id TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision>=1), record TEXT NOT NULL,
 PRIMARY KEY(workspace_id,connection_id,repository_id),
 FOREIGN KEY(workspace_id,module_key) REFERENCES modules(workspace_id,key)) STRICT"""



def _snapshot_schema(connection):
    row = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='gui_smoke_snapshots'").fetchone()
    if row is None or type(row[0]) is not str or " ".join(row[0].split()) != " ".join(_SCHEMA.split()):
        raise StorageError("invalid_gui_smoke_schema")


def validate_schema(connection):
    _snapshot_schema(connection)
    grants.validate_schema(connection)


def initialize(store):
    """Explicit new schema installation; never upgrade a partial/legacy store."""
    if type(store) is not SQLiteWorkspaceService:raise ValueError("configured_store_required")
    def setup(policy,connection):
        present=connection.execute("SELECT name FROM sqlite_master WHERE name IN ('gui_smoke_snapshots','gui_smoke_grants')").fetchall()
        if not present:
            connection.execute(_SCHEMA);connection.execute(grants._SCHEMA)
        validate_schema(connection)
    store._transaction(setup,write=True,with_connection=True)


def migrate_v1(store):
    """Explicit offline schema addition. Preserves legacy rows, captures no grant."""
    if type(store) is not SQLiteWorkspaceService:raise ValueError("configured_store_required")
    def migrate(policy,connection):
        _snapshot_schema(connection)
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='gui_smoke_grants'").fetchone() is None:
            connection.execute(grants._SCHEMA)
        validate_schema(connection)
    store._transaction(migrate,write=True,with_connection=True)


def _capture(policy,configured):
    for reader in configured.readers:_binding(policy,reader,configured.scope)
    for publisher in configured.publishers:
        _binding(policy,publisher,configured.scope)
        policy.authorize(publisher,configured.scope.workspace_id,'gui_smoke','write')
    return capture_authority(policy,grants.recipients(configured))


def initialize_connections(store, configured):
    """Explicit first enrollment only. Not a runtime fallback or regrant API."""
    if (type(store) is not SQLiteWorkspaceService or type(configured) is not tuple or len(configured)>100
            or any(type(c) is not SmokeConnection for c in configured) or len({c.scope for c in configured})!=len(configured)):
        raise ValueError('explicit_connections_required')
    def enroll(policy,connection):
        validate_schema(connection)
        for config in configured:
            prior=load_grant(connection,config.scope,required=False)
            if prior is not None:
                if prior.configured!=config or any(not authority_current(policy,p,prior.authority) for p in grants.recipients(config)):
                    raise PolicyError(409,'explicit_regrant_required')
                continue
            if connection.execute('SELECT 1 FROM gui_smoke_snapshots WHERE workspace_id=? AND connection_id=? AND repository_id=?',grants.key(config.scope)).fetchone():
                raise PolicyError(409,'explicit_regrant_required')
            save_grant(connection,config,grants.grant_text(config,_capture(policy,config)),1,0)
    store._transaction(enroll,write=True,with_connection=True)


def _current(policy,connection,actor,configured,role):
    grant=load_grant(connection,configured.scope)
    allowed=configured.readers if role=='read' else configured.publishers
    if grant.configured!=configured or actor not in allowed or not authority_current(policy,actor,grant.authority):
        raise PolicyError(404,'not_found')
    return grant


def _expected(value,current):
    if value is None:raise PolicyError(428,'precondition_required')
    if type(value) is not int or not 0<=value<=MAX_REVISION:raise PolicyError(400,'invalid_revision')
    if value!=current:raise PolicyError(412,'stale_revision')


def regrant_connection(store,configured,*,actor,expected_grant_revision,expected_snapshot_revision,clock=lambda:datetime.now(timezone.utc)):
    """Current-admin destructive regrant; both counters and audit are atomic."""
    if type(store) is not SQLiteWorkspaceService or type(configured) is not SmokeConnection:raise ValueError('configured_publication_required')
    identifier(actor)
    def replace(policy,connection):
        policy._admin(actor,configured.scope.workspace_id)
        _binding(policy,actor,configured.scope)
        policy.authorize(actor,configured.scope.workspace_id,'gui_smoke','write')
        validate_schema(connection)
        grant=load_grant(connection,configured.scope,required=False)
        prior=_load(connection,configured.scope,_now(clock))
        revision=grant.snapshot_revision if grant else prior['revision'] if prior else 0
        grant_revision=grant.revision if grant else 0
        if prior is not None:
            if grant is None and prior['version']==3:raise StorageError('invalid_gui_smoke_grant')
            if grant is not None:_matching_snapshot(prior,grant)
        _expected(expected_grant_revision,grant_revision);_expected(expected_snapshot_revision,revision)
        if max(grant_revision,revision)==MAX_REVISION:raise PolicyError(409,'snapshot_limit')
        raw=grants.grant_text(configured,_capture(policy,configured))
        save_grant(connection,configured,raw,grant_revision+1,revision+1,prior=grant)
        connection.execute('DELETE FROM gui_smoke_snapshots WHERE workspace_id=? AND connection_id=? AND repository_id=?',grants.key(configured.scope))
        identity=hashlib.sha256(encode(configured.scope.as_dict()).encode('ascii')).hexdigest()
        policy._changed(actor,configured.scope.workspace_id,'resource.update','gui-smoke-grant:'+identity,grant_revision+1)
        return {'scope':configured.scope.as_dict(),'grant_revision':grant_revision+1,'revision':revision+1}
    return store._transaction(replace,write=True,with_connection=True)


def _matching_snapshot(record,grant):
    if (record['version']!=3 or record['grant_revision']!=grant.revision or record['grant_digest']!=grant.digest
            or record['revision']!=grant.snapshot_revision):
        raise StorageError('invalid_gui_smoke_snapshot')


def _binding(policy, actor, scope):
    policy.authorize(actor, scope.workspace_id, "gui_smoke", "read")
    principal = policy._principals[actor]
    text(principal.issuer, 200)
    text(principal.subject, 200)
    return {"issuer": principal.issuer, "subject": principal.subject,
            "membership_revision": policy._memberships[(scope.workspace_id, actor)].revision,
            "generation": principal.generation}


def _now(clock):
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("aware_clock_required")
    return value.astimezone(timezone.utc)


def _load(connection, scope, now, check_fence=None):
    validate_schema(connection)
    row = connection.execute("SELECT revision,record FROM gui_smoke_snapshots WHERE workspace_id=? AND connection_id=? AND repository_id=?",
                             tuple(scope.as_dict().values())).fetchone()
    if row is None:
        return None
    try:
        integer(row[0], 1, MAX_REVISION)
        record = decode(row[1])
        exact(record, {"version", "scope", "revision", "observed_at", "workspace_revision",
                       "module_revision", "readers", "tasks"} | ({"policy_realm"} if record.get("version") in {2,3} else set()) | ({"grant_revision","grant_digest"} if record.get("version")==3 else set()))
        if type(record["version"]) is not int or record["version"] not in {1,2,3} or record["scope"] != scope.as_dict() or record["revision"] != row[0]:
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
            exact(binding, {"issuer", "subject", "membership_revision"} | ({"generation"} if record["version"] in {2,3} else set()))
            if record["version"] in {2,3}:integer(binding["generation"], 1, MAX_REVISION)
            text(binding["issuer"], 200)
            text(binding["subject"], 200)
            integer(binding["membership_revision"], 1, MAX_REVISION)
        if record["version"] in {2,3} and (type(record["policy_realm"]) is not str or re.fullmatch(r"[0-9a-f]{32}", record["policy_realm"]) is None):
            raise ValueError("invalid_policy_realm")
        if record["version"]==3:
            integer(record["grant_revision"],1,MAX_REVISION)
            if type(record["grant_digest"]) is not str or re.fullmatch(r"[0-9a-f]{64}",record["grant_digest"]) is None:raise ValueError()
        if check_fence is not None:
            check_fence(record)
        if type(record["tasks"]) is not list or len(record["tasks"]) > MAX_TASKS:
            raise ValueError("invalid_tasks")
        tasks = [stored_task(task, record["observed_at"]) for task in record["tasks"]]
        if len({task["id"] for task in tasks}) != len(tasks):
            raise ValueError("duplicate_task")
        return record
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise StorageError("invalid_gui_smoke_snapshot") from None


def read_snapshot(policy, connection, actor, configured, now):
    grant=_current(policy,connection,actor,configured,"read")
    scope = configured.scope
    binding = _binding(policy, actor, scope)
    def check_fence(record):
        _matching_snapshot(record,grant)
        if (record["version"] != 3 or record.get("policy_realm") != policy._realm
                or record["readers"].get(actor) != binding
                or record["workspace_revision"] != policy._workspaces[scope.workspace_id].revision
                or record["module_revision"] != policy._modules[(scope.workspace_id, "gui_smoke")].revision):
            raise PolicyError(404, "not_found")
    # Evaluate saved audience/current fences before interpreting task payloads.
    return _load(connection, scope, now, check_fence),grant.snapshot_revision


def publish_snapshot(store, configured, *, actor, tasks, observed_at, expected_revision, expected_grant_revision,
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
        grant=_current(policy,connection,actor,configured,"write")
        _expected(expected_grant_revision,grant.revision)
        _binding(policy, actor, scope)
        policy.authorize(actor, scope.workspace_id, "gui_smoke", "write")
        now = _now(clock)
        prior = _load(connection, scope, now)
        if prior is not None:_matching_snapshot(prior,grant)
        revision=grant.snapshot_revision
        _expected(expected_revision,revision)
        if revision == MAX_REVISION:
            raise PolicyError(409, "snapshot_limit")
        observed = timestamp(observed_at)
        if observed > now or (prior is not None and observed <= timestamp(prior["observed_at"])):
            raise ValueError("observation_must_advance")
        record = {"version": 3, "policy_realm": policy._realm, "grant_revision":grant.revision,"grant_digest":grant.digest, "scope": scope.as_dict(), "revision": revision + 1,
                  "observed_at": observed_at,
                  "workspace_revision": policy._workspaces[scope.workspace_id].revision,
                  "module_revision": policy._modules[(scope.workspace_id, "gui_smoke")].revision,
                  "readers": {reader: _binding(policy, reader, scope) for reader in sorted(configured.readers)},
                  "tasks": selected}
        for reader in configured.readers:
            if not authority_current(policy,reader,grant.authority):raise PolicyError(404,"not_found")
        encoded = encode(record)
        save_grant(connection,configured,grant.raw,grant.revision,revision+1,prior=grant)
        connection.execute("INSERT INTO gui_smoke_snapshots VALUES (?,'gui_smoke',?,?,?,?) ON CONFLICT(workspace_id,connection_id,repository_id) DO UPDATE SET revision=excluded.revision,record=excluded.record",
                           (scope.workspace_id, scope.connection_id, scope.repository_id, revision + 1, encoded))
        identity = hashlib.sha256(encode(scope.as_dict()).encode("ascii")).hexdigest()
        policy._changed(actor, scope.workspace_id, "resource.update", "gui-smoke:" + identity, revision + 1)
        return {"scope": scope.as_dict(), "revision": revision + 1, "task_count": len(selected)}
    return store._transaction(publish, write=True, with_connection=True)
