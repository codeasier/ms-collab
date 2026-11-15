# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Versioned index and publication records in the authorization database."""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re

from packages.artifacts import Artifact, Scope, Snapshot
from packages.workspaces import PolicyError, SQLiteWorkspaceService, StorageError
from packages.workspace_api.application import _object
from .provider import Connection, MAX_OBJECTS, object_key

_TABLE = 'artifact_indexes_v1'
_EVENTS = 'artifact_index_events_v1'
_CREATE = f'''CREATE TABLE {_TABLE} (
 workspace TEXT NOT NULL REFERENCES workspaces(id), connection TEXT NOT NULL, bucket TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision>=1), policy TEXT NOT NULL,
 candidate TEXT, published TEXT, published_revision INTEGER,
 PRIMARY KEY(workspace,connection,bucket),
 CHECK((published IS NULL AND published_revision IS NULL) OR
       (published IS NOT NULL AND published_revision>=1 AND published_revision<=revision))) STRICT'''
_EVENT_CREATE = f'''CREATE TABLE {_EVENTS} (
 sequence INTEGER PRIMARY KEY, actor TEXT NOT NULL REFERENCES principals(id),
 workspace TEXT NOT NULL, connection TEXT NOT NULL, bucket TEXT NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('refresh','approve')), revision INTEGER NOT NULL CHECK(revision>=1),
 digest TEXT NOT NULL, FOREIGN KEY(workspace,connection,bucket)
 REFERENCES {_TABLE}(workspace,connection,bucket)) STRICT'''
_MAX_BYTES = 1048576


