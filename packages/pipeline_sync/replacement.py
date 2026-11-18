# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Compare two bounded V1 snapshots supplied by an already-authorized caller."""
from dataclasses import dataclass, field
import hashlib

from packages.pipeline_api import InvalidRecord, decode_snapshots, encode_snapshots
from packages.pipelines import PipelineScope
from packages.workspaces.model import revision
from .provider import ProviderError


@dataclass(frozen=True, slots=True)
class ReplacementDescription:
    scope: PipelineScope = field(repr=False)
    expected_revision: int
    old_count: int
    new_count: int
    removed_ids: tuple[str, ...] = field(repr=False)
    previous_utf8_sha256: str = field(repr=False)
    candidate_utf8_sha256: str = field(repr=False)


def describe_replacement(scope, expected_revision, old_text, new_text) -> ReplacementDescription:
    """Describe a local subset replacement, not a grant or a review receipt.

    An omitted ID says nothing about upstream history. Neither this comparison
    nor its hashes establish the source, freshness, approval or authority.
    """
    if type(scope) is not PipelineScope:
        raise InvalidRecord("invalid_scope")
    revision(expected_revision)
    old = decode_snapshots(scope, old_text)
    new = decode_snapshots(scope, new_text)
    if not new:
        raise ProviderError("empty_observation")
    # Old records may predate the canonical representation. A new candidate
    # must already be the exact bytes the V1 projection would publish.
    if encode_snapshots(scope, new) != new_text:
        raise ProviderError("invalid_review_candidate")
    removed = {r["id"] for r in old} - {r["id"] for r in new}
    return ReplacementDescription(
        scope=scope, expected_revision=expected_revision, old_count=len(old), new_count=len(new),
        removed_ids=tuple(sorted(removed)),
        previous_utf8_sha256=hashlib.sha256(old_text.encode("utf-8")).hexdigest(),
        candidate_utf8_sha256=hashlib.sha256(new_text.encode("utf-8")).hexdigest(),
    )
