# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Versioned index and publication records in the authorization database."""
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
import re

from packages.artifacts import Artifact, Scope, Snapshot
from packages.workspaces import PolicyError, SQLiteWorkspaceService, StorageError
from packages.workspace_api.application import _object
from packages.workspaces.authority import capture_authority, validate_authority, authority_current, principal_pin
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
MAX_REVISION = 9007199254740991
PUBLICATION_RESERVE = 16384


def dump(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'))


def digest(value):
    return sha256(value.encode('ascii')).hexdigest()


def _json(raw):
    if type(raw) is not str or len(raw) > _MAX_BYTES:
        raise ValueError('invalid record')
    return json.loads(raw, object_pairs_hook=_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def _decode_projection(raw):
    """Decode a bounded BLOB prefix without dropping NUL or malformed bytes."""
    if type(raw) is not bytes:
        raise ValueError('invalid record projection')
    return raw.decode('utf-8')


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


def recipients(config):
    return set(config.readers + config.refreshers + config.approvers)


def bound_policy_text(config, authority, grant_revision):
    validate_authority(authority, recipients(config))
    if type(grant_revision) is not int or not 1 <= grant_revision <= MAX_REVISION:
        raise ValueError('invalid grant revision')
    raw = dump({'schema':2, 'policy':_json(policy_text(config)),
                'authority':authority, 'grant_revision':grant_revision})
    if len(raw) > _MAX_BYTES:
        raise ValueError('policy too large')
    return raw


def _bound_policy(raw, scope, *, legacy=False):
    value = _json(raw)
    if legacy and type(value) is dict and value.get('schema') == 1:
        return decode_policy(raw, scope), None, 0
    if (type(value) is not dict or set(value) != {'schema','policy','authority','grant_revision'}
            or type(value['schema']) is not int or value['schema'] != 2):
        raise ValueError('unbound index authority')
    config = decode_policy(dump(value['policy']), scope)
    if bound_policy_text(config,value['authority'],value['grant_revision']) != raw:
        raise ValueError('invalid bound policy')
    return config,value['authority'],value['grant_revision']


def flow_pin(policy, actor, scope):
    return (principal_pin(policy,actor),
            policy._memberships[(scope.workspace_id,actor)].revision,
            policy._workspaces[scope.workspace_id].revision,
            policy._modules[(scope.workspace_id,'artifacts')].revision)


def _proof(value):
    if (type(value) is not dict or set(value) != {'actor','authority','membership_revision','policy_digest','snapshot_digest'}
            or type(value['actor']) is not str
            or type(value['membership_revision']) is not int or not 1 <= value['membership_revision'] <= MAX_REVISION
            or any(type(value[k]) is not str or not re.fullmatch(r'[0-9a-f]{64}',value[k]) for k in ('policy_digest','snapshot_digest'))):
        raise ValueError('invalid publication proof')
    validate_authority(value['authority'],(value['actor'],))


def publication_text(snapshot, provenance):
    _proof(provenance)
    if provenance['snapshot_digest'] != digest(snapshot):
        raise ValueError('misbound publication proof')
    raw = dump({'schema':2,'snapshot':_json(snapshot),'provenance':provenance})
    if len(raw) > _MAX_BYTES:
        raise ValueError('publication too large')
    return raw


def _publication(raw, config, *, legacy=False):
    if raw is None:
        return None,None
    value = _json(raw)
    if legacy:
        # A recognizable v2 publication beside a plain v1 config is mixed data,
        # not a known legacy record whose grant counter may start at one.
        if type(value) is not dict or type(value.get('schema')) is not int or value['schema'] != 1:
            raise ValueError('mixed publication authority')
        decode_snapshot(raw,config)
        return raw,None
    if (type(value) is not dict or set(value) != {'schema','snapshot','provenance'}
            or type(value['schema']) is not int or value['schema'] != 2):
        raise ValueError('unbound publication')
    snapshot = dump(value['snapshot'])
    decode_snapshot(snapshot,config)
    if publication_text(snapshot,value['provenance']) != raw:
        raise ValueError('invalid bound publication')
    return snapshot,value['provenance']


def capture_publication(policy, actor, record, candidate):
    member = policy._admin(actor,record.policy.connection.scope.workspace_id)
    if actor not in record.policy.approvers or not authority_current(policy,actor,record.authority):
        raise PolicyError(404,'not_found')
    return {'actor':actor,'authority':capture_authority(policy,(actor,)),
            'membership_revision':member.revision,'policy_digest':digest(record.policy_raw),
            'snapshot_digest':digest(candidate)}


def publication_current(policy, record):
    if record.published_raw is None:
        return True
    proof = record.provenance
    actor = proof['actor']
    member = policy._memberships.get((record.policy.connection.scope.workspace_id,actor))
    return (actor in record.policy.approvers and authority_current(policy,actor,record.authority)
            and authority_current(policy,actor,proof['authority'])
            and policy._membership_current(actor,record.policy.connection.scope.workspace_id) and member.role == 'admin'
            and member.revision == proof['membership_revision']
            and proof['policy_digest'] == digest(record.policy_raw))


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
            authority = capture_authority(policy,recipients(p))
            raw = bound_policy_text(p,authority,1)
            existing = connection.execute(f'SELECT policy FROM {_TABLE} WHERE workspace=? AND connection=? AND bucket=?', scope_key(scope)).fetchone()
            if existing is not None:
                current, stored, _ = _bound_policy(existing[0],scope)
                if current != p or stored != authority:
                    raise ValueError('existing policy cannot be replaced')
            else:
                connection.execute(f'INSERT INTO {_TABLE} VALUES (?,?,?,1,?,NULL,NULL,NULL)', (*scope_key(scope), raw))
    store._transaction(initialize, write=True, with_connection=True)


@dataclass(frozen=True)
class Record:
    policy: IndexPolicy
    revision: int
    policy_raw: str
    # Directory loads defer decoding these byte projections until explicit grant.
    candidate: str | bytes | None
    published_raw: str | bytes | None
    published_revision: int | None
    authority: dict | None
    grant_revision: int

    @property
    def published(self):
        return _publication(self.published_raw,self.policy.connection)[0]

    @property
    def provenance(self):
        return _publication(self.published_raw,self.policy.connection)[1]


def load(connection, scope, *, allow_legacy=False, read_candidate=True, budget=None):
    check_schema(connection)
    # A reader directory must not fetch the private candidate at all. Bounded
    # discovery projections cap allocation before applying the aggregate budget.
    # BLOB substr preserves embedded NUL (TEXT substr does not). Valid codecs
    # round-trip through ensure_ascii=True dump, so their character/byte limits
    # coincide; the extra byte makes an oversized canonical prefix fail closed.
    field = lambda name: f'substr(CAST({name} AS BLOB),1,{_MAX_BYTES + 1})' if budget is not None else name
    candidate_column = field('candidate') if read_candidate else 'NULL'
    row = connection.execute(f"SELECT revision,{field('policy')},{candidate_column},{field('published')},published_revision FROM {_TABLE} WHERE workspace=? AND connection=? AND bucket=?", scope_key(scope)).fetchone()
    if row is None:
        raise PolicyError(404, 'not_found')
    try:
        revision, raw, candidate, published, published_revision = row
        if budget is not None:
            raw = _decode_projection(raw)
            budget(raw)
        if type(revision) is not int or not 1 <= revision <= MAX_REVISION:
            raise ValueError()
        policy,authority,grant_revision = _bound_policy(raw,scope,legacy=allow_legacy)
        if (published is None) != (published_revision is None) or (published_revision is not None and (type(published_revision) is not int or not 1 <= published_revision <= revision)):
            raise ValueError()
        return Record(policy,revision,raw,candidate,published,published_revision,authority,grant_revision)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise StorageError('invalid_artifact_index') from None


def authorized(policy, actor, connection, scope, action, *, budget=None):
    policy.authorize(actor, scope.workspace_id, 'artifacts', 'read' if action in ('read', 'review') else 'write')
    if action != 'read':
        policy._admin(actor, scope.workspace_id)
    record = load(connection, scope, read_candidate=action != 'read', budget=budget)
    allowed = record.policy.readers if action == 'read' else record.policy.refreshers if action == 'refresh' else record.policy.approvers
    if actor not in allowed or not authority_current(policy,actor,record.authority):
        raise PolicyError(404, 'not_found')
    try:
        # Do not parse private candidate metadata for readers or denied actors.
        if budget is not None:
            record = replace(record,
                candidate=None if record.candidate is None else _decode_projection(record.candidate),
                published_raw=None if record.published_raw is None else _decode_projection(record.published_raw))
            # Only granted scopes contribute snapshot bytes; denied scopes never
            # decode publication/candidate. Each authorized validation input is charged.
            for raw in ((record.published_raw,) if action == 'read' else (record.candidate, record.published_raw)):
                if raw is not None:
                    budget(raw)
        values = (record.published,) if action == 'read' else (record.candidate, record.published)
        for raw in values:
            if raw is not None:
                decode_snapshot(raw, record.policy.connection)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise StorageError('invalid_artifact_index') from None
    if action == 'read' and not publication_current(policy,record):
        return replace(record,published_raw=None,published_revision=None)
    return record


def save(connection, actor, scope, record, candidate, published, published_revision, action, value_digest, *, provenance=None):
    revision = record.revision + 1
    if revision > MAX_REVISION:
        raise PolicyError(409,'index_revision_exhausted')
    try:
        if action == 'approve':
            if published is None:
                raise ValueError('approval requires publication')
            persisted = publication_text(published,provenance)
        else:
            if provenance is not None or published != record.published or published_revision != record.published_revision:
                raise ValueError('refresh cannot replace publication')
            persisted = record.published_raw
    except (ValueError, TypeError, KeyError, RecursionError):
        raise PolicyError(409,'invalid_publication') from None
    changed = connection.execute(f'UPDATE {_TABLE} SET revision=?,candidate=?,published=?,published_revision=? WHERE workspace=? AND connection=? AND bucket=? AND revision=? AND policy=?',
                                 (revision, candidate, persisted, published_revision, *scope_key(scope), record.revision, record.policy_raw)).rowcount
    if changed != 1:
        raise PolicyError(412, 'stale_revision')
    connection.execute(f'INSERT INTO {_EVENTS}(actor,workspace,connection,bucket,action,revision,digest) VALUES (?,?,?,?,?,?,?)',
                       (actor, *scope_key(scope), action, revision, value_digest))
    return revision


def regrant_index(store, config, *, actor_id, expected_revision, expected_grant_revision):
    """Explicit destructive authority replacement; never called by initializer."""
    if type(store) is not SQLiteWorkspaceService or type(config) is not IndexPolicy:
        raise ValueError('configured index regrant required')
    scope = config.connection.scope
    def operation(policy, connection):
        policy._admin(actor_id,scope.workspace_id)
        record = load(connection,scope,allow_legacy=True)
        policy._match(expected_revision,record.revision)
        if type(expected_grant_revision) is not int or expected_grant_revision != record.grant_revision:
            raise PolicyError(412,'stale_grant_revision')
        try:
            if record.candidate is not None:
                decode_snapshot(record.candidate,record.policy.connection)
            _publication(record.published_raw,record.policy.connection,legacy=record.authority is None)
            authority = capture_authority(policy,recipients(config))
        except (ValueError,TypeError,KeyError,AttributeError,RecursionError):
            raise PolicyError(400,'invalid_index_regrant') from None
        if record.revision >= MAX_REVISION or record.grant_revision >= MAX_REVISION:
            raise PolicyError(409,'index_revision_exhausted')
        raw = bound_policy_text(config,authority,record.grant_revision+1)
        changed = connection.execute(f'UPDATE {_TABLE} SET policy=?,revision=revision+1,candidate=NULL,published=NULL,published_revision=NULL WHERE workspace=? AND connection=? AND bucket=? AND revision=? AND policy=?',
            (raw,*scope_key(scope),record.revision,record.policy_raw)).rowcount
        if changed != 1:
            raise PolicyError(412,'stale_revision')
        policy._changed(actor_id,scope.workspace_id,'resource.update',
            'artifact-grant:'+digest(dump(scope_key(scope))),record.revision+1)
        return load(connection,scope)
    return store._transaction(operation,write=True,with_connection=True)