def dump(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'))


def digest(value):
    return sha256(value.encode('ascii')).hexdigest()


def _json(raw):
    if type(raw) is not str or len(raw) > _MAX_BYTES:
        raise ValueError('invalid record')
    return json.loads(raw, object_pairs_hook=_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


@dataclass(frozen=True, slots=True)
class IndexPolicy:
    """Trusted exact-scope publication grants, NOT credentials or HTTP input."""
    connection: Connection
    credential_ref: str
    readers: tuple[str, ...]
    refreshers: tuple[str, ...]
    approvers: tuple[str, ...]

    def __post_init__(self):
        if type(self.connection) is not Connection or type(self.credential_ref) is not str or not re.fullmatch(r'[A-Za-z0-9._-]{1,128}', self.credential_ref):
            raise ValueError('invalid index policy')
        for name in ('readers', 'refreshers', 'approvers'):
            value = getattr(self, name)
            if (type(value) not in (list, tuple) or not 1 <= len(value) <= 100
                    or any(type(v) is not str or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', v) for v in value)
                    or len(set(value)) != len(value)):
                raise ValueError('invalid explicit grant')
            object.__setattr__(self, name, tuple(sorted(value)))


def policy_text(policy):
    if type(policy) is not IndexPolicy:
        raise ValueError('validated policy required')
    return dump({'schema': 1, **asdict(policy)})


def decode_policy(raw, scope):
    value = _json(raw)
    if type(value) is not dict or set(value) != {'schema', 'connection', 'credential_ref', 'readers', 'refreshers', 'approvers'} or type(value.pop('schema')) is not int:
        raise ValueError('invalid policy')
    # Canonical representation also pins schema=1 and rejects unknown fields.
    connection = value.pop('connection')
    connection['scope'] = Scope(**connection['scope'])
    result = IndexPolicy(Connection(**connection), **value)
    if result.connection.scope != scope or policy_text(result) != raw:
        raise ValueError('invalid policy')
    return result


def snapshot_text(snapshot):
    if type(snapshot) is not Snapshot or len(snapshot.artifacts) > MAX_OBJECTS:
        raise ValueError('invalid snapshot')
    for row in snapshot.artifacts:
        object_key(row.key)
        if row.size > 9007199254740991:
            raise ValueError('invalid size')
    text = dump({'schema': 1, **asdict(snapshot)})
    if len(text) > _MAX_BYTES:
        raise ValueError('index too large')
    return text


def decode_snapshot(raw, config):
    value = _json(raw)
    if type(value) is not dict or set(value) != {'schema', 'scope', 'observed_at', 'artifacts', 'offset_minutes'} or type(value.pop('schema')) is not int:
        raise ValueError('invalid snapshot')
    value['scope'] = Scope(**value['scope'])
    value['artifacts'] = tuple(Artifact(**r) for r in value['artifacts'])
    snapshot = Snapshot(**value)
    if (snapshot.scope != config.scope or snapshot.offset_minutes != config.offset_minutes
            or any(not r.key.startswith(config.prefix) for r in snapshot.artifacts)
            or snapshot_text(snapshot) != raw):
        raise ValueError('invalid snapshot')
    return snapshot


def check_schema(connection):
    normalize = lambda sql: re.sub(r'\s+', ' ', sql).strip()
    for name, expected in ((_TABLE, _CREATE), (_EVENTS, _EVENT_CREATE)):
        row = connection.execute('SELECT type,sql FROM sqlite_schema WHERE name=?', (name,)).fetchone()
        if row is None or row[0] != 'table' or type(row[1]) is not str or normalize(row[1]) != normalize(expected):
            raise StorageError('invalid_artifact_index_schema')


def scope_key(scope):
    if type(scope) is not Scope:
        raise ValueError('validated scope required')
    return scope.workspace_id, scope.connection_id, scope.bucket_id


def initialize_indexes(store, policies):
    """Trusted, additive setup only. Never overwrites grants, indexes or schemas.

    Reconfiguration/rotation is an explicit host migration, not a user endpoint.
    An identical repeat is safe; a changed existing policy fails atomically.
    """
    if type(store) is not SQLiteWorkspaceService:
        raise ValueError('initialized policy store required')
    policies = tuple(policies)
    if not 1 <= len(policies) <= 100 or any(type(p) is not IndexPolicy for p in policies):
        raise ValueError('bounded policies required')
    if len({p.connection.scope for p in policies}) != len(policies):
        raise ValueError('duplicate policy scope')
    def initialize(policy, connection):
        for sql in (_CREATE, _EVENT_CREATE):
            connection.execute(sql.replace('CREATE TABLE', 'CREATE TABLE IF NOT EXISTS', 1))
        check_schema(connection)
        for p in policies:
            scope = p.connection.scope
            if ((scope.workspace_id, 'artifacts') not in policy._modules
                    or any(v not in policy._principals for v in p.readers + p.refreshers + p.approvers)):
                raise ValueError('unprovisioned policy reference')
            raw = policy_text(p)
            existing = connection.execute(f'SELECT policy FROM {_TABLE} WHERE workspace=? AND connection=? AND bucket=?', scope_key(scope)).fetchone()
            if existing is not None:
                if existing[0] != raw:
                    raise ValueError('existing policy cannot be replaced')
            else:
                connection.execute(f'INSERT INTO {_TABLE} VALUES (?,?,?,1,?,NULL,NULL,NULL)', (*scope_key(scope), raw))
    store._transaction(initialize, write=True, with_connection=True)


@dataclass(frozen=True)
class Record:
    policy: IndexPolicy
    revision: int
    policy_raw: str
    candidate: str | None
    published: str | None
    published_revision: int | None


def load(connection, scope):
    check_schema(connection)
    row = connection.execute(f'SELECT revision,policy,candidate,published,published_revision FROM {_TABLE} WHERE workspace=? AND connection=? AND bucket=?', scope_key(scope)).fetchone()
    if row is None:
        raise PolicyError(404, 'not_found')
    try:
        revision, raw, candidate, published, published_revision = row
        if type(revision) is not int or not 1 <= revision <= 9007199254740991:
            raise ValueError()
        policy = decode_policy(raw, scope)
        if (published is None) != (published_revision is None) or (published_revision is not None and (type(published_revision) is not int or not 1 <= published_revision <= revision)):
            raise ValueError()
        return Record(policy, revision, raw, candidate, published, published_revision)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise StorageError('invalid_artifact_index') from None


def authorized(policy, actor, connection, scope, action):
    policy.authorize(actor, scope.workspace_id, 'artifacts', 'read' if action in ('read', 'review') else 'write')
    if action != 'read':
        policy._admin(actor, scope.workspace_id)
    record = load(connection, scope)
    allowed = record.policy.readers if action == 'read' else record.policy.refreshers if action == 'refresh' else record.policy.approvers
    if actor not in allowed:
        raise PolicyError(404, 'not_found')
    try:
        # Do not parse private candidate metadata for readers or denied actors.
        values = (record.published,) if action == 'read' else (record.candidate, record.published)
        for raw in values:
            if raw is not None:
                decode_snapshot(raw, record.policy.connection)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise StorageError('invalid_artifact_index') from None
    return record


def save(connection, actor, scope, record, candidate, published, published_revision, action, value_digest):
    revision = record.revision + 1
    if revision > 9007199254740991:
        raise StorageError('invalid_artifact_index')
    changed = connection.execute(f'UPDATE {_TABLE} SET revision=?,candidate=?,published=?,published_revision=? WHERE workspace=? AND connection=? AND bucket=? AND revision=? AND policy=?',
                                 (revision, candidate, published, published_revision, *scope_key(scope), record.revision, record.policy_raw)).rowcount
    if changed != 1:
        raise PolicyError(412, 'stale_revision')
    connection.execute(f'INSERT INTO {_EVENTS}(actor,workspace,connection,bucket,action,revision,digest) VALUES (?,?,?,?,?,?,?)',
                       (actor, *scope_key(scope), action, revision, value_digest))
    return revision
