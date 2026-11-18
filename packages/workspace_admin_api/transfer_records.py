# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Retained handover history format only: no writer, replay or HTTP authority.

A historical receipt is not a grant. Decode/check/backup must retain valid stale
identities and membership provenance, not compare them with today's permissions.
"""
from contextlib import closing
from hashlib import sha256
import json
import re
import sqlite3
from urllib.parse import quote
from uuid import UUID

from packages.workspaces import StorageError
from packages.workspaces.authority import validate_authority
from packages.workspaces.model import MAX_REVISION, identifier, revision

TABLE = 'workspace_admin_transfers_v1'
INTENT_LIMIT = 4096
VECTOR_LIMIT = 32768
QUOTA = 256
BATCH = 64
FIELDS = ('actor', 'workspace_id', 'request_key', 'replacement_id', 'intent',
          'request_digest', 'provenance', 'actor_revision', 'replacement_revision',
          'audit_first', 'audit_last')
_CREATE = """CREATE TABLE workspace_admin_transfers_v1 (
    actor TEXT NOT NULL REFERENCES principals(id),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    request_key TEXT NOT NULL,
    replacement_id TEXT NOT NULL REFERENCES principals(id),
    intent TEXT NOT NULL, request_digest TEXT NOT NULL, provenance TEXT NOT NULL,
    actor_revision INTEGER NOT NULL CHECK(actor_revision>=1 AND actor_revision<9007199254740991),
    replacement_revision INTEGER NOT NULL CHECK(replacement_revision>=1 AND replacement_revision<9007199254740991),
    audit_first INTEGER NOT NULL UNIQUE CHECK(audit_first>=1 AND audit_first<9007199254740991),
    audit_last INTEGER NOT NULL CHECK(audit_last=audit_first+1),
    PRIMARY KEY(actor,workspace_id,request_key)) STRICT"""
SCHEMA = (_CREATE,)
_ROUTE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}\Z')
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_PROOF = re.compile(r'ats1-[0-9a-f]{64}\Z')
# Prefixes operate on BLOB bytes, never SQLite TEXT length/substr (NUL truncates).
_PROJECTION = ','.join((
    'substr(CAST(actor AS BLOB),1,801)',
    'substr(CAST(workspace_id AS BLOB),1,201)',
    'substr(CAST(request_key AS BLOB),1,37)',
    'substr(CAST(replacement_id AS BLOB),1,201)',
    f'substr(CAST(intent AS BLOB),1,{INTENT_LIMIT + 1})',
    'substr(CAST(request_digest AS BLOB),1,65)',
    f'substr(CAST(provenance AS BLOB),1,{VECTOR_LIMIT + 1})',
    'actor_revision', 'replacement_revision', 'audit_first', 'audit_last'))
_ORDER = ' ORDER BY actor,workspace_id,request_key'
FIRST_PAGE_SQL = f'SELECT {_PROJECTION} FROM main.{TABLE}' + _ORDER + f' LIMIT {BATCH + 1}'
NEXT_PAGE_SQL = (f'SELECT {_PROJECTION} FROM main.{TABLE} WHERE '
                 '(actor,workspace_id,request_key)>(?,?,?)' + _ORDER + f' LIMIT {BATCH + 1}')
RECEIPT_SQL = (f'SELECT {_PROJECTION} FROM main.{TABLE} '
               'WHERE actor=? AND workspace_id=? AND request_key=?')
_AUDIT_SQL = """SELECT sequence,substr(CAST(actor_id AS BLOB),1,801),
    substr(CAST(workspace_id AS BLOB),1,201),substr(CAST(action AS BLOB),1,33),
    substr(CAST(object_id AS BLOB),1,801),revision FROM main.audit
    WHERE sequence IN (?,?) ORDER BY sequence"""


def _invalid():
    return StorageError('invalid_transfer_record')


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'))


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _no_number(_):
    raise ValueError()


def _text(raw, limit, *, ascii_only=False):
    if type(raw) is not bytes or not 0 < len(raw) <= limit:
        raise ValueError()
    return raw.decode('ascii' if ascii_only else 'utf-8', errors='strict')


def _json(raw, limit):
    text = _text(raw, limit, ascii_only=True)
    value = json.loads(text, object_pairs_hook=_object, parse_float=_no_number,
                       parse_constant=_no_number)
    if _canonical(value) != text:
        raise ValueError()
    return value


def _keys(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError()


def _version(value):
    if type(value) is not int or value != 1:
        raise ValueError()


def _policy_id(value):
    if type(value) is not str:
        raise ValueError()
    identifier(value)  # Preserve policy's NUL/DEL and Unicode semantics.
    value.encode('utf-8', errors='strict')


def _route_id(value):
    if type(value) is not str or _ROUTE_ID.fullmatch(value) is None:
        raise ValueError()


def _key(value):
    if type(value) is not str or len(value) != 36:
        raise ValueError()
    parsed = UUID(value)
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError()


def _principal(value, *, actor, realm):
    _keys(value, ('id', 'issuer', 'subject', 'generation', 'authority_epoch', 'membership'))
    for key in ('id', 'issuer', 'subject'):
        _policy_id(value[key])
    revision(value['generation']); revision(value['authority_epoch'])
    member = value['membership']
    _keys(member, ('role', 'state', 'revision', 'authority'))
    if member['state'] != 'active' or member['role'] not in (('admin',) if actor else ('viewer', 'developer', 'admin')):
        raise ValueError()
    revision(member['revision'])
    grant = member['authority']
    validate_authority(grant, (value['id'],))
    if (grant['realm'] != realm or grant['principals'][value['id']] != {
            key: value[key] for key in ('issuer', 'subject', 'generation')}):
        raise ValueError()


def decode_record(row):
    """Decode a bounded BLOB projection without consulting current authority."""
    try:
        if type(row) is not tuple or len(row) != len(FIELDS):
            raise ValueError()
        value = dict(zip(FIELDS, row))
        for key, limit, ascii_only in (('actor', 800, False), ('workspace_id', 200, True),
                ('request_key', 36, True), ('replacement_id', 200, True), ('request_digest', 64, True)):
            value[key] = _text(value[key], limit, ascii_only=ascii_only)
        _policy_id(value['actor']); _route_id(value['workspace_id']); _route_id(value['replacement_id'])
        _key(value['request_key'])
        if value['actor'] == value['replacement_id'] or _HEX.fullmatch(value['request_digest']) is None:
            raise ValueError()
        for key in ('actor_revision', 'replacement_revision', 'audit_first', 'audit_last'):
            revision(value[key])
        if (max(value['actor_revision'], value['replacement_revision'], value['audit_first']) >= MAX_REVISION
                or value['audit_last'] != value['audit_first'] + 1):
            raise ValueError()
        intent = _json(value['intent'], INTENT_LIMIT)
        vector = _json(value['provenance'], VECTOR_LIMIT)
        _keys(intent, ('version', 'action', 'actor', 'workspace', 'replacement',
                       'actor_revision', 'replacement_revision', 'precondition'))
        _version(intent['version'])
        if (intent['action'] != 'admin.transfer'
                or type(intent['precondition']) is not str or _PROOF.fullmatch(intent['precondition']) is None):
            raise ValueError()
        revision(intent['actor_revision']); revision(intent['replacement_revision'])
        if any(intent[a] != value[b] for a, b in (
                ('actor', 'actor'), ('workspace', 'workspace_id'), ('replacement', 'replacement_id'),
                ('actor_revision', 'actor_revision'), ('replacement_revision', 'replacement_revision'))):
            raise ValueError()
        _keys(vector, ('version', 'action', 'route', 'realm', 'workspace', 'actor', 'replacement'))
        _version(vector['version'])
        if (vector['action'] != 'admin.transfer' or type(vector['realm']) is not str
                or re.fullmatch(r'[0-9a-f]{32}', vector['realm']) is None
                or vector['route'] != f"/api/workspaces/{quote(value['workspace_id'], safe='')}/admin-transfers"):
            raise ValueError()
        _keys(vector['workspace'], ('id', 'state', 'revision'))
        revision(vector['workspace']['revision'])
        if vector['workspace']['id'] != value['workspace_id'] or vector['workspace']['state'] != 'active':
            raise ValueError()
        _principal(vector['actor'], actor=True, realm=vector['realm'])
        _principal(vector['replacement'], actor=False, realm=vector['realm'])
        for name, id_column, rev_column in (('actor', 'actor', 'actor_revision'),
                ('replacement', 'replacement_id', 'replacement_revision')):
            if (vector[name]['id'] != value[id_column]
                    or vector[name]['membership']['revision'] != value[rev_column]):
                raise ValueError()
        if (intent['precondition'] != 'ats1-' + sha256(_canonical(vector).encode('ascii')).hexdigest()
                or value['request_digest'] != sha256(_canonical(intent).encode('ascii')).hexdigest()):
            raise ValueError()
        value['intent'], value['provenance'] = intent, vector
        return value
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, AttributeError):
        raise _invalid() from None


def check_schema(db):
    """Exact receipt objects only. No DDL, repair or identity adoption."""
    try:
        rows = db.execute("SELECT type,substr(CAST(name AS BLOB),1,101),substr(CAST(sql AS BLOB),1,8193) "
                          "FROM main.sqlite_schema WHERE name GLOB 'workspace_admin_transfers_*' LIMIT 2").fetchall()
        if len(rows) != 1:
            raise ValueError()
        kind, name, sql = rows[0]
        if (kind != 'table' or _text(name, 100, ascii_only=True) != TABLE
                or ' '.join(_text(sql, 8192, ascii_only=True).split()) != ' '.join(_CREATE.split())):
            raise ValueError()
        if db.execute("SELECT 1 FROM main.sqlite_schema WHERE tbl_name=? AND "
                "(type='trigger' OR (type='index' AND sql IS NOT NULL)) LIMIT 1", (TABLE,)).fetchone():
            raise ValueError()
        if db.execute('SELECT 1 FROM temp.sqlite_schema LIMIT 1').fetchone():
            raise ValueError()
    except (ValueError, TypeError, UnicodeError, sqlite3.Error):
        raise StorageError('invalid_transfer_schema') from None


def _audit(db, record):
    with closing(db.execute(_AUDIT_SQL, (record['audit_first'], record['audit_last']))) as cursor:
        rows = cursor.fetchall()  # Two indexed primary-key reads, not history scan.
    try:
        decoded = []
        for seq, actor, workspace, action, obj, rev in rows:
            revision(seq); revision(rev)
            decoded.append((seq, _text(actor, 800), _text(workspace, 200, ascii_only=True),
                            _text(action, 32, ascii_only=True), _text(obj, 800), rev))
        expected = [
            (record['audit_first'], record['actor'], record['workspace_id'], 'membership.promote',
             record['replacement_id'], record['replacement_revision'] + 1),
            (record['audit_last'], record['actor'], record['workspace_id'], 'membership.demote',
             record['actor'], record['actor_revision'] + 1)]
        if decoded != expected:
            raise ValueError()
    except (ValueError, TypeError, UnicodeError):
        raise _invalid() from None


def validate_all(db, policy):
    """Check ALL retained history with bounded pages; no current-auth comparisons.

    O(history) total work, not a constant-work or streaming whole-policy service.
    Stale realm/generation/active/role/revision/ownership is not storage damage.
    """
    check_schema(db)
    total, count, partition, after = 0, 0, None, None
    try:
        while True:
            sql, args = (FIRST_PAGE_SQL, ()) if after is None else (NEXT_PAGE_SQL, after)
            with closing(db.execute(sql, args)) as cursor:
                rows = cursor.fetchall()  # At most BATCH+1 bounded projections.
            for row in rows[:BATCH]:
                item = decode_record(row)
                key = (item['actor'], item['workspace_id'], item['request_key'])
                if after is not None and key <= after:
                    raise _invalid()
                if key[:2] != partition:
                    partition, count = key[:2], 0
                count += 1
                if count > QUOTA:
                    raise _invalid()
                # Foreign existence is structure. No active/admin/authority check.
                if (item['actor'] not in policy._principals or item['replacement_id'] not in policy._principals
                        or item['workspace_id'] not in policy._workspaces):
                    raise _invalid()
                _audit(db, item)
                total += 1
                after = key
            if len(rows) <= BATCH:
                return total
    except sqlite3.Error:
        raise _invalid() from None
