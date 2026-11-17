# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Bounded, current-authority directories over existing exact index keys.

No provider discovery, configuration or new access grants.
Reader discovery never reads candidate data.
A cursor names only the last visible tuple, never a hidden scan position/count.
"""
import re
from urllib.parse import parse_qsl

from packages.artifacts import Scope
from packages.workspaces import PolicyError, StorageError
from .records import _TABLE, authorized, check_schema

MAX_SCOPES = 256
PAGE_SIZE = 25
MAX_DIRECTORY_BYTES = 8 * 1048576
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')


def directory_request(environ, namespace):
    """Return None for an ordinary exact-scope route; reject malformed lists."""
    path = environ.get('PATH_INFO', '')
    if type(path) is not str or len(path) > 1000:
        raise PolicyError(400, 'invalid_request')
    parts = path.split('/')
    if len(parts) != 6 or parts[:3] != ['', 'api', 'workspaces'] or parts[4:] != [namespace, 'scopes']:
        return None
    if environ.get('REQUEST_METHOD') != 'GET' or not _ID.fullmatch(parts[3]):
        raise PolicyError(404, 'not_found')
    query = environ.get('QUERY_STRING', '')
    try:
        if type(query) is not str or len(query) > 600:
            raise ValueError()
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True, max_num_fields=1)
        after = None
        if pairs:
            if pairs[0][0] != 'after':
                raise ValueError()
            after = tuple(pairs[0][1].split('~'))
            if len(after) != 2 or any(not _ID.fullmatch(v) for v in after):
                raise ValueError()
        return parts[3], after
    except (ValueError, TypeError):
        raise PolicyError(400, 'invalid_request') from None


def directory(policy, actor, db, workspace, after, *, status=None):
    # Check identity, membership, workspace/module availability before reading
    # any index keys. Admin status may legally be write-only (refresh grant).
    if status is None:
        policy.authorize(actor, workspace, 'artifacts', 'read')
    else:
        policy._admin(actor, workspace)
        try:
            policy.authorize(actor, workspace, 'artifacts', 'read')
        except PolicyError as error:
            if error.status != 403:
                raise
            policy.authorize(actor, workspace, 'artifacts', 'write')
    check_schema(db)
    keys = db.execute(f'SELECT substr(connection,1,129),substr(bucket,1,129) FROM {_TABLE} WHERE workspace=? ORDER BY connection,bucket LIMIT ?',
                      (workspace, MAX_SCOPES + 1)).fetchall()
    if len(keys) > MAX_SCOPES:
        # No partial response, scan count, hidden cursor or truncation.
        raise PolicyError(503, 'scope_directory_unavailable')
    used = 0
    def budget(raw):
        nonlocal used
        if type(raw) is not str:
            raise StorageError('invalid_artifact_index')
        used += len(raw.encode('utf-8'))
        if used > MAX_DIRECTORY_BYTES:
            raise PolicyError(503, 'scope_directory_unavailable')
    visible = []
    for connection, bucket in keys:
        if any(type(v) is not str or not _ID.fullmatch(v) for v in (connection, bucket)):
            raise StorageError('invalid_artifact_index')
        scope = Scope(workspace, connection, bucket)
        try:
            if status is None:
                record = authorized(policy, actor, db, scope, 'read', budget=budget)
                # authorized validates the entire bound publication (including
                # scope, configuration, fingerprint and provenance), not candidate.
                if record.published_raw is None:
                    continue
                item = {'scope': scope.as_dict(), 'catalog_revision': record.published_revision}
            else:
                current = status(policy, actor, db, scope, budget=budget)
                item = {'scope': scope.as_dict(), 'capabilities': current['capabilities']}
        except PolicyError as error:
            if error.status not in (403, 404):
                raise
            continue
        visible.append(((connection, bucket), item))
    start = 0
    if after is not None:
        # Revocation/removal invalidates this cursor rather than exposing where
        # a now-invisible tuple sorts. Every page is a fresh authorization check.
        positions = [i for i, (key, _) in enumerate(visible) if key == after]
        if not positions:
            raise PolicyError(409, 'scope_directory_changed')
        start = positions[0] + 1
    page = visible[start:start + PAGE_SIZE]
    next_after = '~'.join(page[-1][0]) if start + len(page) < len(visible) else None
    return {'workspace_id': workspace, 'items': [item for _, item in page], 'next_after': next_after}
