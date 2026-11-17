# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Same-authority admission and atomic completion, not a new identity boundary."""
from dataclasses import dataclass, field
import hashlib
import math
import re
import time
from uuid import uuid4

from packages.community_api import CommunityApplication
from packages.community_api.records import _SCHEMA, _MAX_REVISION, read
from packages.community_sync import GitCodeProvider, ProviderError
from packages.workspaces import SQLiteWorkspaceService, PolicyError, StorageError
from packages.workspaces.authority import principal_pin
from .transport import BoundedGitCodeReadTransport, RefreshCleanupError

_TABLE = 'community_refresh_leases_v1'
_CREATE = f'''CREATE TABLE {_TABLE} (
 id TEXT PRIMARY KEY NOT NULL, actor TEXT NOT NULL REFERENCES principals(id),
 workspace TEXT NOT NULL, connection TEXT NOT NULL, repository TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision>=1), config_digest TEXT NOT NULL,
 started REAL NOT NULL, expires REAL NOT NULL CHECK(expires>started),
 state TEXT NOT NULL CHECK(state IN ('active','complete','failed')),
 result_revision INTEGER CHECK(result_revision IS NULL OR result_revision=revision+1),
 CHECK((state='complete' AND result_revision IS NOT NULL) OR
       (state!='complete' AND result_revision IS NULL)),
 FOREIGN KEY(workspace,connection,repository)
 REFERENCES community_connections(workspace_id,connection_id,repository)) STRICT'''
_INDEX = f'CREATE INDEX community_refresh_active_v1 ON {_TABLE}(state)'
TTL_SECONDS = 30


def check_schema(db):
    normalize = lambda sql: re.sub(r'\s+', ' ', sql).strip()
    expected = {'community_connections': ('table', _SCHEMA), _TABLE: ('table', _CREATE),
                'community_refresh_active_v1': ('index', _INDEX)}
    rows = db.execute("SELECT name,type,sql FROM sqlite_schema WHERE tbl_name IN (?,?) AND sql IS NOT NULL",
                      ('community_connections', _TABLE)).fetchall()
    actual = {name: (kind, normalize(sql)) for name, kind, sql in rows}
    if actual != {name: (kind, normalize(sql)) for name, (kind, sql) in expected.items()}:
        raise StorageError('invalid_community_refresh_schema')


def initialize_refresh(store):
    """Trusted explicit installation only. Never called by host or request."""
    if type(store) is not SQLiteWorkspaceService:
        raise ValueError('initialized policy store required')
    def install(policy, db):
        db.execute(_CREATE.replace('CREATE TABLE', 'CREATE TABLE IF NOT EXISTS', 1))
        db.execute(_INDEX.replace('CREATE INDEX', 'CREATE INDEX IF NOT EXISTS', 1))
        check_schema(db)
    store._transaction(install, write=True, with_connection=True)


def _key(scope):
    return (scope.workspace_id, scope.connection_id, scope.repository)


def _digest(text):
    return hashlib.sha256(text.encode('ascii')).hexdigest()


def _pin(policy, actor, scope):
    return (principal_pin(policy, actor), policy._memberships[(scope.workspace_id, actor)].revision,
            policy._workspaces[scope.workspace_id].revision,
            policy._modules[(scope.workspace_id, 'community')].revision)


def _persisted_policy(store, policy, db):
    """Catch cross-table triggers after policy/audit persistence, under its lock."""
    saved = store._load(db)
    for name in ('_principals', '_workspaces', '_memberships', '_modules', '_resources',
                 '_operators', '_membership_authority', '_operator_authority', '_audit', '_epoch', '_realm'):
        if getattr(saved, name) != getattr(policy, name):
            raise StorageError('refresh_policy_changed')
    return saved


@dataclass(frozen=True, slots=True)
class CommunityRefreshConfiguration:
    credential_loader: object = field(repr=False)
    transport: object = field(default=None, repr=False)

    def __post_init__(self):
        if not callable(self.credential_loader):
            raise ValueError('explicit scoped credential loader required')
        if self.transport is not None and not callable(getattr(self.transport, 'get', None)):
            raise ValueError('explicit bounded GET transport required')


