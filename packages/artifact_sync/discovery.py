# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Bounded, current-authority directories over existing exact index keys.

No provider discovery, configuration or new access grants.
Reader discovery never reads candidate data.
Only currently visible scopes contribute authority or visibility fences.
"""
import re

from packages.artifacts import Scope
from packages.directory_cursors import Projection
from packages.directory_cursors.directory import fingerprint, gate
from packages.workspaces.authority import principal_pin
from packages.workspaces import PolicyError, StorageError
from .records import _TABLE, _decode_projection, authorized, check_schema, digest

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
    return parts[3], environ.get('QUERY_STRING', '')


def directory(policy, actor, db, workspace, *, status=None):
    gate(policy, actor, workspace, 'artifact.scopes.read' if status is None else 'artifact.scopes.admin')
    check_schema(db)
    keys = db.execute(f'SELECT substr(CAST(connection AS BLOB),1,129),substr(CAST(bucket AS BLOB),1,129) FROM {_TABLE} WHERE workspace=? ORDER BY connection,bucket LIMIT ?',
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
    visible, authority = [], []
    for connection, bucket in keys:
        try:
            connection, bucket = _decode_projection(connection), _decode_projection(bucket)
            if any(not _ID.fullmatch(v) for v in (connection, bucket)):
                raise ValueError()
        except (ValueError, TypeError):
            raise StorageError('invalid_artifact_index') from None
        scope = Scope(workspace, connection, bucket)
        try:
            if status is None:
                record = authorized(policy, actor, db, scope, 'read', budget=budget)
                # authorized validates the entire bound publication (including
                # scope, configuration, fingerprint and provenance), not candidate.
                if record.published_raw is None:
                    continue
                item = {'scope': scope.as_dict(), 'catalog_revision': record.published_revision}
                proof = record.provenance
                approver = proof['actor']
                member = policy._memberships[(workspace, approver)]
                fence = [grant_fence(record, actor), approver, proof['authority'],
                         principal_pin(policy, approver),
                         [member.role, member.state, member.revision,
                          policy._membership_authority[(workspace, approver)]],
                         proof['membership_revision'], proof['policy_digest']]
            else:
                current, fence = status(policy, actor, db, scope, budget=budget)
                item = {'scope': scope.as_dict(), 'capabilities': current['capabilities']}
        except PolicyError as error:
            if error.status not in (403, 404):
                raise
            continue
        visible.append(((connection, bucket), item))
        authority.append([(connection, bucket), fence])
    return Projection(fingerprint(authority), fingerprint([key for key, _ in visible]), tuple(visible))


def grant_fence(record, actor):
    """Existing validated grant only. Never recapture/regrant during discovery."""
    return [record.grant_revision, digest(record.policy_raw),
            record.authority['realm'], record.authority['principals'][actor]]
