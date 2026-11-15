# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Bounded, explicit GUI smoke observations. Not execution or wire compatibility."""
from dataclasses import dataclass
from datetime import datetime
import json
import re

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
STATES = frozenset({"pending", "running", "success", "failed", "cancelled", "unknown"})
SOURCES = frozenset({"web", "scheduled", "api", "retry", "unknown"})
STAGE_STATES = STATES | {"skipped"}
MAX_TASKS = 500
MAX_BYTES = 1024 * 1024
MAX_REVISION = 2**53 - 1


def identifier(value):
    if type(value) is not str or not _ID.fullmatch(value):
        raise ValueError("invalid_identifier")
    return value


def text(value, limit):
    if type(value) is not str or not value.strip() or len(value.encode("utf-8")) > limit:
        raise ValueError("invalid_text")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("invalid_text")
    return value


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("invalid_integer")
    return value


def timestamp(value):
    if type(value) is not str or len(value) > 64:
        raise ValueError("invalid_timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone_required")
    return parsed


def choice(value, allowed):
    if type(value) is not str or value not in allowed:
        raise ValueError("invalid_enum")
    return value


def exact(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError("invalid_fields")
    return value


def encode(value):
    result = json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
    if len(result) > MAX_BYTES:
        raise ValueError("snapshot_too_large")
    return result


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_field")
        result[key] = value
    return result


def decode(value):
    if type(value) is not str or len(value.encode("utf-8")) > MAX_BYTES:
        raise ValueError("invalid_snapshot")
    return json.loads(value, object_pairs_hook=_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid_number")))


@dataclass(frozen=True)
class SmokeScope:
    workspace_id: str
    connection_id: str
    repository_id: str

    def __post_init__(self):
        for value in (self.workspace_id, self.connection_id, self.repository_id):
            identifier(value)

    def as_dict(self):
        return {"workspace_id": self.workspace_id, "connection_id": self.connection_id,
                "repository_id": self.repository_id}


def reported_state(raw):
    """Unknown external values are not success; this never infers from counts."""
    return raw if type(raw) is str and raw in STATES else "unknown"


def _times(value, observed, state):
    created = timestamp(value["created_at"]) if "created_at" in value else None
    started = timestamp(value["started_at"]) if value["started_at"] is not None else None
    finished = timestamp(value["finished_at"]) if value["finished_at"] is not None else None
    if any(t is not None and t > observed for t in (created, started, finished)):
        raise ValueError("future_task_time")
    if ((created is not None and any(t is not None and t < created for t in (started, finished)))
            or (started is not None and finished is not None and finished < started)):
        raise ValueError("reversed_task_time")
    if (state == "pending" and (started is not None or finished is not None)) or (state == "running" and finished is not None):
        raise ValueError("contradictory_task_time")
    # Missing timings remain unknown, never zero-filled or replaced by observation.
    return (finished - started).total_seconds() if started is not None and finished is not None else None


def project_task(value, observed_at):
    """Validate an already selected task projection, not raw panel JSON.

    No submitter, email, runner, URL, path, log, raw error or report HTML is allowed.
    Trusted ingestion must select content approved for the explicit readers and
    prove source repository scope. This is not redaction or an upstream fetcher.
    """
    exact(value, {"id", "status", "source", "branch", "created_at", "started_at", "finished_at",
                  "commit", "requested_commit", "summary", "stages"})
    observed = timestamp(observed_at)
    identifier(value["id"])
    state = choice(value["status"], STATES)
    choice(value["source"], SOURCES)
    text(value["branch"], 400)
    for key in ("commit", "requested_commit"):
        if value[key] is not None and (type(value[key]) is not str or not _SHA.fullmatch(value[key])):
            raise ValueError("invalid_commit")
    if state == "pending" and value["commit"] is not None:
        raise ValueError("pending_is_not_execution_evidence")
    duration = _times(value, observed, state)
    summary = value["summary"]
    if summary is not None:
        exact(summary, {"passed", "failed", "skipped", "total"})
        for count in summary.values():
            integer(count, 0, 1000000)
        if sum(summary[k] for k in ("passed", "failed", "skipped")) != summary["total"]:
            raise ValueError("inconsistent_counts")
        if state == "success" and summary["failed"]:
            raise ValueError("contradictory_success")
    stages = value["stages"]
    normalized_stages = None
    if stages is not None:
        if type(stages) is not list or len(stages) > 20:
            raise ValueError("invalid_stages")
        normalized_stages, seen = [], set()
        for stage in stages:
            exact(stage, {"id", "name", "status", "started_at", "finished_at"})
            identity = identifier(stage["id"])
            if identity in seen:
                raise ValueError("duplicate_stage")
            seen.add(identity)
            text(stage["name"], 400)
            choice(stage["status"], STAGE_STATES)
            elapsed = _times(stage, observed, stage["status"])
            normalized_stages.append(dict(stage, duration_seconds=elapsed))
    return {**value, "summary": dict(summary) if summary is not None else None,
            "stages": normalized_stages, "duration_seconds": duration}


def stored_task(value, observed_at):
    """Return only canonical input fields; derived values never enter storage."""
    projected = project_task(value, observed_at)
    projected.pop("duration_seconds")
    if projected["stages"] is not None:
        projected["stages"] = [{k: v for k, v in stage.items() if k != "duration_seconds"}
                               for stage in projected["stages"]]
    return projected
