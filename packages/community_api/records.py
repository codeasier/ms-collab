# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Dedicated community tables; never accessible through generic resource routes."""
from dataclasses import dataclass, asdict
import hashlib
import json
import re

from packages.community_board.model import Scope, Snapshot, BoardItem
from packages.community_sync import Connection
from packages.workspaces import SQLiteWorkspaceService, StorageError, PolicyError
from packages.workspaces.authority import capture_authority, validate_authority, authority_current

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


_MAX_REVISION = 9007199254740991


def _recipients(config):
    return set(config.reader_ids + config.refresher_ids + config.approver_ids)


def config_text(config, authority, grant_revision, retired_principals=None):
    validate_authority(authority, _recipients(config))
    retired_principals = {} if retired_principals is None else retired_principals
    if type(retired_principals) is not dict or set(retired_principals) & _recipients(config):
        raise ValueError('invalid_retired_authority')
    validate_authority({'version':1,'realm':authority['realm'],'principals':retired_principals},tuple(retired_principals))
    if len(retired_principals)+len(authority['principals'])>3000:raise ValueError('authority_history_limit')
    if type(grant_revision) is not int or not 1 <= grant_revision <= _MAX_REVISION:
        raise ValueError("invalid_grant_revision")
    return encode({"version":2, "policy":config.as_dict(), "authority":authority,
                   "grant_revision":grant_revision,"retired_principals":retired_principals})


def _config(raw, scope, *, legacy=False):
    value = decode(raw)
    if legacy and type(value) is dict and 'connection' in value:
        config = ConnectionPolicy.from_dict(value)
        if config.scope != scope: raise ValueError("misplaced_config")
        return config, None, 0, {}
    if type(value) is not dict or set(value) != {'version','policy','authority','grant_revision','retired_principals'} or type(value['version']) is not int or value['version'] != 2:
        raise ValueError("unbound_community_grants")
    config = ConnectionPolicy.from_dict(value['policy'])
    if config.scope != scope or config_text(config,value['authority'],value['grant_revision'],value['retired_principals']) != raw:
        raise ValueError("invalid_bound_community_grants")
    return config, value['authority'], value['grant_revision'], value['retired_principals']


def empty_approvals():
    return encode({'version':2, 'fingerprints':[], 'provenance':None})


def approval_text(fingerprints, provenance):
    fingerprints = sorted(fingerprints)
    if len(fingerprints)>200 or len(set(fingerprints))!=len(fingerprints) or any(type(v) is not str or not _HASH.fullmatch(v) for v in fingerprints):
        raise ValueError('invalid_approvals')
    if fingerprints:
        _validate_provenance(provenance)
    elif provenance is not None:
        raise ValueError('empty_approval_has_no_authority')
    return encode({'version':2,'fingerprints':fingerprints,'provenance':provenance})


def _validate_provenance(value):
    if (type(value) is not dict or set(value) != {'actor','authority','membership_revision','config_digest'}
            or type(value['actor']) is not str or not _ID.fullmatch(value['actor'])
            or type(value['membership_revision']) is not int or not 1<=value['membership_revision']<=_MAX_REVISION
            or type(value['config_digest']) is not str or not _HASH.fullmatch(value['config_digest'])):
        raise ValueError('invalid_approval_provenance')
    validate_authority(value['authority'], (value['actor'],))


def capture_approval(policy, state, actor):
    member=policy._admin(actor,state.policy.scope.workspace_id)
    if actor not in state.policy.approver_ids or not authority_current(policy,actor,state.authority):
        raise PolicyError(404,'not_found')
    return {'actor':actor,'authority':capture_authority(policy,(actor,)),
            'membership_revision':member.revision,
            'config_digest':hashlib.sha256(state.config_raw.encode('ascii')).hexdigest()}


def approval_current(policy, state):
    if not state.approvals: return True
    proof=state.provenance
    if proof is None: return False
    actor=proof['actor'];member=policy._memberships.get((state.policy.scope.workspace_id,actor))
    return (actor in state.policy.approver_ids and authority_current(policy,actor,state.authority)
            and authority_current(policy,actor,proof['authority'])
            and policy._membership_current(actor,state.policy.scope.workspace_id) and member.role=='admin'
            and member.revision==proof['membership_revision']
            and proof['config_digest']==hashlib.sha256(state.config_raw.encode('ascii')).hexdigest())


@dataclass(frozen=True)
class State:
    policy: ConnectionPolicy
    snapshot: Snapshot | None
    approvals: frozenset[str]
    revision: int
    authority: dict | None
    grant_revision: int
    config_raw: str
    provenance: dict | None
    retired_principals: dict


def read(connection, scope, *, allow_legacy=False):
    """Structure only. Legacy is solely for explicit destructive regrant, not reads."""
    row = connection.execute("SELECT config,snapshot,approvals,revision FROM community_connections WHERE workspace_id=? AND connection_id=? AND repository=?",
                             (scope.workspace_id, scope.connection_id, scope.repository)).fetchone()
    if row is None: return None
    try:
        config,authority,grant_revision,retired = _config(row[0],scope,legacy=allow_legacy)
        snapshot = load_snapshot(scope, decode(row[1])) if row[1] is not None else None
        value = decode(row[2]);provenance=None
        if allow_legacy and authority is None:
            approvals=value
        else:
            if (type(value) is not dict or set(value)!={'version','fingerprints','provenance'}
                    or type(value['version']) is not int or value['version']!=2
                    or type(value['fingerprints']) is not list): raise ValueError()
            approvals=value['fingerprints'];provenance=value['provenance']
            if approval_text(approvals,provenance)!=row[2]: raise ValueError()
        if config.scope != scope or type(row[3]) is not int or not 1<=row[3]<=_MAX_REVISION or type(approvals) is not list:
            raise ValueError()
        if len(approvals)>200 or any(type(v) is not str or not _HASH.fullmatch(v) for v in approvals) or len(set(approvals))!=len(approvals): raise ValueError()
        available = {fingerprint(scope,item) for item in snapshot.items} if snapshot else set()
        if not set(approvals)<=available: raise ValueError()
        return State(config,snapshot,frozenset(approvals),row[3],authority,grant_revision,row[0],provenance,retired)
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise StorageError("invalid_store") from None