class ConfiguredCommunityRefresh:
    def __init__(self, *, service, transport=None, monotonic=time.monotonic):
        if type(service) is not CommunityApplication or type(service._provider) is not GitCodeProvider or not callable(monotonic):
            raise ValueError('configured community application required')
        if service._session_boundary is None:
            raise ValueError('cookie session authority required')
        self.service, self._monotonic = service, monotonic
        self._store = service._session_boundary._sessions.policy
        if type(self._store) is not SQLiteWorkspaceService:
            raise ValueError('same policy store required')
        self._transport = BoundedGitCodeReadTransport() if transport is None else transport

    def _tick(self):
        value = self._monotonic()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ProviderError('invalid_clock')
        return value

    def refresh(self, assertion, scope, expected):
        started, ident = self._tick(), str(uuid4())
        lease = _Lease(self, ident, scope, expected, started)
        def admit(policy, actor, db):
            state = self.service._state(policy, actor, db, scope, 'refresh')
            policy._match(expected, state.revision)
            if state.revision >= _MAX_REVISION:
                raise PolicyError(409, 'connection_revision_exhausted')
            check_schema(db)
            active = lease.active_rows(db)
            # No TTL eviction: a blocked worker/socket still owns its reservation.
            if len(active) >= 2 or any(row[1] == actor or tuple(row[2:5]) == _key(scope) for row in active):
                raise PolicyError(429, 'refresh_capacity')
            now = lease.now(); lease.remaining()
            lease.actor, lease.authority = actor, _pin(policy, actor, scope)
            lease.row = (ident, actor, *_key(scope), expected, _digest(state.config_raw), now, now + TTL_SECONDS, 'active', None)
            lease.active = sorted(active + [lease.row])
            db.execute(f'INSERT INTO {_TABLE} VALUES (?,?,?,?,?,?,?,?,?,?,?)', lease.row)
            lease.expect(policy, db, read(db, scope), 'active')
        self.service._authenticated_transaction(assertion, admit, write=True, with_connection=True, before_commit=lease.verify_commit)
        try:
            return self.service._refresh(assertion, scope, expected, None, lifecycle=lease)
        except BaseException:
            if not lease.cleanup_uncertain:
                lease.fail()
            raise


class _Lease:
    def __init__(self, owner, ident, scope, revision, started):
        self.owner, self.ident, self.scope, self.revision, self.started = owner, ident, scope, revision, started
        self.cleanup_uncertain = False
        self.provider = GitCodeProvider(transport=self)

    def now(self):
        value = self.owner.service._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ProviderError('invalid_clock')
        return value.timestamp()

    def remaining(self):
        elapsed = self.owner._tick() - self.started
        if not 0 <= elapsed < TTL_SECONDS:
            raise PolicyError(504, 'refresh_deadline')
        return TTL_SECONDS - elapsed

    @staticmethod
    def active_rows(db):
        return db.execute(f"SELECT * FROM {_TABLE} WHERE state='active' ORDER BY id LIMIT 3").fetchall()

    def _row(self, db):
        check_schema(db)
        return db.execute(f'SELECT * FROM {_TABLE} WHERE id=?', (self.ident,)).fetchone()

    def guard(self, policy, actor, db):
        self.remaining()
        row = self._row(db)
        state = self.owner.service._state(policy, actor, db, self.scope, 'refresh')
        if (row != self.row or actor != self.actor or _pin(policy, actor, self.scope) != self.authority
                or state.revision != self.revision or _digest(state.config_raw) != self.row[6]):
            raise PolicyError(412, 'refresh_changed')
        if not self.row[7] <= self.now() < self.row[8]:
            raise PolicyError(504, 'refresh_deadline')

    def get(self, path, credential, connection):
        try:
            result = self.owner._transport.get(path, credential, connection, remaining=self.remaining())
        except RefreshCleanupError:
            self.cleanup_uncertain = True
            self.remaining()
            raise
        self.remaining()
        return result

    def expect(self, policy, db, state, status):
        self.commit = (policy, db, state, self.row[:9] + (status, self.revision + 1 if status == 'complete' else None))

    def verify_commit(self):
        self.remaining()
        policy, db, state, expected = self.commit
        saved = _persisted_policy(self.owner._store, policy, db)
        if (self._row(db) != expected or self.active_rows(db) != self.active or read(db, self.scope) != state
                or _pin(saved, self.actor, self.scope) != self.authority):
            raise StorageError('refresh_commit_changed')
        self.owner.service._state(saved, self.actor, db, self.scope, 'refresh')
        if not self.row[7] <= self.now() < self.row[8]:
            raise PolicyError(504, 'refresh_deadline')
        self.remaining()

    def complete(self, policy, db, state):
        self.remaining()
        if self._row(db) != self.row:
            raise StorageError('refresh_lease_changed')
        self.active = [row for row in self.active_rows(db) if row[0] != self.ident]
        changed = db.execute(f"UPDATE {_TABLE} SET state='complete',result_revision=? WHERE id=? AND state='active'",
                             (self.revision + 1, self.ident)).rowcount
        if changed != 1:
            raise StorageError('refresh_lease_changed')
        self.expect(policy, db, state, 'complete')

    def fail(self):
        expected = self.row[:9] + ('failed', None)
        context = []
        def finish(policy, db):
            if self._row(db) != self.row:
                raise StorageError('refresh_lease_changed')
            self.active = [row for row in self.active_rows(db) if row[0] != self.ident]
            db.execute(f"UPDATE {_TABLE} SET state='failed' WHERE id=? AND state='active'", (self.ident,))
            context.extend((policy, db, read(db, self.scope)))
        def verify():
            policy, db, state = context
            _persisted_policy(self.owner._store, policy, db)
            if self._row(db) != expected or self.active_rows(db) != self.active or read(db, self.scope) != state:
                raise StorageError('refresh_lease_changed')
        self.owner._store._transaction(finish, write=True, with_connection=True, before_commit=verify)
