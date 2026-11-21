# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Ephemeral prepared batches; no I/O, job, decoder, or external producer."""

from dataclasses import dataclass, field
from hashlib import sha256
from os import getpid

from packages.community_api.records import encode, fingerprint
from packages.community_board.model import Scope
from packages.responsibility.suggestions import IssueSuggestion
from packages.workspaces import PolicyError
from packages.workspaces.authority import principal_pin

_MAX_SCOPES = 32
_MAX_ENTRIES = 200


@dataclass(frozen=True, slots=True)
class _PreparedSuggestions:
    # A process-local validated value, not a serialization contract or credential.
    scope: Scope
    records: tuple[IssueSuggestion, ...] = field(repr=False)
    stamp: str = field(repr=False)
    publisher: str = field(repr=False)
    store: object = field(repr=False, compare=False)
    pid: int = field(repr=False)


def index_batches(batches, store):
    if (type(batches) is not tuple or len(batches) > _MAX_SCOPES
            or any(type(batch) is not _PreparedSuggestions
                   or batch.pid != getpid() or batch.store is not store for batch in batches)):
        raise ValueError("prepared_suggestions_require_current_process_and_store")
    for batch in batches:
        validate_records(batch.records)
    index = {batch.scope: batch for batch in batches}
    if len(index) != len(batches):
        raise ValueError("duplicate_suggestion_scope")
    return index


def validate_records(records):
    if (type(records) is not tuple or not 1 <= len(records) <= _MAX_ENTRIES
            or any(type(record) is not IssueSuggestion for record in records)
            or len({record.number for record in records}) != len(records)):
        raise ValueError("invalid_suggestions")


def _digest(text):
    return sha256(text.encode("ascii")).hexdigest()


def _stamp(policy, scope, state, community, publisher, records):
    workspace = scope.workspace_id
    readers = set(state["policy"].reader_ids) & set(community.policy.reader_ids)
    # Persisted ACLs pin identity; membership revisions prevent a revoked and
    # subsequently restored reader from reviving this prepared audience.
    memberships = [
        (ident, member.revision if member is not None else None)
        for ident in sorted(readers)
        for member in (policy._memberships.get((workspace, ident)),)
    ]
    return _digest(encode([
        "responsibility-routing-suggestion/v1",
        scope.as_dict(), principal_pin(policy, publisher),
        policy._memberships[(workspace, publisher)].revision, memberships,
        policy._workspaces[workspace].revision,
        [policy._modules[(workspace, key)].revision for key in ("community", "responsibility")],
        community.revision, _digest(community.config_raw),
        state["config_version"], _digest(state["fields_raw"]), _digest(state["policy_raw"]),
        [(record.number, record.fingerprint, record.field_ids) for record in records],
    ]))


def _approved_issues(scope, community, wanted):
    if community.snapshot is None or community.snapshot.scope != scope:
        return
    for item in community.snapshot.items:
        if item.kind == "issue" and item.state == "open" and item.number in wanted:
            digest = fingerprint(scope, item)
            if digest in community.approvals:
                yield item.number, digest


def prepare(policy, actor, scope, state, community, records, store):
    if not state["fields"]:
        raise PolicyError(409, "suggestion_configuration_unavailable")
    available = dict(_approved_issues(scope, community, {record.number for record in records}))
    developer_fields = {item["id"] for item in state["fields"] if item["kind"] == "developer"}
    if any(available.get(record.number) != record.fingerprint
           or not developer_fields.issuperset(record.field_ids) for record in records):
        raise PolicyError(400, "invalid_suggestion_target")
    return _PreparedSuggestions(
        scope, records, _stamp(policy, scope, state, community, actor, records), actor, store, getpid()
    )


def approved_fingerprint(batch, scope, state, community, number):
    # The service already authorized the requesting reader and checked current
    # config references. Look up a prepared record only for an approved Issue.
    if batch is None or batch.pid != getpid() or not state["fields"]:
        return None
    return next((digest for _, digest in _approved_issues(scope, community, {number})), None)


def select(batch, policy, scope, state, community, number, digest):
    if batch.stamp != _stamp(policy, scope, state, community, batch.publisher, batch.records):
        return None
    return next((record for record in batch.records
                 if record.number == number and record.fingerprint == digest), None)
