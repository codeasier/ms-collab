# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Bounded, immutable snapshots for the community-board read model.

No network, credential discovery, persistent storage or provider mutation occurs
in this module. Inputs must already be authorized and normalized by the caller.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re


_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}\Z")
_KINDS = frozenset({"issue", "pull"})
_STATES = frozenset({"open", "closed", "merged"})


def _identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f"invalid {name}")


def utc_time(value: str) -> datetime:
    """Require explicit timezones; never invent timezone or truncate precision."""
    if not isinstance(value, str):
        raise ValueError("timestamp must be a timezone-aware ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid ISO 8601 timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp requires an explicit timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class Scope:
    """Repository identity includes workspace and provider connection."""

    workspace_id: str
    connection_id: str
    repository: str

    def __post_init__(self) -> None:
        _identifier(self.workspace_id, "workspace_id")
        _identifier(self.connection_id, "connection_id")
        if not isinstance(self.repository, str) or len(self.repository.split("/")) != 2:
            raise ValueError("repository must be owner/name")
        owner, name = self.repository.split("/")
        _identifier(owner, "repository owner")
        _identifier(name, "repository name")

    def as_dict(self) -> dict:
        return {
            "workspace_id": self.workspace_id,
            "connection_id": self.connection_id,
            "repository": self.repository,
        }


@dataclass(frozen=True, slots=True)
class BoardItem:
    """Small allowlisted record, not a raw provider response.

    Titles and labels remain untrusted text. A UI must escape them, not render
    them as HTML. Descriptions, user profiles, tokens and provider URLs are not
    accepted into this initial public projection contract.
    """

    kind: str
    number: int
    title: str
    state: str
    updated_at: str
    labels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError("kind must be issue or pull")
        if type(self.number) is not int or self.number <= 0:
            raise ValueError("number must be a positive integer")
        if not isinstance(self.title, str) or not 1 <= len(self.title) <= 1000:
            raise ValueError("title must contain 1..1000 characters")
        if self.state not in _STATES or (self.kind == "issue" and self.state == "merged"):
            raise ValueError("unsupported item state")
        utc_time(self.updated_at)
        labels = tuple(self.labels)
        if isinstance(self.labels, str) or len(labels) > 100:
            raise ValueError("labels must be a sequence of at most 100 strings")
        if any(not isinstance(label, str) or not 1 <= len(label) <= 100 for label in labels):
            raise ValueError("invalid label")
        object.__setattr__(self, "labels", labels)

    @property
    def key(self) -> tuple[str, int]:
        return self.kind, self.number

    def as_dict(self) -> dict:
        return {
            "kind": self.kind, "number": self.number, "title": self.title,
            "state": self.state, "updated_at": self.updated_at,
            "labels": list(self.labels),
        }


@dataclass(frozen=True, slots=True)
class Snapshot:
    """An observed subset; omission is never evidence that an item was closed."""

    scope: Scope
    observed_at: str
    items: tuple[BoardItem, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.scope, Scope):
            raise ValueError("scope must be a Scope")
        observed_at = utc_time(self.observed_at)
        records = tuple(self.items)
        if len(records) > 10000 or any(not isinstance(item, BoardItem) for item in records):
            raise ValueError("snapshot must contain at most 10000 BoardItem records")
        if len({item.key for item in records}) != len(records):
            raise ValueError("duplicate item identity in snapshot")
        if any(utc_time(item.updated_at) > observed_at for item in records):
            raise ValueError("item is newer than snapshot observation")
        object.__setattr__(self, "items", records)


class UnknownWorkspace(LookupError):
    """No configured projection exists for the requested workspace."""


class BoardProjection:
    """Read-only projection, with explicit workspace-scoped lookup.

    The caller must authorize workspace membership and capability enablement.
    Passing an arbitrary workspace ID here is not authorization. In particular,
    the public workspace is an ordinary separate scope, not a private-data union.
    """

    def __init__(self, snapshots: tuple[Snapshot, ...] | list[Snapshot]) -> None:
        values = tuple(snapshots)
        if any(not isinstance(value, Snapshot) for value in values):
            raise ValueError("snapshots must contain only Snapshot objects")
        if len({value.scope for value in values}) != len(values):
            raise ValueError("duplicate repository scope")
        self._snapshots = values
        self._workspaces = frozenset(value.scope.workspace_id for value in values)

    def list_items(
        self, workspace_id: str, *, kind: str | None = None,
        state: str | None = None, query: str = "", limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Return a stable page and counts over the current observed subset.

        No pagination request performs network I/O or changes any projection.
        Unknown workspaces fail closed; there is deliberately no all-workspaces
        fallback, implicit sharing, or provider URL in the response.
        """
        _identifier(workspace_id, "workspace_id")
        if workspace_id not in self._workspaces:
            raise UnknownWorkspace("workspace projection is unavailable")
        if kind is not None and kind not in _KINDS:
            raise ValueError("invalid kind filter")
        if state is not None and state not in _STATES:
            raise ValueError("invalid state filter")
        if not isinstance(query, str) or len(query) > 200:
            raise ValueError("query must contain at most 200 characters")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        selected = [s for s in self._snapshots if s.scope.workspace_id == workspace_id]
        needle = query.casefold().strip()
        rows = [
            (snapshot, item)
            for snapshot in selected for item in snapshot.items
            if (kind is None or item.kind == kind)
            and (state is None or item.state == state)
            and (not needle or needle in item.title.casefold()
                 or any(needle in label.casefold() for label in item.labels))
        ]
        # Stable tie-breaking across repositories, connections and issue/PR IDs.
        rows.sort(key=lambda row: (
            row[0].scope.connection_id, row[0].scope.repository,
            row[1].kind, -row[1].number,
        ))
        counts = {"issue": 0, "pull": 0, "open": 0, "closed": 0, "merged": 0}
        for _, item in rows:
            counts[item.kind] += 1
            counts[item.state] += 1
        return {
            "schema": "ms-collab/community-board/v1",
            "workspace_id": workspace_id,
            "coverage": "observed_subset",
            "sources": [dict(s.scope.as_dict(), observed_at=s.observed_at)
                        for s in sorted(selected, key=lambda s: (s.scope.connection_id, s.scope.repository))],
            "total": len(rows), "offset": offset, "limit": limit,
            "counts": counts,
            "items": [dict(s.scope.as_dict(), **item.as_dict())
                      for s, item in rows[offset:offset + limit]],
        }
