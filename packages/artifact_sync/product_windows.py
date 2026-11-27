# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Resolve the four OBS product-view quick ranges, never an authority or a clock input."""
from datetime import datetime, time, timedelta, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from packages.workspaces import StorageError

# Only explicit geographic IANA keys, UTC, and Etc/ zones. In particular, no
# host-local aliases, alternate TZif trees, path traversal, or POSIX TZ strings.
_ZONE = re.compile(r'(?:UTC|(?:Africa|America|Antarctica|Arctic|Asia|Atlantic|Australia|'
                   r'Europe|Indian|Pacific|Etc)(?:/[A-Za-z0-9_+-]+){1,2})')
_RELATIVE = frozenset(('today', 'week', 'month', '30d'))


def separate_window(body):
    """Leave old requests completely unchanged. The caller validates the remaining fields."""
    if type(body) is not dict or not ({'relative_range', 'time_zone'} & body.keys()):
        return body, None
    kind, key = body.get('relative_range'), body.get('time_zone')
    if (body.get('mode') != 'files' or 'start_time' in body or 'end_time' in body
            or type(kind) is not str or kind not in _RELATIVE or type(key) is not str
            or len(key) > 96 or not _ZONE.fullmatch(key)):
        raise ValueError('invalid product relative window')
    try:
        zone = ZoneInfo(key)
    except ZoneInfoNotFoundError:
        raise ValueError('unknown product time zone') from None
    return {k: v for k, v in body.items() if k not in ('relative_range', 'time_zone')}, (kind, zone)


def resolve_window(spec, clock):
    """OBS first subtracts elapsed days for week/30d, then snaps to civil midnight."""
    kind, zone = spec
    try:
        now = clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('aware server clock required')
        utc = now.astimezone(timezone.utc)
        today = utc.astimezone(zone).date()
        if kind in ('week', '30d'):
            start = (utc - timedelta(days=7 if kind == 'week' else 30)).astimezone(zone).date()
        elif kind == 'month':
            start = today.replace(day=1)
        else:
            start = today
        # fold=0 matches JS Date's earlier occurrence on ambiguous wall times.
        # A nonexistent midnight uses its pre-transition offset (advance through gap).
        def stamp(day, wall):
            return datetime.combine(day, wall, zone).astimezone(timezone.utc).isoformat()
        return {'start_time': stamp(start, time.min), 'end_time': stamp(today, time(23, 59, 59))}
    except (OverflowError, TypeError, ValueError):
        raise StorageError('invalid_product_clock') from None
