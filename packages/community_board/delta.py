# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Explicit observed-state changes, not inferred lifecycle events.

Adapted from the prior community-board snapshot/delta concepts. The public
contract uses composite scope and treats missing records as unavailable, not
closed, because snapshots may cover a bounded scan window.
"""
from __future__ import annotations

from .model import Snapshot, utc_time


def compare_snapshots(previous: Snapshot | None, current: Snapshot) -> dict:
    """Compare observations of one exact scope without mutating either input.

    A first snapshot establishes a baseline, not a list of newly created items.
    A stale item observation is rejected instead of reporting a false change.
    Equal timestamps with different fields are allowed but are only *observed
    differences*, never proof of authoritative webhook ordering.
    """
    if not isinstance(current, Snapshot) or (previous is not None and not isinstance(previous, Snapshot)):
        raise ValueError("expected Snapshot inputs")
    result = {
        "scope": current.scope.as_dict(), "has_previous": previous is not None,
        "previous_at": previous.observed_at if previous else None,
        "current_at": current.observed_at, "coverage": "observed_subset",
        "new": [], "reopened": [], "closed": [], "merged": [],
        "updated": [], "unavailable": [],
    }
    if previous is None:
        return result
    if previous.scope != current.scope:
        raise ValueError("cannot compare different repository scopes")
    if utc_time(current.observed_at) < utc_time(previous.observed_at):
        raise ValueError("snapshot observation moved backwards")
    before = {item.key: item for item in previous.items}
    after = {item.key: item for item in current.items}
    for key in sorted(before.keys() | after.keys(), key=lambda key: (key[0], -key[1])):
        old, new = before.get(key), after.get(key)
        if new is None:
            # The item can be filtered, outside the scan window or inaccessible.
            # Do not represent the stale record as a current closed/deleted item.
            result["unavailable"].append({"kind": key[0], "number": key[1]})
            continue
        if old is None:
            result["new"].append(new.as_dict())
            continue
        if utc_time(new.updated_at) < utc_time(old.updated_at):
            raise ValueError("item observation moved backwards")
        if old == new:
            continue
        category = "updated"
        if new.state != old.state:
            category = {"open": "reopened", "closed": "closed", "merged": "merged"}[new.state]
        result[category].append(new.as_dict())
    return result
