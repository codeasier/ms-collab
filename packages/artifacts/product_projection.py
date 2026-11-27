# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Pure single-snapshot product projection. The caller must authorize its inputs."""
from dataclasses import dataclass
from datetime import datetime
import json
import re

from .catalog import Artifact, Entry, Snapshot, instant, key, quarter, quarter_for_time, text
from .product_conditions import ASCII_FOLD, compile_conditions, matches_conditions

MATCH = 'relative-key-ascii-substring-v1'
_TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')
_HEX = re.compile(r'[0-9a-f]{64}')
_TIME = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]'
                   r'(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])')
_BASE_FIELDS = frozenset(('format', 'quarter', 'path', 'mode', 'page', 'page_size', 'if_observation'))
_FILE_FIELDS = frozenset(('recursive', 'keyword', 'conditions', 'start_time', 'end_time'))
_MAX_OBJECTS, _MAX_KEY_BYTES, _MAX_SEGMENTS, _MAX_JSON = 500, 1024, 32, 1048576


def token(value):
    if type(value) is not str or not _TOKEN.fullmatch(value):
        raise ValueError('invalid product token')
    return value


def _bounds(value):
    if len(value.encode('utf-8')) > _MAX_KEY_BYTES or value.count('/') + 1 > _MAX_SEGMENTS:
        raise ValueError('key outside product projection limits')


def _time(value):
    if type(value) is not str or not _TIME.fullmatch(value):
        raise ValueError('invalid product time')
    return instant(value)


@dataclass(frozen=True, slots=True)
class _Query:
    quarter: str
    path: str
    mode: str
    page: int
    page_size: int
    recursive: bool
    keyword: str | None
    conditions: tuple | None
    start: datetime | None
    end: datetime | None
    observation: str | None


def validate_request(body):
    """Strict request codec shared by the pure entry and the HTTP precondition."""
    if (type(body) is not dict or not {'format', 'quarter'} <= body.keys()
            or type(body['format']) is not int or body['format'] != 1):
        raise ValueError('invalid product request')
    mode = body.get('mode', 'tree')
    if (type(mode) is not str or mode not in ('tree', 'files')
            or body.keys() - (_BASE_FIELDS if mode == 'tree' else _BASE_FIELDS | _FILE_FIELDS)):
        raise ValueError('invalid product fields')
    selected = quarter(body['quarter'])
    path = key(body.get('path', ''), directory=True, root=True)
    _bounds(path)
    page, size = body.get('page', 1), body.get('page_size', 50)
    if type(page) is not int or not 1 <= page <= 1000000 or type(size) is not int or not 1 <= size <= 100:
        raise ValueError('invalid product pagination')
    observed = body.get('if_observation')
    if (page > 1 or 'if_observation' in body) and (type(observed) is not str or not _HEX.fullmatch(observed)):
        raise ValueError('invalid product observation')
    recursive = body.get('recursive', False)
    if type(recursive) is not bool:
        raise ValueError('invalid recursion flag')
    keyword = text(body['keyword'], 256) if 'keyword' in body else None
    if keyword is not None and not keyword.strip():
        raise ValueError('empty product refinement')
    conditions = None
    if 'conditions' in body:
        if 'keyword' in body:
            raise ValueError('ambiguous product refinement')
        conditions = compile_conditions(body['conditions'])
    start = _time(body['start_time']) if 'start_time' in body else None
    end = _time(body['end_time']) if 'end_time' in body else None
    if start is not None and end is not None and start > end:
        raise ValueError('reversed product time range')
    return _Query(selected, path, mode, page, size, recursive, keyword, conditions, start, end, observed)


def _ascii_bytes(value):
    # The index snapshot codec uses compact ensure_ascii JSON. Charge each row
    # separately so that checking an oversize pure input does not build a giant string.
    return len(json.dumps(value, ensure_ascii=True, separators=(',', ':')))


def _bounded_rows(snapshot):
    if type(snapshot) is not Snapshot or len(snapshot.artifacts) > _MAX_OBJECTS:
        raise ValueError('bounded product snapshot required')
    used = _ascii_bytes({'schema': 1, 'scope': snapshot.scope.as_dict(), 'observed_at': snapshot.observed_at,
                         'offset_minutes': snapshot.offset_minutes, 'artifacts': []})
    for index, row in enumerate(snapshot.artifacts):
        if type(row) is not Artifact or row.size > 9007199254740991:
            raise ValueError('invalid indexed artifact')
        _bounds(row.key)
        used += (1 if index else 0) + _ascii_bytes({'key': row.key, 'modified_at': row.modified_at,
                                                   'size': row.size, 'is_dir': row.is_dir})
        if used > _MAX_JSON:
            raise ValueError('snapshot outside product projection limits')
        yield row


def project(snapshot, product_token, request):
    """Return metadata only. No lookup, persistence, authorization or provider I/O.

    No ordinary Definition/SavedView semantics are reused or mutated. A single
    pass filters complete leaves before deriving *one* directory level.
    """
    query = validate_request(request)
    needle = token(product_token).translate(ASCII_FOLD)
    rows = {}
    available = False
    for item in _bounded_rows(snapshot):
        if quarter_for_time(item.modified_at, offset_minutes=snapshot.offset_minutes) != query.quarter:
            continue
        available = True
        if item.is_dir:
            continue
        folded = item.key.translate(ASCII_FOLD)
        if folded.endswith('.sha256') or needle not in folded or not item.key.startswith(query.path):
            continue
        rest = item.key[len(query.path):]
        if query.mode == 'tree':
            segment, separator, _ = rest.partition('/')
            if separator:
                # Empty internal segments are distinct; the trailing slash also
                # distinguishes a directory from a matching direct file of the same name.
                child = query.path + segment + '/'
                if child not in rows:
                    rows[child] = Entry(child, None, 0, True, True)
                continue
        else:
            if not query.recursive and '/' in rest:
                continue
            if query.keyword is not None and query.keyword not in item.key:
                continue
            if query.conditions is not None and not matches_conditions(folded, query.conditions):
                continue
            if query.start is not None or query.end is not None:
                when = instant(item.modified_at)
                if (query.start is not None and when < query.start or
                        query.end is not None and when > query.end):
                    continue
        rows[item.key] = Entry(item.key, item.modified_at, item.size, False)

    ordered = sorted(rows.values(), key=lambda entry: (not entry.is_dir, entry.name, entry.key))
    start = (query.page - 1) * query.page_size
    return {'scope': snapshot.scope.as_dict(), 'quarter': query.quarter, 'path': query.path,
            'mode': query.mode, 'observed_at': snapshot.observed_at, 'quarter_available': available,
            'page': query.page, 'page_size': query.page_size, 'total': len(ordered),
            'items': [entry.as_dict() for entry in ordered[start:start + query.page_size]]}
