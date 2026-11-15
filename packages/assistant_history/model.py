# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Allowlisted stored display records, not an ACP client or execution request."""
from dataclasses import dataclass
from datetime import datetime
import json
import re

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}\Z")
KINDS = frozenset({"user_message", "assistant_message", "agent_thought", "tool", "notice"})
STATUSES = frozenset({"pending", "in_progress", "completed", "failed", "canceled"})
MAX_EVENTS = 2000
MAX_BYTES = 1024 * 1024
MAX_REVISION = 2**53 - 1


def identifier(value):
    if type(value) is not str or not _ID.fullmatch(value):
        raise ValueError("invalid_identifier")
    return value


def positive(value):
    if type(value) is not int or not 1 <= value <= MAX_REVISION:
        raise ValueError("invalid_revision")
    return value


def text(value, *, limit, empty=False):
    if type(value) is not str or (not empty and not value.strip()):
        raise ValueError("invalid_text")
    try:
        if len(value.encode("utf-8")) > limit or "\x00" in value:
            raise ValueError("invalid_text")
    except UnicodeError:
        raise ValueError("invalid_text") from None
    return value


def timestamp(value):
    if type(value) is not str or len(value) > 64:
        raise ValueError("invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
    except (ValueError, OverflowError):
        raise ValueError("invalid_timestamp") from None
    return parsed


def encode(value):
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("ascii")) > MAX_BYTES:
        raise ValueError("record_too_large")
    return encoded


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_field")
        value[key] = item
    return value


def decode(value):
    if type(value) is not str or len(value) > MAX_BYTES:
        raise ValueError("invalid_record")
    return json.loads(value, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


@dataclass(frozen=True)
class DisplayEvent:
    """Already selected user-visible text. No raw SDK object or secret scrubber.

    `agent_thought` is only an actual upstream user-visible report, never an
    inferred/private reasoning trace. Tool statuses are reports, not proof.
    Consumers must render every string as text, never fetch links or execute it.
    """
    kind: str
    text: str
    tool_name: str | None = None
    status: str | None = None

    def __post_init__(self):
        if type(self.kind) is not str or self.kind not in KINDS:
            raise ValueError("invalid_event_kind")
        text(self.text, limit=8192, empty=self.kind == "tool")
        if self.kind == "tool":
            text(self.tool_name, limit=200)
            if type(self.status) is not str or self.status not in STATUSES:
                raise ValueError("invalid_tool_status")
        elif self.tool_name is not None or self.status is not None:
            raise ValueError("unexpected_tool_fields")

    def as_dict(self):
        value = {"kind": self.kind, "text": self.text}
        if self.kind == "tool":
            value.update(tool_name=self.tool_name, status=self.status)
        return value

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) not in ({"kind", "text"}, {"kind", "text", "tool_name", "status"}):
            raise ValueError("invalid_event")
        return cls(**value)


def project_observation(value):
    """Normalize an explicitly user-addressed allowlisted observation.

    This is NOT ACP wire compatibility or redaction. A trusted producer must
    sanitize content and prove its source permissions before calling this.
    Unknown fields (including raw input/output, locators, config and URLs as
    metadata) are rejected; omitted/non-user audience is not a display grant.
    """
    if type(value) is not dict or "audience" not in value:
        raise ValueError("explicit_audience_required")
    audience = value["audience"]
    if type(audience) is not list or len(audience) > 2 or any(type(x) is not str or x not in {"user", "assistant"} for x in audience) or len(set(audience)) != len(audience):
        raise ValueError("invalid_audience")
    event = DisplayEvent.from_dict({key: item for key, item in value.items() if key != "audience"})
    return event if "user" in audience else None
