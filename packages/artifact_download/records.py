# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Trusted byte grants and durable admission leases, in the policy database."""
from dataclasses import asdict, dataclass
import re

from packages.artifacts import Scope
from packages.artifacts.catalog import instant
from packages.artifact_sync.provider import object_key
from packages.artifact_sync.records import dump, _json, scope_key, load as load_index
from packages.workspaces import SQLiteWorkspaceService, PolicyError, StorageError

MAX_BYTES = 5 * 1024 * 1024
TTL_SECONDS = 30
CHUNK_BYTES = 65536
_TABLE = 'artifact_download_policies_v1'
_LEASES = 'artifact_download_leases_v1'
_CREATE = f'''CREATE TABLE {_TABLE} (
 workspace TEXT NOT NULL, connection TEXT NOT NULL, bucket TEXT NOT NULL, policy TEXT NOT NULL,
 PRIMARY KEY(workspace,connection,bucket), FOREIGN KEY(workspace,connection,bucket)
 REFERENCES artifact_indexes_v1(workspace,connection,bucket)) STRICT'''
_LEASE_CREATE = f'''CREATE TABLE {_LEASES} (
 id TEXT PRIMARY KEY NOT NULL, actor TEXT NOT NULL REFERENCES principals(id),
 workspace TEXT NOT NULL, connection TEXT NOT NULL, bucket TEXT NOT NULL,
 key_digest TEXT NOT NULL, published_revision INTEGER NOT NULL CHECK(published_revision>=1),
 size INTEGER NOT NULL CHECK(size>=0 AND size<=5242880),
 started REAL NOT NULL, expires REAL NOT NULL CHECK(expires>started),
 state TEXT NOT NULL CHECK(state IN ('active','complete','interrupted','failed')),
 offered INTEGER NOT NULL CHECK(offered>=0 AND offered<=size),
 FOREIGN KEY(workspace,connection,bucket)
 REFERENCES {_TABLE}(workspace,connection,bucket)) STRICT'''


@dataclass(frozen=True, slots=True)
class ContentBinding:
    """Independent trusted content approval. Never inferred from catalog metadata."""
    key: str
    size: int
    modified_at: str
    etag: str
    sha256: str

    def __post_init__(self):
        object_key(self.key)
        if self.key.endswith('/') or type(self.size) is not int or not 0 <= self.size <= MAX_BYTES:
            raise ValueError('invalid byte binding')
        dt = instant(self.modified_at)
        # OBS Last-Modified is an HTTP date; fractional timestamps unsupported.
        if dt.microsecond:
            raise ValueError('whole second timestamp required')
        object.__setattr__(self, 'modified_at', dt.isoformat())
        if (type(self.etag) is not str or not re.fullmatch(r'"[A-Za-z0-9._-]{1,128}"', self.etag)
                or type(self.sha256) is not str or not re.fullmatch(r'[0-9a-f]{64}', self.sha256)):
            raise ValueError('strong ETag and SHA256 required')


@dataclass(frozen=True, slots=True)
class DownloadPolicy:
    scope: Scope
    downloaders: tuple[str, ...]
    bindings: tuple[ContentBinding, ...]

    def __post_init__(self):
        if type(self.scope) is not Scope:
            raise ValueError('exact scope required')
        values = self.downloaders
        if (type(values) not in (list, tuple) or not 1 <= len(values) <= 100
                or any(type(v) is not str or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', v) for v in values)
                or len(set(values)) != len(values)):
            raise ValueError('explicit downloaders required')
        object.__setattr__(self, 'downloaders', tuple(sorted(values)))
        if (type(self.bindings) not in (list, tuple) or not 1 <= len(self.bindings) <= 500
                or any(type(v) is not ContentBinding for v in self.bindings)
                or len({v.key for v in self.bindings}) != len(self.bindings)):
            raise ValueError('bounded unique content bindings required')
        object.__setattr__(self, 'bindings', tuple(sorted(self.bindings, key=lambda v: v.key)))


def policy_text(policy):
    if type(policy) is not DownloadPolicy:
        raise ValueError('validated policy required')
    raw = dump({'schema': 1, **asdict(policy)})
    if len(raw) > 1048576:
        raise ValueError('oversized policy')
    return raw


def check_schema(connection):
    normalize = lambda sql: re.sub(r'\s+', ' ', sql).strip()
    for name, expected in ((_TABLE, _CREATE), (_LEASES, _LEASE_CREATE)):
        row = connection.execute('SELECT type,sql FROM sqlite_schema WHERE name=?', (name,)).fetchone()
        if row is None or row[0] != 'table' or type(row[1]) is not str or normalize(row[1]) != normalize(expected):
            raise StorageError('invalid_download_schema')


def load(connection, scope):
    check_schema(connection)
    row = connection.execute(f'SELECT policy FROM {_TABLE} WHERE workspace=? AND connection=? AND bucket=?', scope_key(scope)).fetchone()
    if row is None:
        raise PolicyError(404, 'not_found')
    try:
        raw = row[0]
        value = _json(raw)
        if type(value) is not dict or set(value) != {'schema', 'scope', 'downloaders', 'bindings'} or type(value.pop('schema')) is not int:
            raise ValueError()
        value['scope'] = Scope(**value['scope'])
        value['bindings'] = tuple(ContentBinding(**v) for v in value['bindings'])
        result = DownloadPolicy(**value)
        if result.scope != scope or policy_text(result) != raw:
            raise ValueError()
        return result, raw
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise StorageError('invalid_download_policy') from None


def initialize_downloads(store, policies):
    """Trusted, additive/idempotent setup. Rotation is an explicit host migration."""
    if type(store) is not SQLiteWorkspaceService:
        raise ValueError('configured store required')
    policies = tuple(policies)
    if (not 1 <= len(policies) <= 100 or any(type(p) is not DownloadPolicy for p in policies)
            or len({p.scope for p in policies}) != len(policies)):
        raise ValueError('bounded unique policies required')
    def operation(policy, connection):
        for sql in (_CREATE, _LEASE_CREATE):
            connection.execute(sql.replace('CREATE TABLE', 'CREATE TABLE IF NOT EXISTS', 1))
        check_schema(connection)
        for p in policies:
            index = load_index(connection, p.scope)
            if (any(v not in policy._principals for v in p.downloaders)
                    or any(not b.key.startswith(index.policy.connection.prefix) for b in p.bindings)):
                raise ValueError('unprovisioned download policy')
            raw = policy_text(p)
            row = connection.execute(f'SELECT policy FROM {_TABLE} WHERE workspace=? AND connection=? AND bucket=?', scope_key(p.scope)).fetchone()
            if row is not None:
                if row[0] != raw:
                    raise ValueError('existing download policy cannot be replaced')
            else:
                connection.execute(f'INSERT INTO {_TABLE} VALUES (?,?,?,?)', (*scope_key(p.scope), raw))
    store._transaction(operation, write=True, with_connection=True)
