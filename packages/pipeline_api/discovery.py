# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Current-authority directory of stored pipeline snapshots, never providers.

The existing SQLite loader has already loaded global policy/resources. Bounds
here apply only to the additional directory projection and snapshot parsing.
"""
from datetime import datetime, timezone
import re
from urllib.parse import parse_qsl

from packages.pipelines import InvalidSnapshot, timestamp
from packages.workspaces import PolicyError
from packages.workspaces.model import revision
from .records import MAX_BYTES, InvalidRecord, decode_snapshots, resource_id, snapshot_scope

MAX_SCOPES = 256
MAX_DIRECTORY_BYTES = 1048576
PAGE_SIZE = 25
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')
_KEY = re.compile(r'pipeline-[a-f0-9]{64}\Z')


def directory_request(environ):
    path = environ.get('PATH_INFO', '')
    if type(path) is not str or len(path) > 1000:
        raise PolicyError(400, 'invalid_request')
    parts = path.split('/')
    if len(parts) != 6 or parts[:3] != ['', 'api', 'workspaces'] or parts[4:] != ['pipelines', 'scopes']:
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


def directory(policy, actor, db, workspace, after, *, clock):
    # Current module read covers all its stored snapshots. There are no separate
    # reader/refresher/approver grants or publication provenance in this model.
    policy.authorize(actor, workspace, 'pipelines', 'read')
    # The existing composite primary key supports a bounded BINARY prefix range;
    # a substr(id,...) predicate would scan unrelated generic resources first.
    rows = db.execute('''SELECT workspace_id,module_key,substr(CAST(id AS BLOB),1,74),substr(CAST(text AS BLOB),1,?),revision
        FROM resources WHERE workspace_id=? AND module_key='pipelines'
        AND id >= 'pipeline-' AND id < 'pipeline.' ORDER BY id LIMIT ?''',
        (MAX_BYTES + 1, workspace, MAX_SCOPES + 1)).fetchall()
    if len(rows) > MAX_SCOPES:
        raise PolicyError(503, 'scope_directory_unavailable')
    visible, used = [], 0
    try:
        now = clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise InvalidRecord('invalid_clock')
        for outer_workspace, module, key_bytes, raw_bytes, version in rows:
            # BLOB substr preserves embedded NUL. TEXT substr could silently
            # validate a prefix different from the exact resource read.
            if type(key_bytes) is not bytes or len(key_bytes) > 73:
                raise InvalidRecord('invalid_binding')
            key = key_bytes.decode('ascii')
            if outer_workspace != workspace or module != 'pipelines' or not _KEY.fullmatch(key):
                raise InvalidRecord('invalid_binding')
            revision(version)
            if type(raw_bytes) is not bytes or len(raw_bytes) > MAX_BYTES:
                raise InvalidRecord('invalid_record')
            used += len(raw_bytes)
            if used > MAX_DIRECTORY_BYTES:
                raise PolicyError(503, 'scope_directory_unavailable')
            raw = raw_bytes.decode('utf-8')
            scope = snapshot_scope(raw)
            if scope.workspace_id != workspace or resource_id(scope) != key:
                raise InvalidRecord('invalid_binding')
            snapshots = decode_snapshots(scope, raw)
            if any(timestamp(row['observed_at']) > now.astimezone(timezone.utc) for row in snapshots):
                raise InvalidRecord('future_observation')
            visible.append(((scope.connection_id, scope.pipeline_id),
                            {'scope': scope.as_dict(), 'snapshot_revision': version}))
    except (InvalidRecord, InvalidSnapshot, ValueError, TypeError, UnicodeError):
        raise PolicyError(503, 'pipeline_data_unavailable') from None
    visible.sort(key=lambda entry: entry[0])
    start = 0
    if after is not None:
        positions = [i for i, (key, _) in enumerate(visible) if key == after]
        if not positions:
            raise PolicyError(409, 'scope_directory_changed')
        start = positions[0] + 1
    page = visible[start:start + PAGE_SIZE]
    next_after = '~'.join(page[-1][0]) if start + len(page) < len(visible) else None
    return {'workspace_id': workspace, 'items': [item for _, item in page], 'next_after': next_after}
