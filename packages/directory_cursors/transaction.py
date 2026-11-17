# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Private receipt-only S0-S7 controller for the three configured directories."""
from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
import json
import secrets
import sqlite3

from packages.identity import AuthenticationError, AuthenticationUnavailable
from packages.workspaces import PolicyError, StorageError
from packages.workspaces.authority import principal_pin
from .store import (ReceiptStore, Row, ACTIONS, PARTITIONS, RECEIPTS, WHERE, TTL,
                    MAX_TIME, QUOTA, check_schema, identifier, digest, token_value,
                    unavailable, changed)


@dataclass(frozen=True)
class Projection:
    """Trusted action-specific authority/visibility view; no client-supplied fence."""
    authority: bytes = field(repr=False)
    visibility: bytes = field(repr=False)
    value: object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        digest(self.authority)
        digest(self.visibility)


@dataclass(frozen=True)
class ReceiptOnly:
    store: ReceiptStore = field(repr=False)
    workspace: str
    action: str
    projection: object = field(repr=False)
    allow_refresh: bool = False

    def __post_init__(self):
        identifier(self.workspace)
        if (type(self.store) is not ReceiptStore or self.action not in ACTIONS
                or not callable(self.projection) or type(self.allow_refresh) is not bool
                or (self.allow_refresh and self.action != 'artifact.scopes.admin')):
            raise ValueError('trusted receipt operation required')

    def gate(self, policy, actor):
        if self.action == 'pipeline.scopes.read':
            policy._authorize(actor, self.workspace, 'pipelines', 'read')
        elif self.action == 'artifact.scopes.read':
            policy._authorize(actor, self.workspace, 'artifacts', 'read')
        else:
            policy._admin(actor, self.workspace)
            try:
                policy._authorize(actor, self.workspace, 'artifacts', 'read')
            except PolicyError as error:
                if error.status != 403:
                    raise
                policy._authorize(actor, self.workspace, 'artifacts', 'write')

    def context(self, policy, actor):
        self.gate(policy, actor)
        module = policy._modules[(self.workspace, 'pipelines' if self.action == ACTIONS[0] else 'artifacts')]
        member = policy._memberships[(self.workspace, actor)]
        space = policy._workspaces[self.workspace]
        value = [1, self.action, {}, 1, 25, principal_pin(policy, actor),
                 [space.id, space.state, space.revision],
                 [member.role, member.state, member.revision, policy._membership_authority[(self.workspace, actor)]],
                 [module.key, module.state, module.capabilities, module.revision], self.allow_refresh]
        return sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('ascii')).digest()


@dataclass(frozen=True)
class _DirectoryUnavailable:
    """Fixed-action denial plan, never a store/projection or receipt capability.

    Unconfigured directories still need the same final current-authentication
    path on gate/W05 rejection. This plan cannot construct/arm a receipt context.
    It is private to the three directory adapters, not an ordinary read mode.
    """
    workspace: str
    action: str

    def __post_init__(self):
        identifier(self.workspace)
        if self.action not in ACTIONS:
            raise ValueError('fixed directory action required')


# SQLite PRAGMAs with no argument are NOT necessarily read-only. Only the
# actual metadata reads used by this transaction are admitted, with fixed DBs.
_READ_PRAGMAS = frozenset({
    ('main', 'application_id'), ('main', 'user_version'),
    ('main', 'foreign_key_check'), ('main', 'database_list'),
    ('main', 'journal_mode'), ('main', 'synchronous'),
    ('login', 'application_id'), ('login', 'user_version'),
    ('login', 'journal_mode'), ('login', 'synchronous'),
})


