# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Small, unprivileged value for an explicitly prepared Issue suggestion."""

from dataclasses import dataclass, field
import re

from .core import FIELD_ID

MAX_ISSUE_NUMBER = 9007199254740991
_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class IssueSuggestion:
    """Ordered field IDs, not assignment, ownership, or evidence of provenance."""

    number: int
    fingerprint: str = field(repr=False)
    field_ids: tuple[str, ...]

    def __post_init__(self):
        if type(self.number) is not int or not 1 <= self.number <= MAX_ISSUE_NUMBER:
            raise ValueError("invalid_issue_number")
        if type(self.fingerprint) is not str or not _FINGERPRINT.fullmatch(self.fingerprint):
            raise ValueError("invalid_issue_fingerprint")
        if (type(self.field_ids) is not tuple or not 1 <= len(self.field_ids) <= 3
                or any(type(key) is not str or not FIELD_ID.fullmatch(key) for key in self.field_ids)
                or len(set(self.field_ids)) != len(self.field_ids)):
            raise ValueError("invalid_suggested_fields")