def initialize(store, policies):
    """Trusted one-time setup; no adoption or regrant of an existing record."""
    policies = tuple(policies)
    if type(store) is not SQLiteWorkspaceService or any(type(p) is not ConnectionPolicy for p in policies):
        raise ValueError("invalid_bootstrap")
    def operation(policy, connection):
        connection.execute(_SCHEMA)
        for config in policies:
            if (config.scope.workspace_id,"community") not in policy._modules: raise ValueError("missing_community_module")
            authority=capture_authority(policy,_recipients(config));scope=config.scope
            connection.execute("INSERT INTO community_connections VALUES (?,?,?,?,?,?,?,?)",
                (scope.workspace_id,"community",scope.connection_id,scope.repository,
                 config_text(config,authority,1),None,empty_approvals(),1))
    store._transaction(operation,write=True,with_connection=True)


def _write_policy_record(policy, connection, actor, config, state, authority, retired=None):
    if state.revision>=_MAX_REVISION or state.grant_revision>=_MAX_REVISION:
        raise PolicyError(409,'connection_revision_exhausted')
    scope=config.scope
    try:
        raw=config_text(config,authority,state.grant_revision+1,retired)
    except (ValueError, TypeError):
        raise PolicyError(400,'invalid_connection_grants') from None
    changed=connection.execute("UPDATE community_connections SET config=?,snapshot=NULL,approvals=?,revision=revision+1 WHERE workspace_id=? AND connection_id=? AND repository=? AND revision=? AND config=?",
        (raw,empty_approvals(),scope.workspace_id,scope.connection_id,scope.repository,state.revision,state.config_raw)).rowcount
    if changed!=1:raise PolicyError(412,'stale_revision')
    # This helper is the sole audit owner, in the caller's SAME transaction.
    policy._changed(actor,scope.workspace_id,'resource.update',
        'community-config:'+hashlib.sha256(encode(scope.as_dict()).encode('ascii')).hexdigest(),state.revision+1)
    return read(connection,scope)


def replace_policy_record(policy, connection, actor, config, *, expected_revision):
    """Regular same-transaction edit + sole audit; never recaptures existing pins.

    Caller must additionally enforce its trusted template/manager/capability pins.
    This helper independently requires current workspace admin. No new transaction.
    """
    if type(config) is not ConnectionPolicy:raise PolicyError(400,'invalid_connection_policy')
    policy._admin(actor,config.scope.workspace_id)
    state=read(connection,config.scope)
    if state is None:raise PolicyError(404,'not_found')
    policy._match(expected_revision,state.revision)
    if state.policy==config:return state
    ids=_recipients(config);old=state.retired_principals | state.authority['principals']
    # Previously seen IDs retain their original authority even after removal.
    # Remove/re-add is NOT explicit authority regrant. Only never-seen IDs get
    # their first capture; bounded tombstones are cleared only by regrant_policy.
    added=ids-set(old)
    try:
        fresh=capture_authority(policy,added)
    except (ValueError, TypeError):
        raise PolicyError(400,'invalid_connection_grants') from None
    authority={'version':1,'realm':state.authority['realm'],
               'principals':{i:old[i] if i in old else fresh['principals'][i] for i in sorted(ids)}}
    if state.authority['realm']!=policy._realm:raise PolicyError(409,'connection_authority_changed')
    retired={i:binding for i,binding in old.items() if i not in ids}
    return _write_policy_record(policy,connection,actor,config,state,authority,retired)


def replace_policy(store, config, *, expected_revision, actor_id):
    """Trusted regular edit; actor required for current authorization and audit."""
    if type(store) is not SQLiteWorkspaceService:raise ValueError('invalid_store')
    return store._transaction(lambda p,c:replace_policy_record(p,c,actor_id,config,expected_revision=expected_revision),write=True,with_connection=True)


def regrant_policy(store, config, *, expected_revision, actor_id):
    """Explicit trusted regrant: fresh pins, monotonic revision, discard old content.

    No HTTP route or auto-upgrade. Known legacy config may only enter this path.
    Identical DTOs still advance grant revision and clear snapshot/approvals.
    """
    if type(store) is not SQLiteWorkspaceService or type(config) is not ConnectionPolicy:raise ValueError('invalid_regrant')
    def operation(policy, connection):
        policy._admin(actor_id,config.scope.workspace_id)
        state=read(connection,config.scope,allow_legacy=True)
        if state is None:raise PolicyError(404,'not_found')
        policy._match(expected_revision,state.revision)
        try:
            authority=capture_authority(policy,_recipients(config))
        except (ValueError, TypeError):
            raise PolicyError(400,'invalid_connection_grants') from None
        return _write_policy_record(policy,connection,actor_id,config,state,authority)
    return store._transaction(operation,write=True,with_connection=True)