class _SQL:
    """Deny raw callback writes. Reset authorizer on every phase/cache boundary."""
    def __init__(self, db):
        self.db, self.allowed, self.control = db, frozenset(), False
        self.reset()

    def authorize(self, code, a, b, schema, source):
        if code in (sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if code == sqlite3.SQLITE_PRAGMA:
            database = 'main' if schema is None else schema
            if (b is None and source is None and type(a) is str and a.isascii()
                    and type(database) is str and database.isascii()
                    and (database.lower(), a.lower()) in _READ_PRAGMAS):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if code in (sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT) and self.control:
            return sqlite3.SQLITE_OK
        if (schema, a, code) in self.allowed and source is None:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def reset(self):
        self.db.set_authorizer(self.authorize)

    @contextmanager
    def permit(self, *allowed, control=False):
        if self.allowed or self.control:
            raise unavailable()
        self.allowed, self.control = frozenset(allowed), control
        self.reset()
        try:
            yield
        finally:
            self.allowed, self.control = frozenset(), False
            self.reset()

    def sql(self, statement):
        with self.permit(control=True):
            self.db.execute(statement)

    def commit(self):
        with self.permit(control=True):
            self.db.commit()


class _ReceiptContext:
    def __init__(self, db, sql, service, plan, policy, actor):
        self.db, self.sql, self.service, self.plan = db, sql, service, plan
        self.policy, self.actor, self.pin = policy, actor, principal_pin(policy, actor)
        self.key = (actor, plan.workspace, plan.action)
        self.armed = self.savepoint = False
        self.original = self.expected = None
        self.observed = self.first = None
        self.projected = self.context = None
        self.used = []
        self.issued = False

    def arm(self):
        if self.armed:
            raise unavailable()
        self.plan.gate(self.policy, self.actor)
        if principal_pin(self.policy, self.actor) != self.pin:
            raise AuthenticationError()
        if (type(self.actor) is not str or not 0 < len(self.actor.encode('utf-8')) <= 800
                or any(ord(c) < 32 or ord(c) == 127 for c in self.actor)):
            raise unavailable()
        self.plan.store._same_store(self.service, self.db)
        check_schema(self.db, self.policy._realm)
        self.original = self.plan.store._snapshot(self.db, self.key)
        floor, rows = self.original
        if floor is not None and any(row.issued_us > floor for row in rows):
            raise unavailable()
        now = self.plan.store._now()
        if floor is not None and now < floor:
            raise unavailable()
        self.armed, self.observed, self.first = True, now, now
        self.sql.sql('SAVEPOINT receipt_work')
        self.savepoint = True
        if floor is None:
            with self.sql.permit(('main', PARTITIONS, sqlite3.SQLITE_INSERT)):
                self.db.execute(f'INSERT INTO main.{PARTITIONS} VALUES(?,?,?,?)', (*self.key, now))
        self.expected = (now if floor is None else floor, rows)

    def _observe(self):
        now = self.plan.store._now()
        if now < self.observed:
            raise unavailable()
        self.observed = now
        return now

    def view(self):
        if not self.armed or not self.savepoint:
            raise unavailable()
        if self.projected is None:
            self.context = self.plan.context(self.policy, self.actor)
            value = self.plan.projection(self.policy, self.actor, self.db)
            if type(value) is not Projection:
                raise unavailable()
            self.projected = value
        return self.projected.value

    def resume(self, token):
        self.view()
        token = token_value(token)
        # All rows belong to the verified partition; never query another actor.
        row = next((r for r in self.expected[1] if r.token == token), None)
        if (row is None or self.observed >= row.expires_us or row.issued_us > self.observed
                or (row.context, row.authority, row.visibility) !=
                (self.context, self.projected.authority, self.projected.visibility)):
            raise changed()
        if self.used:
            raise unavailable()
        self.used.append(row)
        return row.connection_id, row.object_id

    def issue(self, anchor):
        self.view()
        if self.issued or type(anchor) is not tuple or len(anchor) != 2:
            raise unavailable()
        self.issued = True
        a, b = map(identifier, anchor)
        if self.observed > MAX_TIME - TTL:
            raise unavailable()
        rows = list(self.expected[1])
        alive = [r for r in rows if self.observed < r.expires_us]
        with self.sql.permit(('main', RECEIPTS, sqlite3.SQLITE_DELETE)):
            self.db.execute(f'DELETE FROM main.{RECEIPTS} WHERE {WHERE} AND expires_us<=?', (*self.key, self.observed))
        self.expected = self.expected[0], tuple(alive)
        position = self.context, self.projected.authority, self.projected.visibility, a, b
        existing = next((r for r in alive if r.position == position), None)
        if existing is not None:
            self.used.append(existing)
            return existing.token
        if len(alive) >= QUOTA:
            raise unavailable()
        for _ in range(3):
            token = 'dcr1_' + secrets.token_urlsafe(32)
            token_value(token)
            row = Row(token, *position, self.observed, self.observed + TTL)
            try:
                with self.sql.permit(('main', RECEIPTS, sqlite3.SQLITE_INSERT)):
                    self.db.execute(f'INSERT INTO main.{RECEIPTS} VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (token, *self.key, *position, row.issued_us, row.expires_us))
            except sqlite3.IntegrityError:
                # A random collision is the only retryable constraint failure.
                collision = self.db.execute(f'SELECT 1 FROM main.{RECEIPTS} WHERE token=?', (token,)).fetchone()
                if collision is not None:
                    continue
                raise unavailable() from None
            self.expected = self.expected[0], tuple(sorted((*alive, row), key=lambda r: r.token))
            self.used.append(row)
            return token
        raise unavailable()

    def verify(self, *, projection):
        check_schema(self.db, self.policy._realm)
        if self.plan.store._snapshot(self.db, self.key) != self.expected:
            raise unavailable()
        policy = self.service._load(self.db)
        if projection:
            if self.projected is None:
                raise unavailable()
            context = self.plan.context(policy, self.actor)
            current = self.plan.projection(policy, self.actor, self.db)
            if type(current) is not Projection:
                raise unavailable()
            if context != self.context or current != self.projected:
                raise changed()
        return policy

    def observe_final(self):
        now = self._observe()
        if any(now >= row.expires_us for row in self.used):
            raise changed()

    def restore(self):
        if self.savepoint:
            self.sql.sql('ROLLBACK TO receipt_work')
            self.sql.sql('RELEASE receipt_work')
            self.savepoint = False
            self.expected = self.original
        check_schema(self.db, self.policy._realm)
        if self.plan.store._snapshot(self.db, self.key) != self.original:
            raise unavailable()

    def persist_floor(self, *, success):
        if self.savepoint:
            if not success:
                raise unavailable()
            self.sql.sql('RELEASE receipt_work')
            self.savepoint = False
        check_schema(self.db, self.policy._realm)
        floor, rows = self.expected
        if floor is None:
            with self.sql.permit(('main', PARTITIONS, sqlite3.SQLITE_INSERT)):
                count = self.db.execute(f'INSERT INTO main.{PARTITIONS} VALUES(?,?,?,?)', (*self.key, self.observed)).rowcount
        else:
            with self.sql.permit(('main', PARTITIONS, sqlite3.SQLITE_UPDATE)):
                count = self.db.execute(f'UPDATE main.{PARTITIONS} SET clock_floor_us=? WHERE {WHERE} AND clock_floor_us=?',
                    (self.observed, *self.key, floor)).rowcount
        if count != 1:
            raise unavailable()
        self.expected = self.observed, rows
        check_schema(self.db, self.policy._realm)
        if self.plan.store._snapshot(self.db, self.key) != self.expected:
            raise unavailable()


def validate_mode(plan, *, write, with_connection, before_commit):
    if type(plan) not in (ReceiptOnly, _DirectoryUnavailable) or write is not False or with_connection is not True or before_commit is not None:
        raise ValueError('receipt-only requires read connection and no extra commit hook')


def _safe(error):
    if isinstance(error, (AuthenticationError, AuthenticationUnavailable, PolicyError, StorageError)):
        return error
    return unavailable()


def _identity_projection(db, actor):
    return (db.execute("""SELECT substr(CAST(issuer AS BLOB),1,801),substr(CAST(subject AS BLOB),1,801),
        active,generation,authority_epoch FROM main.principals WHERE id=?""", (actor,)).fetchone(),
        db.execute('SELECT substr(CAST(realm AS BLOB),1,33),epoch FROM main.metadata WHERE id=1').fetchone())


def run(db, service, plan, callback, current, *, security_clock=None):
    """Own the sole transaction/finalizer. current performs real boundary auth.

    callback has four args. No caller may commit or nest the old business mode.
    Ordinary errors retain clock floors; unrecoverable I/O cannot promise this.
    """
    sql = _SQL(db)
    ctx = None
    actor = pin = None
    pending = fatal = None
    result = None
    cleanup_failed = False
    try:
        sql.sql('BEGIN IMMEDIATE')
        try:
            if security_clock is not None:
                security_clock.begin(db)
            try:
                policy = service._load(db)
            except sqlite3.Error:
                # Preserve the existing boundary's pre-authentication loader
                # failure code. This does not certify a current identity.
                if security_clock is not None:
                    raise AuthenticationUnavailable() from None
                raise StorageError('invalid_storage') from None
            actor = current(policy, db)
            pin = principal_pin(policy, actor)
            if type(plan) is ReceiptOnly:
                ctx = _ReceiptContext(db, sql, service, plan, policy, actor)
            # The unavailable-directory plan has no receipt context, storage
            # handle or dedicated receipt schema access. Even a returning callback
            # cannot succeed; the original global policy loader remains unchanged.
            result = callback(policy, actor, db, ctx)
            if ctx is None or not ctx.armed:
                raise unavailable()
        except BaseException as error:
            pending = error
            if not isinstance(error, Exception):
                fatal = error

        if ctx is not None and ctx.armed:
            try:
                if pending is None:
                    ctx.verify(projection=True)  # The only second projection pass.
            except BaseException as error:
                if not isinstance(error, Exception):
                    fatal = fatal or error
                pending = pending or error
            try:
                # A recoverable projection/codec error must not skip this clock.
                ctx.observe_final()
            except BaseException as error:
                if not isinstance(error, Exception):
                    fatal = fatal or error
                if pending is None or (isinstance(error, PolicyError) and error.status == 503):
                    pending = error
            if pending is not None:
                ctx.restore()

        # Callback exceptions do NOT bypass the final current-identity check.
        if actor is not None:
            policy = service._load(db)
            try:
                final_actor = current(policy, db)
                if final_actor != actor or principal_pin(policy, final_actor) != pin:
                    raise AuthenticationError()
            except BaseException as error:
                pending = error
                if not isinstance(error, Exception):
                    fatal = fatal or error
            else:
                if ctx is not None and ctx.armed:
                    try:
                        plan.gate(policy, actor)
                    except PolicyError as error:
                        pending = error
            if pending is not None and ctx is not None and ctx.armed:
                ctx.restore()

        # Last auth has run. Only exact clock DML/readback and commit remain.
        identity_before = _identity_projection(db, actor) if actor is not None else None
        login_before = security_clock.projection(db) if security_clock is not None else None
        if ctx is not None and ctx.armed:
            ctx.persist_floor(success=pending is None and fatal is None)
        if security_clock is not None:
            security_clock.persist(db, sql)
        if actor is not None and _identity_projection(db, actor) != identity_before:
            raise unavailable()
        if security_clock is not None and security_clock.projection(db) != login_before:
            raise unavailable()
        sql.commit()
    except BaseException as failure:
        # Storage/schema/commit failure never emits a result. No second connection
        # or automatic replay; uncommitted observations may be lost on disk fault.
        try:
            with sql.permit(control=True):
                db.rollback()
        except BaseException:
            pass
        if fatal is not None:
            raise fatal from None
        if not isinstance(failure, Exception):
            raise
        raise unavailable() from None
    finally:
        try:
            db.set_authorizer(None)
        except Exception:
            cleanup_failed = True
    if fatal is not None:
        raise fatal
    if cleanup_failed:
        raise unavailable()
    if pending is not None:
        raise _safe(pending) from None
    return result
