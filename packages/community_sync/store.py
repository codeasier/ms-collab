# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Atomic exact-scope snapshots. This trusted storage layer is not user auth."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3

from packages.community_board.delta import compare_snapshots
from packages.community_board.model import BoardItem, Scope, Snapshot, utc_time
from .provider import Connection, BoundCredential, GitCodeProvider, ProviderError

_MAX_PAYLOAD = 16 * 1024 * 1024


class StoreError(RuntimeError):
    pass


class Conflict(StoreError):
    """Expected revision no longer current; caller must re-observe, not overwrite."""


@dataclass(frozen=True, slots=True)
class StoredSnapshot:
    revision: int
    snapshot: Snapshot


def _encode(snapshot):
    payload = json.dumps({"observed_at": snapshot.observed_at,
                       "items": [item.as_dict() for item in snapshot.items]},
                      separators=(",", ":"), ensure_ascii=True)
    if len(payload) > _MAX_PAYLOAD:
        raise StoreError("snapshot payload exceeds storage limit")
    return payload


def _decode(scope, payload):
    raw = json.loads(payload)
    if set(raw) != {"observed_at", "items"} or not isinstance(raw["items"], list):
        raise ValueError("invalid stored snapshot")
    if any(set(row) != {"kind", "number", "title", "state", "updated_at", "labels"} for row in raw["items"]):
        raise ValueError("invalid stored item")
    return Snapshot(scope, raw["observed_at"], tuple(BoardItem(**row) for row in raw["items"]))


class SnapshotStore:
    """Private operator-owned SQLite path; explicit creation, no implicit bootstrap.

    Callers must authorize workspace capability and connection access first.
    Scope checking prevents accidental cross-connection publication, not a
    malicious caller with direct database or Python access.
    """
    def __init__(self, path):
        self.path = Path(path).absolute()

    def initialize(self):
        # Never reset an existing store. Mode 0600 also applies before SQLite opens.
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            with sqlite3.connect(self.path) as db:
                db.executescript("""
                    CREATE TABLE metadata (version INTEGER NOT NULL);
                    INSERT INTO metadata VALUES (1);
                    CREATE TABLE snapshots (
                      workspace TEXT NOT NULL, connection TEXT NOT NULL, repository TEXT NOT NULL,
                      revision INTEGER NOT NULL CHECK(revision > 0), payload TEXT NOT NULL,
                      PRIMARY KEY (workspace, connection, repository));
                """)
        except Exception:
            self.path.unlink()
            raise

    def _connect(self):
        try:
            db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=5)
            if db.execute("SELECT version FROM metadata").fetchall() != [(1,)]:
                db.close()
                raise StoreError("unsupported snapshot store")
            return db
        except sqlite3.Error:
            if "db" in locals():
                db.close()
            raise StoreError("snapshot store unavailable") from None

    @staticmethod
    def _read(db, scope):
        row = db.execute("SELECT revision,payload FROM snapshots WHERE workspace=? AND connection=? AND repository=?",
                         (scope.workspace_id, scope.connection_id, scope.repository)).fetchone()
        if row is None:
            return None
        try:
            if type(row[0]) is not int or row[0] < 1 or len(row[1]) > _MAX_PAYLOAD:
                raise ValueError("invalid stored revision or size")
            return StoredSnapshot(row[0], _decode(scope, row[1]))
        except (TypeError, ValueError, KeyError, RecursionError, OverflowError):
            raise StoreError("stored snapshot is invalid") from None

    def load(self, scope: Scope) -> StoredSnapshot | None:
        if not isinstance(scope, Scope):
            raise ValueError("explicit scope required")
        db = self._connect()
        try:
            return self._read(db, scope)
        except sqlite3.Error:
            raise StoreError("snapshot store unavailable") from None
        finally:
            db.close()

    def publish(self, snapshot: Snapshot, *, expected_revision: int) -> StoredSnapshot:
        if not isinstance(snapshot, Snapshot) or type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("snapshot and nonnegative revision required")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            previous = self._read(db, snapshot.scope)
            if (previous.revision if previous else 0) != expected_revision:
                raise Conflict("snapshot changed; re-observe before publishing")
            if previous:
                # Reject stale observations and changed payload at an identical item
                # timestamp. Equal-timestamp changes need explicit reconciliation.
                compare_snapshots(previous.snapshot, snapshot)
                before = {item.key: item for item in previous.snapshot.items}
                for item in snapshot.items:
                    old = before.get(item.key)
                    if old and utc_time(old.updated_at) == utc_time(item.updated_at) and old != item:
                        raise Conflict("ambiguous item timestamp; reconcile before publishing")
                if snapshot == previous.snapshot:
                    return previous
                if utc_time(snapshot.observed_at) == utc_time(previous.snapshot.observed_at):
                    raise Conflict("ambiguous observation timestamp")
            result = StoredSnapshot(expected_revision + 1, snapshot)
            db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?) ON CONFLICT(workspace,connection,repository) "
                       "DO UPDATE SET revision=excluded.revision,payload=excluded.payload",
                       (snapshot.scope.workspace_id, snapshot.scope.connection_id, snapshot.scope.repository,
                        result.revision, _encode(snapshot)))
            db.commit()
            return result
        except sqlite3.Error:
            raise StoreError("snapshot publication failed") from None
        finally:
            # Also rolls back any exception before commit; no partial refresh.
            db.close()


def refresh(connection: Connection, credential: BoundCredential, store: SnapshotStore, *,
            provider: GitCodeProvider | None = None, observed_at: str) -> StoredSnapshot:
    """Explicit trusted refresh; no scheduler, credential discovery or external writes.

    observed_at is the authorized job's observation cut-off (UTC-aware). A row
    newer than the cut-off aborts. Caller retries with a fresh cut-off, never
    marks a scan as complete. Network work is outside the short DB transaction.
    """
    previous = store.load(connection.scope)
    snapshot = (provider or GitCodeProvider()).observe(connection, credential, observed_at=observed_at)
    if not isinstance(snapshot, Snapshot) or snapshot.scope != connection.scope:
        raise ProviderError("provider returned a different snapshot scope")
    return store.publish(snapshot, expected_revision=previous.revision if previous else 0)
