# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Bounded opaque receipts, not identity, grants, or stable content snapshots."""
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import base64
import os
import re
import sqlite3

from packages.workspaces import PolicyError, SQLiteWorkspaceService, StorageError

META = 'directory_cursor_meta_v1'
PARTITIONS = 'directory_cursor_partitions_v1'
RECEIPTS = 'directory_cursor_receipts_v1'
PREFIX = 'directory_cursor_'
ACTIONS = ('pipeline.scopes.read', 'artifact.scopes.read', 'artifact.scopes.admin')
TTL = 300_000_000
QUOTA = 32
MAX_TIME = 253402300799999999
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')
_TOKEN = re.compile(r'dcr1_[A-Za-z0-9_-]{43}\Z')
KEY = 'actor_id,workspace_id,action'
WHERE = 'actor_id=? AND workspace_id=? AND action=?'
SCHEMA = (
    f"CREATE TABLE {META} (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL CHECK(version=1), realm TEXT NOT NULL CHECK(length(CAST(realm AS BLOB))=32)) STRICT",
    f"CREATE TABLE {PARTITIONS} (actor_id TEXT NOT NULL REFERENCES principals(id) CHECK(length(CAST(actor_id AS BLOB)) BETWEEN 1 AND 800), workspace_id TEXT NOT NULL REFERENCES workspaces(id) CHECK(length(CAST(workspace_id AS BLOB)) BETWEEN 1 AND 128), action TEXT NOT NULL CHECK(action IN {ACTIONS}), clock_floor_us INTEGER NOT NULL CHECK(clock_floor_us BETWEEN 0 AND {MAX_TIME}), PRIMARY KEY({KEY})) STRICT",
    f"CREATE TABLE {RECEIPTS} (token TEXT PRIMARY KEY CHECK(length(CAST(token AS BLOB))=48), actor_id TEXT NOT NULL, workspace_id TEXT NOT NULL, action TEXT NOT NULL, context BLOB NOT NULL CHECK(length(context)=32), authority BLOB NOT NULL CHECK(length(authority)=32), visibility BLOB NOT NULL CHECK(length(visibility)=32), connection_id TEXT NOT NULL CHECK(length(CAST(connection_id AS BLOB)) BETWEEN 1 AND 128), object_id TEXT NOT NULL CHECK(length(CAST(object_id AS BLOB)) BETWEEN 1 AND 128), issued_us INTEGER NOT NULL CHECK(issued_us BETWEEN 0 AND {MAX_TIME-TTL}), expires_us INTEGER NOT NULL CHECK(expires_us=issued_us+{TTL}), FOREIGN KEY({KEY}) REFERENCES {PARTITIONS}({KEY})) STRICT",
    f"CREATE UNIQUE INDEX directory_cursor_position_v1 ON {RECEIPTS}({KEY},context,authority,visibility,connection_id,object_id)",
)


def unavailable():
    return PolicyError(503, 'scope_directory_unavailable')


def changed():
    return PolicyError(409, 'scope_directory_changed')


def instant(value):
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise unavailable()
    value = (value.astimezone(timezone.utc) - EPOCH) // timedelta(microseconds=1)
    if not 0 <= value <= MAX_TIME:
        raise unavailable()
    return value


