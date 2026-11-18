# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Bounded permanent handover receipts, explicitly configured, never a grant.

Online admission is a fixed read-only structural check. Full retained-history
validation and expected-schema preparation happen only at trusted factory open.
"""
from contextlib import closing
from hashlib import sha256
from pathlib import Path
import re
import sqlite3

from packages.workspaces import PolicyError, SQLiteWorkspaceService, StorageError
from packages.workspaces.authority import authority_current
from packages.workspaces.sqlite import _SCHEMA, _APPLICATION_ID, _SCHEMA_VERSION, _encode_grant
from packages.login_state.core import _SCHEMA as LOGIN_SCHEMA, _APP, _VERSION
from . import transfer_records as records
from .transfer_selection import selection

# Fixed ceiling for explicit factories' auxiliary schema, not a captured
# allowlist. Runtime profiles use their exact smaller expected object count.
_EXPLICIT_SCHEMA_LIMIT = 256
_SCHEMA_NAME_LIMIT = 200
_SCHEMA_SQL_LIMIT = 16384
_SQL_SPACE = re.compile(r'[ \t\r\n\f]+')
_INERT_NAME = re.compile(r'auxiliary_[a-z0-9_]+')
# Reviewed optional module DDL, joined in the order below, with NUL separators.
# A change to another package cannot silently widen this writer's admission.
_AUXILIARY_DDL_SHA256 = '78a46cb76cd467ae1fe537cdca7d52a4b3b6ee93f868daed8a5bf808a4fd4efd'
_SCHEMA_COLUMNS = ('type', 'name', 'tbl_name', 'sql')
_SCHEMA_PROJECTION = ','.join(f'substr(CAST({key} AS BLOB),1,{limit + 1})'
    for key, limit in zip(_SCHEMA_COLUMNS, (16, _SCHEMA_NAME_LIMIT, _SCHEMA_NAME_LIMIT, _SCHEMA_SQL_LIMIT)))
QUOTA_SQL = (f'SELECT substr(CAST(request_key AS BLOB),1,37) FROM main.{records.TABLE} '
             'WHERE actor=? AND workspace_id=? ORDER BY request_key LIMIT 257')
_PRINCIPAL_SQL = ('SELECT substr(CAST(issuer AS BLOB),1,801),substr(CAST(subject AS BLOB),1,801),'
                  'active,generation,authority_epoch FROM main.principals WHERE id=?')
_MEMBER_SQL = ('SELECT substr(CAST(role AS BLOB),1,17),substr(CAST(state AS BLOB),1,17),revision '
               'FROM main.memberships WHERE workspace_id=? AND principal_id=?')
_OWNER_SQL = ('SELECT substr(CAST(realm AS BLOB),1,33),substr(CAST(issuer AS BLOB),1,801),'
              'substr(CAST(subject AS BLOB),1,801),generation FROM main.membership_authority '
              'WHERE workspace_id=? AND principal_id=?')
_WORKSPACE_SQL = ('SELECT substr(CAST(name AS BLOB),1,801),substr(CAST(kind AS BLOB),1,17),'
                  'substr(CAST(state AS BLOB),1,17),revision FROM main.workspaces WHERE id=?')
_METADATA_SQL = 'SELECT epoch,substr(CAST(realm AS BLOB),1,33) FROM main.metadata WHERE id=1'


def _unavailable():
    return PolicyError(503, 'admin_transfer_unavailable')


def _conflict():
    return PolicyError(409, 'idempotency_conflict')


def _blob(value):
    return value.encode('utf-8') if type(value) is str else value


def _ddl(sql):
    # All reviewed declarations use plain ASCII identifiers, no comments,
    # and only single-quoted literals. Fold SQLite whitespace outside those
    # literals only; never conflate quoted data or Unicode identifier bytes.
    if not any(run in sql for run in ('  ', '\t', '\n', '\r', '\f')):
        return sql
    return "'".join(part if ix % 2 else _SQL_SPACE.sub(' ', part)
                    for ix, part in enumerate(sql.split("'")))


def _schema_rows(db, schema, limit, *, where='', args=()):
    # No ORDER BY: LIMIT bounds the object rows before Python compares them.
    # A complete registry compares count+1 and every field's byte sentinel.
    rows = db.execute(f'SELECT {_SCHEMA_PROJECTION} FROM {schema}.sqlite_schema '
                      f'{where} LIMIT {limit + 1}', args).fetchall()
    if len(rows) > limit:
        raise _unavailable()
    result = []
    for row in rows:
        parsed = []
        for index, (raw, maximum) in enumerate(zip(row, (16, _SCHEMA_NAME_LIMIT, _SCHEMA_NAME_LIMIT, _SCHEMA_SQL_LIMIT))):
            if index == 3 and raw is None:
                parsed.append(None)
                continue
            if type(raw) is not bytes or not 0 < len(raw) <= maximum:
                raise _unavailable()
            value = raw.decode('utf-8', errors='strict')
            parsed.append(_ddl(value) if index == 3 else value)
        result.append(tuple(parsed))
    objects = frozenset(result)
    if len(objects) != len(result):
        raise _unavailable()
    return objects


def _expected(statements):
    # Factory only. Never invoked from _admit_schema or any request callback.
    with closing(sqlite3.connect(':memory:')) as db:
        for statement in statements:
            db.execute(statement)
        # Fixed reviewed DDL can create at most two autoindices per statement.
        return _schema_rows(db, 'main', len(statements) * 3)


def _explicit_modules():
    # Factory only. Use the modules' own declarations, without importing the
    # runtime registry (or using the caller's database to choose a definition).
    from .requests import _CREATE as admin
    from packages.workspace_invitations.store import _CREATE as invites
    from packages.community_api.records import _SCHEMA as community
    from packages.artifact_sync.records import _CREATE as artifacts, _EVENT_CREATE as events
    from packages.assistant_history.store import _TABLES as history
    from packages.responsibility_api.records import _SCHEMA as responsibility
    from packages.gui_smoke.store import _SCHEMA as gui
    from packages.gui_smoke.grants import _SCHEMA as grants
    from packages.research_assets.store import _SCHEMA as assets
    from packages.assistant_protocol.preflight import _SCHEMA as preflight
    from packages.artifact_download.records import _CREATE as downloads, _LEASE_CREATE as leases
    from packages.artifact_refresh.service import _CREATE as artifact_refresh
    from packages.community_refresh.service import _CREATE as community_refresh, _INDEX as refresh_index
    from packages.directory_cursors.store import SCHEMA as cursors
    from packages.workspace_connections.records import _SCHEMA as connections

    ddl = (admin, invites, community, artifacts, events, *history.values(),
           responsibility, gui, grants, *assets, preflight, downloads, leases,
           artifact_refresh, community_refresh, refresh_index, *cursors, connections)
    if sha256('\0'.join(ddl).encode('utf-8')).hexdigest() != _AUXILIARY_DDL_SHA256:
        raise _unavailable()
    # Group by owner so any present module table must have exactly its reviewed
    # definition and indexes (including implicit ones), not any other index.
    groups = {}
    for row in _expected(ddl):
        groups.setdefault(row[2], set()).add(row)
    return {owner: frozenset(rows) for owner, rows in groups.items()}


def _admit_explicit(actual, required, optional):
    if not required <= actual:
        raise _unavailable()
    groups = {}
    for row in actual - required:
        kind, name, owner, sql = row
        if owner in optional:
            groups.setdefault(owner, set()).add(row)
        elif (kind != 'table' or name != owner or not _INERT_NAME.fullmatch(name)
              or sql != f'CREATE TABLE {name} (value TEXT)'):
            # This single inert ordinary-table form has no FK, expression,
            # index, trigger, virtual module or other write-side behavior.
            raise _unavailable()
    if any(rows != optional[owner] for owner, rows in groups.items()):
        raise _unavailable()


def _no_runtime(policy):
    parent = policy._path.resolve().parent
    if any((parent / name).exists() or (parent / name).is_symlink()
           for name in ('installation.json', '.runtime.lock')):
        raise _unavailable()


def initialize_transfers(policy, *, layout='explicit-factory-v1'):
    """Trusted explicit non-runtime initialization; no HTTP/open-time DDL."""
    if type(policy) is not SQLiteWorkspaceService or layout != 'explicit-factory-v1' or type(layout) is not str:
        raise ValueError('explicit transfer policy/layout required')
    try:
        _no_runtime(policy)
        handle = object.__new__(TransferReceiptStore)
        handle._prepare(policy, None)
        with closing(policy._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                _no_runtime(policy)
                policy._check_schema(db)
                # SQLite identifiers ignore ASCII case; GLOB by itself does not.
                family = db.execute("SELECT 1 FROM main.sqlite_schema "
                                    "WHERE lower(name) GLOB 'workspace_admin_transfers_*' LIMIT 1").fetchone()
                if family is None:
                    db.execute(records._CREATE)
                loaded = policy._load(db)
                handle._realm = loaded._realm
                handle._admit_bearer(db)
                records.validate_all(db, loaded)
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return handle
    except (sqlite3.Error, OSError, StorageError):
        raise _unavailable() from None


def _from_verified_runtime(policy, inspection):
    # Lazy factory-only import: avoid host -> admin -> runtime cycles and never
    # import runtime.schema (which prepares registries) in an online operation.
    from packages.service_runtime.runtime import Inspection
    from packages.service_runtime.schema import layout_profile
    if type(inspection) is not Inspection or inspection.profile is not layout_profile(2):
        raise ValueError('verified layout two inspection required')
    return TransferReceiptStore(policy, _profile=inspection.profile)


class TransferReceiptStore:
    """Exact trusted file/realm binding; mutable authority is checked per request."""
    def __init__(self, policy, *, _profile=None):
        if type(policy) is not SQLiteWorkspaceService:
            raise ValueError('initialized transfer policy required')
        if _profile is not None:
            from packages.service_runtime.schema import layout_profile
            if _profile is not layout_profile(2):
                raise ValueError('verified layout two required')
        try:
            if _profile is None:
                _no_runtime(policy)
            self._prepare(policy, _profile)
            with closing(policy._connect()) as db:
                db.execute('BEGIN')
                try:
                    loaded = policy._load(db)
                    self._realm = loaded._realm
                    self._admit_bearer(db)
                    records.validate_all(db, loaded)
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
        except (sqlite3.Error, OSError, StorageError):
            raise _unavailable() from None

    def _prepare(self, policy, profile):
        self.policy = policy
        self._path = policy._path.resolve(strict=True)
        stat = self._path.stat()
        self._file = stat.st_dev, stat.st_ino
        self._login = _expected(LOGIN_SCHEMA)
        self._runtime = _expected(profile.policy_schema) if profile is not None else None
        if profile is None:
            self._required = _expected((*_SCHEMA, *records.SCHEMA))
            self._optional = _explicit_modules()

    def _admit_bearer(self, db):
        return self._admit_schema(db, expected_login=False)

    def _admit_cookie(self, db):
        return self._admit_schema(db, expected_login=True)

    def _admit_schema(self, db, *, expected_login=None):
        """Trusted SELECT/readonly-PRAGMA only, bounded, before clock/error DML."""
        try:
            current_path = self.policy._path.resolve(strict=True)
            stat = current_path.stat()
            if current_path != self._path or (stat.st_dev, stat.st_ino) != self._file:
                raise _unavailable()
            databases = db.execute('PRAGMA database_list').fetchmany(4)
            names = {row[1] for row in databases}
            if (len(databases) > 3 or not names <= {'main', 'login', 'temp'}
                    or (expected_login is not None and ('login' in names) is not expected_login)):
                raise _unavailable()
            main = next((row[2] for row in databases if row[1] == 'main'), None)
            if main is None or Path(main).resolve(strict=True) != self._path:
                raise _unavailable()
            if db.execute('SELECT 1 FROM temp.sqlite_schema LIMIT 1').fetchone():
                raise _unavailable()
            if (db.execute('PRAGMA main.application_id').fetchone() != (_APPLICATION_ID,)
                    or db.execute('PRAGMA main.user_version').fetchone() != (_SCHEMA_VERSION,)):
                raise _unavailable()
            if self._runtime is not None:
                if _schema_rows(db, 'main', len(self._runtime)) != self._runtime:
                    raise _unavailable()
            else:
                # One count+1 catalog scan; no per-object SQL or DDL. Reject
                # unreviewed cross-table effects, not just unreviewed triggers.
                _admit_explicit(_schema_rows(db, 'main', _EXPLICIT_SCHEMA_LIMIT),
                                self._required, self._optional)
            realm = db.execute('SELECT substr(CAST(realm AS BLOB),1,33) FROM main.metadata WHERE id=1').fetchone()
            if realm != (self._realm.encode('ascii'),):
                raise _unavailable()
            if 'login' in names:
                if (db.execute('PRAGMA login.application_id').fetchone() != (_APP,)
                        or db.execute('PRAGMA login.user_version').fetchone() != (_VERSION,)
                        or _schema_rows(db, 'login', len(self._login)) != self._login):
                    raise _unavailable()
        except Exception:
            raise _unavailable() from None

    def _bound(self, policy, db, admission=None):
        (admission or self._admit_schema)(db)
        if policy._realm != self._realm:
            raise _unavailable()

    @staticmethod
    def _read(db, actor, workspace, key):
        row = db.execute(records.RECEIPT_SQL, (actor, workspace, key)).fetchone()
        if row is None:
            return None
        result = records.decode_record(row)
        records._audit(db, result)
        return result

    @staticmethod
    def _snapshot(db, actor, workspace, replacement):
        return (db.execute(_METADATA_SQL).fetchone(),
                db.execute(_WORKSPACE_SQL, (workspace,)).fetchone(),
                tuple((db.execute(_PRINCIPAL_SQL, (ident,)).fetchone(),
                       db.execute(_MEMBER_SQL, (workspace, ident)).fetchone(),
                       db.execute(_OWNER_SQL, (workspace, ident)).fetchone())
                      for ident in (actor, replacement)))

    @staticmethod
    def _expected_state(policy, actor, workspace, replacement):
        scope = policy._workspaces[workspace]
        selected = []
        for ident in (actor, replacement):
            person = policy._principals[ident]
            member = policy._memberships[(workspace, ident)]
            selected.append(((person.issuer.encode('utf-8'), person.subject.encode('utf-8'),
                              int(person.active), person.generation, person.authority_epoch),
                             (member.role.encode('ascii'), member.state.encode('ascii'), member.revision),
                             tuple(_blob(value) for value in _encode_grant(ident, policy._membership_authority[(workspace, ident)]))))
        return ((policy._epoch, policy._realm.encode('ascii')),
                (scope.name.encode('utf-8'), scope.kind.encode('ascii'), scope.state.encode('ascii'), scope.revision),
                tuple(selected))

    @staticmethod
    def _current_pins(policy, record):
        vector = record['provenance']
        if policy._realm != vector['realm']:
            return False
        for name in ('actor', 'replacement'):
            retained = vector[name]
            ident = retained['id']
            person = policy._principals.get(ident)
            grant = policy._membership_authority.get((record['workspace_id'], ident))
            if (person is None or not person.active
                    or any(getattr(person, key) != retained[key] for key in ('issuer', 'subject', 'generation', 'authority_epoch'))
                    or grant != retained['membership']['authority']
                    or not authority_current(policy, ident, grant)):
                return False
        return True

    @staticmethod
    def _result(record):
        return {'workspace_id': record['workspace_id'], 'actor_id': record['actor'],
                'replacement_id': record['replacement_id'], 'actor_role': 'developer',
                'replacement_role': 'admin', 'actor_revision': record['actor_revision'] + 1,
                'replacement_revision': record['replacement_revision'] + 1, 'state': 'committed'}

    @staticmethod
    def _insert(db, row):
        db.execute(f'INSERT INTO main.{records.TABLE} VALUES ({",".join("?" for _ in records.FIELDS)})', row)

    def transfer(self, policy, actor, db, *, workspace, replacement, key,
                 actor_revision, replacement_revision, precondition, admission=None):
        """Return (historical result, request-local precommit readback closure)."""
        policy._admin(actor, workspace)
        admit = admission or self._admit_schema
        self._bound(policy, db, admit)
        try:
            prior = self._read(db, actor, workspace, key)
            intent = {'version': 1, 'action': 'admin.transfer', 'actor': actor,
                      'workspace': workspace, 'replacement': replacement,
                      'actor_revision': actor_revision, 'replacement_revision': replacement_revision,
                      'precondition': precondition}
            if prior is not None:
                if prior['intent'] != intent or not self._current_pins(policy, prior):
                    raise _conflict()
                record = prior
                expected = self._snapshot(db, actor, workspace, replacement)
                tail = len(policy._audit)
            else:
                if precondition is None:
                    raise PolicyError(428, 'admin_transfer_selection_required')
                if type(precondition) is not str or records._PROOF.fullmatch(precondition) is None:
                    raise PolicyError(400, 'invalid_admin_transfer_selection')
                vector, reviewed = selection(policy, actor, workspace, replacement)
                if (actor_revision != reviewed['actor_revision']
                        or replacement_revision != reviewed['replacement_revision']):
                    raise PolicyError(412, 'stale_revision')
                if precondition != reviewed['precondition']:
                    raise PolicyError(412, 'admin_transfer_selection_changed')
                keys = db.execute(QUOTA_SQL, (actor, workspace)).fetchall()
                for (raw,) in keys:
                    records._key(records._text(raw, 36, ascii_only=True))
                if len(keys) >= records.QUOTA:
                    raise _unavailable()
                first = len(policy._audit) + 1
                policy.transfer_admin(actor, workspace, replacement,
                    expected_revision=actor_revision, replacement_revision=replacement_revision)
                intent_text, vector_text = records._canonical(intent), records._canonical(vector)
                digest = sha256(intent_text.encode('ascii')).hexdigest()
                row = (actor, workspace, key, replacement, intent_text, digest, vector_text,
                       actor_revision, replacement_revision, first, first + 1)
                # Decode the exact bounded format before any INSERT; this cannot
                # shrink legal identity fields or invent persisted provenance.
                record = records.decode_record(tuple(_blob(value) for value in row))
                self._insert(db, row)
                expected = self._expected_state(policy, actor, workspace, replacement)
                tail = len(policy._audit)

            def guard():
                try:
                    admit(db)
                    if (self._snapshot(db, actor, workspace, replacement) != expected
                            or self._read(db, actor, workspace, key) != record
                            or db.execute('SELECT sequence FROM main.audit WHERE sequence>? ORDER BY sequence LIMIT 1', (tail,)).fetchone()
                            or db.execute('SELECT sequence FROM main.audit ORDER BY sequence DESC LIMIT 1').fetchone() != (tail,)):
                        raise _unavailable()
                except Exception:
                    raise _unavailable() from None

            return self._result(record), guard
        except (sqlite3.Error, OSError, StorageError, ValueError, TypeError, UnicodeError):
            raise _unavailable() from None
