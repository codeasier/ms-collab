# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Storage-neutral, explicitly scoped quarter catalogs. No I/O or authorization."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')
_QUARTER = re.compile(r'[1-9][0-9]{3}Q[1-4]\Z')


def text(value: str, maximum: int, *, empty: bool = False) -> str:
    if (not isinstance(value, str) or len(value) > maximum or
            (not value and not empty) or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError('invalid text')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError('invalid text') from None
    return value


def instant(value: str) -> datetime:
    text(value, 40)
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError('timezone required')
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError('timestamp requires a valid explicit timezone') from None


def quarter(value: str) -> str:
    if not isinstance(value, str) or not _QUARTER.fullmatch(value):
        raise ValueError('quarter must be YYYYQ1 through YYYYQ4')
    return value


def quarter_for_time(value: str, *, offset_minutes: int = 0) -> str:
    if type(offset_minutes) is not int or not -840 <= offset_minutes <= 840:
        raise ValueError('invalid index timezone offset')
    try:
        dt = instant(value).astimezone(timezone(timedelta(minutes=offset_minutes)))
    except OverflowError:
        raise ValueError('timestamp outside index range') from None
    return quarter(f'{dt.year:04d}Q{(dt.month - 1) // 3 + 1}')


def key(value: str, *, directory: bool | None = None, root: bool = False) -> str:
    text(value, 2048, empty=root)
    if not value:
        return value
    # A key is a storage-relative literal, not a URL or filesystem path.
    if value.startswith('/') or '\\' in value or '://' in value:
        raise ValueError('expected storage-relative key')
    if any(part in {'.', '..'} for part in value.split('/')):
        raise ValueError('dot path segments are unsupported')
    if directory is not None and value.endswith('/') != directory:
        raise ValueError('directory marker mismatch')
    return value


@dataclass(frozen=True, slots=True)
class Scope:
    workspace_id: str
    connection_id: str
    bucket_id: str

    def __post_init__(self):
        for value in (self.workspace_id, self.connection_id, self.bucket_id):
            if not isinstance(value, str) or not _ID.fullmatch(value):
                raise ValueError('invalid artifact scope')

    def as_dict(self):
        return dict(workspace_id=self.workspace_id, connection_id=self.connection_id,
                    bucket_id=self.bucket_id)


@dataclass(frozen=True, slots=True)
class Artifact:
    key: str
    modified_at: str
    size: int = 0
    is_dir: bool = False

    def __post_init__(self):
        if type(self.is_dir) is not bool:
            raise ValueError('is_dir must be boolean')
        key(self.key, directory=self.is_dir)
        instant(self.modified_at)
        if type(self.size) is not int or self.size < 0 or (self.is_dir and self.size != 0):
            raise ValueError('invalid artifact size')


@dataclass(frozen=True, slots=True)
class Entry:
    key: str
    modified_at: str | None
    size: int
    is_dir: bool
    virtual: bool = False

    @property
    def name(self):
        return self.key.removesuffix('/').rsplit('/', 1)[-1]

    @property
    def parent(self):
        stem = self.key.removesuffix('/')
        return stem.rsplit('/', 1)[0] + '/' if '/' in stem else ''

    def as_dict(self):
        return dict(key=self.key, name=self.name, parent=self.parent,
                    modified_at=self.modified_at, size=self.size, is_dir=self.is_dir,
                    virtual=self.virtual)


@dataclass(frozen=True, slots=True)
class Snapshot:
    scope: Scope
    observed_at: str
    artifacts: tuple[Artifact, ...]
    offset_minutes: int = 0

    def __post_init__(self):
        if not isinstance(self.scope, Scope):
            raise ValueError('scope required')
        observed = instant(self.observed_at)
        quarter_for_time(self.observed_at, offset_minutes=self.offset_minutes)
        if not isinstance(self.artifacts, (list, tuple)) or len(self.artifacts) > 10000:
            raise ValueError('expected at most 10000 artifacts')
        values = tuple(self.artifacts)
        if any(not isinstance(a, Artifact) for a in values):
            raise ValueError('invalid artifact')
        if len({a.key for a in values}) != len(values):
            raise ValueError('duplicate artifact key')
        if any(instant(a.modified_at) > observed for a in values):
            raise ValueError('artifact is newer than observation')
        # Validate every quarter before publishing any projection.
        for a in values:
            quarter_for_time(a.modified_at, offset_minutes=self.offset_minutes)
        object.__setattr__(self, 'artifacts', values)

    def quarters(self):
        return tuple(sorted({quarter_for_time(a.modified_at, offset_minutes=self.offset_minutes)
                             for a in self.artifacts}, reverse=True))

    def entries(self, selected_quarter: str):
        quarter(selected_quarter)
        rows = {a.key: Entry(a.key, a.modified_at, a.size, a.is_dir)
                for a in self.artifacts
                if quarter_for_time(a.modified_at, offset_minutes=self.offset_minutes) == selected_quarter}
        for entry in tuple(rows.values()):
            parts = entry.key.removesuffix('/').split('/')
            for length in range(1, len(parts)):
                parent = '/'.join(parts[:length]) + '/'
                rows.setdefault(parent, Entry(parent, None, 0, True, True))
        return tuple(rows.values())


class ScopeUnavailable(LookupError):
    """Unknown exact scope. No fallback or aggregate across workspaces."""


class ArtifactCatalog:
    """Immutable observed metadata, NOT an authorization or download service.

    Authenticate and authorize scope/capability before every call. A caller can
    request any scope loaded here. Saved views narrow a query, not permissions.
    """
    def __init__(self, snapshots):
        if not isinstance(snapshots, (list, tuple)) or len(snapshots) > 1000:
            raise ValueError('expected bounded snapshots')
        if any(not isinstance(s, Snapshot) for s in snapshots):
            raise ValueError('invalid snapshot')
        if len({s.scope for s in snapshots}) != len(snapshots):
            raise ValueError('duplicate scope')
        self._snapshots = {s.scope: s for s in snapshots}

    def _snapshot(self, scope):
        if not isinstance(scope, Scope):
            raise ValueError('scope required')
        try:
            return self._snapshots[scope]
        except KeyError:
            raise ScopeUnavailable('artifact scope is unavailable') from None

    def quarters(self, scope):
        return self._snapshot(scope).quarters()

    def query(self, scope, selected_quarter, definition, *, page=1, page_size=50):
        from .views import Definition
        snapshot = self._snapshot(scope)
        quarter(selected_quarter)
        if not isinstance(definition, Definition):
            raise ValueError('validated Definition required')
        if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError('invalid pagination')
        rows = [row for row in snapshot.entries(selected_quarter) if definition.matches(row)]
        reverse = definition.order == 'desc'
        def sort_value(row):
            if definition.sort_by == 'modified_at':
                return instant(row.modified_at) if row.modified_at is not None else None
            return getattr(row, definition.sort_by)
        # Directories first, missing modification times last within each kind;
        # a full literal key breaks all ties in the requested direction.
        ordered = []
        for is_dir in (True, False):
            group = [r for r in rows if r.is_dir == is_dir]
            known = [r for r in group if sort_value(r) is not None]
            unknown = [r for r in group if sort_value(r) is None]
            ordered.extend(sorted(known, key=lambda r: (sort_value(r), r.key), reverse=reverse))
            ordered.extend(sorted(unknown, key=lambda r: r.key, reverse=reverse))
        start = (page - 1) * page_size
        return dict(scope=scope.as_dict(), quarter=selected_quarter, observed_at=snapshot.observed_at,
                    quarter_available=selected_quarter in snapshot.quarters(), total=len(rows),
                    page=page, page_size=page_size,
                    items=[r.as_dict() for r in ordered[start:start + page_size]])

    def apply_view(self, scope, selected_quarter, view, **pagination):
        from .views import SavedView
        self._snapshot(scope)
        if not isinstance(view, SavedView) or view.scope != scope:
            raise ValueError('view scope mismatch')
        return self.query(scope, selected_quarter, view.definition, **pagination)