def identifier(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise unavailable()
    return value


def token_value(value):
    if type(value) is not str or _TOKEN.fullmatch(value) is None:
        raise changed()
    try:
        raw = base64.b64decode(value[5:] + '=', altchars=b'-_', validate=True)
    except (ValueError, TypeError):
        raise changed() from None
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=') != value[5:]:
        raise changed()
    return value


def _text(value, maximum, *, ascii_only=False):
    if type(value) is not bytes or not 0 < len(value) <= maximum:
        raise unavailable()
    try:
        result = value.decode('ascii' if ascii_only else 'utf-8', errors='strict')
    except UnicodeError:
        raise unavailable() from None
    if any(ord(c) < 32 or ord(c) == 127 for c in result):
        raise unavailable()
    return result


def digest(value):
    if type(value) is not bytes or len(value) != 32:
        raise unavailable()
    return value


def check_schema(db, realm):
    expected = {re.match(r'CREATE (?:UNIQUE INDEX|TABLE) ([a-z_0-9]+)', sql).group(1): sql for sql in SCHEMA}
    rows = db.execute("SELECT substr(CAST(name AS BLOB),1,101),type,substr(CAST(sql AS BLOB),1,4097) FROM main.sqlite_schema WHERE name GLOB 'directory_cursor_*' LIMIT 5").fetchall()
    rows = [(_text(name, 100, ascii_only=True), kind, _text(sql, 4096, ascii_only=True)) for name, kind, sql in rows]
    if set(r[0] for r in rows) != set(expected) or len(rows) != len(expected):
        raise unavailable()
    for name, kind, sql in rows:
        if kind not in ('table', 'index') or type(sql) is not str or ' '.join(sql.split()) != ' '.join(expected[name].split()):
            raise unavailable()
    # Reject triggers and extra explicit indexes on every touched auxiliary table.
    for table in (META, PARTITIONS, RECEIPTS):
        if db.execute("SELECT 1 FROM main.sqlite_schema WHERE tbl_name=? AND (type='trigger' OR (type='index' AND sql IS NOT NULL AND name!='directory_cursor_position_v1')) LIMIT 1", (table,)).fetchone():
            raise unavailable()
    if db.execute('SELECT 1 FROM temp.sqlite_schema LIMIT 1').fetchone():
        raise unavailable()
    rows = db.execute(f'SELECT id,version,substr(CAST(realm AS BLOB),1,33) FROM main.{META} LIMIT 2').fetchall()
    if rows != [(1, 1, realm.encode('ascii'))]:
        raise unavailable()


def _runtime_guard(path):
    try:
        parent = path.resolve(strict=True).parent
        if any(os.path.lexists(parent / name) for name in ('installation.json', '.runtime.lock')):
            raise StorageError('unsupported_layout')
    except OSError:
        raise StorageError('unsupported_layout') from None


def initialize_directory_cursors(policy, *, layout):
    """Explicit factory-only DDL; never a runtime migration or request fallback."""
    if type(policy) is not SQLiteWorkspaceService or layout != 'explicit-factory-v1':
        raise ValueError('explicit factory policy required')
    _runtime_guard(policy._path)
    try:
        with closing(policy._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                current = policy._load(db)
                _runtime_guard(policy._path)
                present = db.execute("SELECT 1 FROM main.sqlite_schema WHERE name GLOB 'directory_cursor_*' LIMIT 1").fetchone()
                if present is None:
                    for sql in SCHEMA:
                        db.execute(sql)
                    db.execute(f'INSERT INTO {META} VALUES(1,1,?)', (current._realm,))
                check_schema(db, current._realm)
                db.commit()
            except BaseException:
                db.rollback()
                raise
    except sqlite3.Error:
        raise unavailable() from None
    return ReceiptStore(policy)


@dataclass(frozen=True)
class Row:
    token: str = field(repr=False)
    context: bytes = field(repr=False)
    authority: bytes = field(repr=False)
    visibility: bytes = field(repr=False)
    connection_id: str
    object_id: str
    issued_us: int
    expires_us: int

    @property
    def position(self):
        return self.context, self.authority, self.visibility, self.connection_id, self.object_id


class ReceiptStore:
    """A trusted handle to one already initialized policy DB. No authority cache."""
    def __init__(self, policy, *, clock=lambda: datetime.now(timezone.utc)):
        if type(policy) is not SQLiteWorkspaceService or not callable(clock):
            raise ValueError('explicit policy and clock required')
        self._policy, self._clock = policy, clock
        try:
            with closing(policy._connect()) as db:
                db.execute('BEGIN')
                try:
                    check_schema(db, policy._load(db)._realm)
                finally:
                    db.rollback()
        except (PolicyError, StorageError):
            raise
        except Exception:
            raise unavailable() from None

    def _same_store(self, policy, db):
        if type(policy) is not SQLiteWorkspaceService or not os.path.samefile(self._policy._path, policy._path):
            raise unavailable()
        main = next((row[2] for row in db.execute('PRAGMA database_list') if row[1] == 'main'), None)
        if not main or not os.path.samefile(main, self._policy._path):
            raise unavailable()

    def _now(self):
        try:
            return instant(self._clock())
        except Exception:
            raise unavailable() from None

    @staticmethod
    def _snapshot(db, key):
        floor_rows = db.execute(f'SELECT clock_floor_us FROM main.{PARTITIONS} WHERE {WHERE} LIMIT 2', key).fetchall()
        if len(floor_rows) > 1:
            raise unavailable()
        floor = floor_rows[0][0] if floor_rows else None
        if floor is not None and (type(floor) is not int or not 0 <= floor <= MAX_TIME):
            raise unavailable()
        # No SELECT * / TEXT substr: hostile NUL/UTF8/oversized fields stay bounded.
        raw = db.execute(f'''SELECT substr(CAST(token AS BLOB),1,49),
            substr(CAST(context AS BLOB),1,33),substr(CAST(authority AS BLOB),1,33),
            substr(CAST(visibility AS BLOB),1,33),substr(CAST(connection_id AS BLOB),1,129),
            substr(CAST(object_id AS BLOB),1,129),issued_us,expires_us
            FROM main.{RECEIPTS} WHERE {WHERE} ORDER BY token LIMIT 33''', key).fetchall()
        if len(raw) > QUOTA or (raw and floor is None):
            raise unavailable()
        rows = []
        for token, context, authority, visibility, connection_id, object_id, issued, expires in raw:
            try:
                token = token_value(_text(token, 48, ascii_only=True))
            except PolicyError:
                raise unavailable() from None
            connection_id = identifier(_text(connection_id, 128, ascii_only=True))
            object_id = identifier(_text(object_id, 128, ascii_only=True))
            if (type(issued) is not int or type(expires) is not int or not 0 <= issued <= MAX_TIME-TTL
                    or expires != issued + TTL):
                raise unavailable()
            rows.append(Row(token, digest(context), digest(authority), digest(visibility), connection_id, object_id, issued, expires))
        if len({r.position for r in rows}) != len(rows):
            raise unavailable()
        return floor, tuple(rows)
