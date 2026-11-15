# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Trusted same-authority refresh admission. Never an identity or grant endpoint."""
from dataclasses import dataclass, field
import math
import re
import time
from uuid import uuid4

from packages.artifact_sync import ArtifactSynchronizer
from packages.artifact_sync.provider import _now, ProviderError
from packages.artifact_sync.records import authorized, check_schema as check_indexes, digest, scope_key, flow_pin, MAX_REVISION
from packages.workspaces import SQLiteWorkspaceService, PolicyError, StorageError
from .transport import BoundedOBSReadTransport, RefreshCleanupError

_TABLE = 'artifact_refresh_leases_v1'
_CREATE = f'''CREATE TABLE {_TABLE} (
 id TEXT PRIMARY KEY NOT NULL, actor TEXT NOT NULL REFERENCES principals(id),
 workspace TEXT NOT NULL, connection TEXT NOT NULL, bucket TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision>=1), policy_digest TEXT NOT NULL,
 started REAL NOT NULL, expires REAL NOT NULL CHECK(expires>started),
 state TEXT NOT NULL CHECK(state IN ('active','complete','failed')),
 result_revision INTEGER CHECK(result_revision IS NULL OR result_revision>revision),
 CHECK((state='complete' AND result_revision IS NOT NULL) OR
       (state!='complete' AND result_revision IS NULL)),
 FOREIGN KEY(workspace,connection,bucket)
 REFERENCES artifact_indexes_v1(workspace,connection,bucket)) STRICT'''
TTL_SECONDS = 30


def check_schema(db):
    row = db.execute('SELECT type,sql FROM sqlite_schema WHERE name=?', (_TABLE,)).fetchone()
    norm = lambda sql: re.sub(r'\s+', ' ', sql).strip()
    if row is None or row[0] != 'table' or type(row[1]) is not str or norm(row[1]) != norm(_CREATE):
        raise StorageError('invalid_refresh_schema')


def initialize_refresh(store):
    """Explicit trusted setup only; never run from a request or host constructor."""
    if type(store) is not SQLiteWorkspaceService:
        raise ValueError('initialized policy store required')
    def operation(policy, db):
        check_indexes(db)
        db.execute(_CREATE.replace('CREATE TABLE', 'CREATE TABLE IF NOT EXISTS', 1))
        check_schema(db)
    store._transaction(operation, write=True, with_connection=True)


@dataclass(frozen=True, slots=True)
class ArtifactRefreshConfiguration:
    credential_loader: object = field(repr=False)
    transport: object = field(default=None, repr=False)

    def __post_init__(self):
        if not callable(self.credential_loader):
            raise ValueError('explicit scoped credential loader required')
        if self.transport is not None and not callable(getattr(self.transport, 'list_page', None)):
            raise ValueError('explicit bounded page transport required')


class ConfiguredArtifactRefresh:
    def __init__(self, *, service, transport=None, monotonic=time.monotonic):
        if type(service) is not ArtifactSynchronizer or not callable(monotonic):
            raise ValueError('configured synchronizer required')
        self.service, self._monotonic = service, monotonic
        boundary = service._session_boundary
        if boundary is None:
            raise ValueError('cookie session authority required')
        self._store = boundary._sessions.policy
        if type(self._store) is not SQLiteWorkspaceService:
            raise ValueError('same policy store required')
        self._transport = BoundedOBSReadTransport() if transport is None else transport

    def refresh(self, assertion, scope, *, expected_revision):
        started = self._tick(); ident = str(uuid4())
        def admit(policy, actor, db):
            record = authorized(policy, actor, db, scope, 'refresh')
            policy._match(expected_revision, record.revision)
            if record.revision >= MAX_REVISION:
                raise PolicyError(409,'index_revision_exhausted')
            check_schema(db)
            rows = db.execute(f'SELECT actor,workspace,connection,bucket FROM {_TABLE} WHERE state=?', ('active',)).fetchall()
            if len(rows) >= 2 or any(r[0] == actor or tuple(r[1:]) == scope_key(scope) for r in rows):
                raise PolicyError(429, 'refresh_capacity')
            now = _now(self.service._clock).timestamp()
            if not 0 <= self._tick() - started < TTL_SECONDS:
                raise PolicyError(504, 'refresh_deadline')
            db.execute(f'INSERT INTO {_TABLE} VALUES (?,?,?,?,?,?,?,?,?,?,NULL)',
                (ident, actor, *scope_key(scope), expected_revision, digest(record.policy_raw), now, now + TTL_SECONDS, 'active'))
            return actor, flow_pin(policy,actor,scope)
        def deadline():
            if not 0 <= self._tick() - started < TTL_SECONDS:
                raise PolicyError(504, 'refresh_deadline')
        actor, authority = self.service._authenticated_transaction(assertion, admit, write=True, with_connection=True, before_commit=deadline)
        lease = _Lease(self, ident, actor, scope, expected_revision, started, authority)
        try:
            return self.service._refresh_verified(assertion, scope, expected_revision=expected_revision, lifecycle=lease)
        except RefreshCleanupError:
            # Uncertain owned socket resources require trusted host recovery.
            raise
        except BaseException:
            if not lease.cleanup_uncertain:
                lease.fail()
            raise

    def _tick(self):
        value = self._monotonic()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ProviderError('invalid_clock')
        return value


class _Lease:
    def __init__(self, owner, ident, actor, scope, revision, started, authority):
        self.owner, self.ident, self.actor, self.scope = owner, ident, actor, scope
        self.revision, self.started, self.authority = revision, started, authority
        self.transport = self
        self.cleanup_uncertain = False

    def remaining(self):
        elapsed = self.owner._tick() - self.started
        if elapsed < 0 or elapsed >= TTL_SECONDS:
            raise PolicyError(504, 'refresh_deadline')
        return TTL_SECONDS - elapsed

    def guard(self, policy, actor, db):
        self.remaining(); check_schema(db)
        row = db.execute(f'SELECT actor,workspace,connection,bucket,revision,policy_digest,started,expires,state FROM {_TABLE} WHERE id=?', (self.ident,)).fetchone()
        record = authorized(policy, actor, db, self.scope, 'refresh')
        now = _now(self.owner.service._clock).timestamp()
        expected = (self.actor, *scope_key(self.scope), self.revision, digest(record.policy_raw))
        if row is None or tuple(row[:6]) != expected or flow_pin(policy,actor,self.scope) != self.authority or row[8] != 'active':
            raise PolicyError(412, 'refresh_changed')
        if not row[6] <= now < row[7]:
            raise PolicyError(504, 'refresh_deadline')

    def list_page(self, connection, credential, marker, now):
        remaining = self.remaining()
        try:
            result = self.owner._transport.list_page(connection, credential, marker, now, remaining=remaining)
        except RefreshCleanupError:
            self.cleanup_uncertain = True
            raise
        self.remaining()
        return result

    def complete(self, db, revision):
        self.remaining(); check_schema(db)
        changed = db.execute(f'UPDATE {_TABLE} SET state=?,result_revision=? WHERE id=? AND actor=? AND state=?',
            ('complete', revision, self.ident, self.actor, 'active')).rowcount
        if changed != 1:
            raise StorageError('refresh_lease_changed')

    def fail(self):
        def finish(policy, db):
            check_schema(db)
            changed = db.execute(f'UPDATE {_TABLE} SET state=? WHERE id=? AND actor=? AND state=?',
                ('failed', self.ident, self.actor, 'active')).rowcount
            if changed != 1:
                raise StorageError('refresh_lease_changed')
        self.owner._store._transaction(finish, write=True, with_connection=True)
