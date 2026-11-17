"""Bounded canonical snapshot envelopes for a trusted, separate importer.

This is encoding/validation, not provider access, ingestion authority or a writer.
Labels are untrusted text and the codec is not a secret scrubber.
"""
import hashlib
import json
import re

from packages.pipelines import PipelineScope, timestamp

MAX_BYTES = 10000
MAX_RUNS = 100
MAX_STAGES = 40
MAX_JOBS = 200
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_STATES = frozenset({"success", "failure", "cancelled", "timed_out", "skipped", "queued", "running", "unknown"})


class InvalidRecord(ValueError):
    """Value-free failure for both trusted importer input and stored corruption."""


def resource_id(scope):
    if type(scope) is not PipelineScope:
        raise InvalidRecord("invalid_scope")
    # Length-prefixed/JSON tuple avoids ambiguous separators and traversal.
    key = json.dumps([scope.connection_id, scope.pipeline_id], separators=(",", ":"))
    return "pipeline-" + hashlib.sha256(key.encode("ascii")).hexdigest()


def _exact(value, required):
    if type(value) is not dict or set(value) != set(required):
        raise InvalidRecord("invalid_record")


def _id(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise InvalidRecord("invalid_record")
    return value


def _label(value):
    if (type(value) is not str or not 1 <= len(value) <= 256
            or value != value.strip() or not all(c.isprintable() for c in value)):
        raise InvalidRecord("invalid_record")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise InvalidRecord("invalid_record") from None
    return value


def _time(value, *, nullable=False):
    if nullable and value is None:
        return None
    parsed = timestamp(value)
    if parsed is None:
        raise InvalidRecord("invalid_record")
    return parsed.isoformat()


def _state(value):
    if type(value) is not str or value not in _STATES:
        raise InvalidRecord("invalid_record")
    return value


def _runs(scope, rows):
    if type(scope) is not PipelineScope or type(rows) is not list or len(rows) > MAX_RUNS:
        raise InvalidRecord("invalid_record")
    result, ids, count = [], set(), 0
    for row in rows:
        _exact(row, {"id", "workspace_id", "connection_id", "pipeline_id", "observed_at", "status", "stages"})
        if any(row[k] != v for k, v in scope.as_dict().items()):
            raise InvalidRecord("scope_mismatch")
        run_id = _id(row["id"])
        if run_id in ids:
            raise InvalidRecord("duplicate_run")
        ids.add(run_id)
        stages = row["stages"]
        if stages is not None and (type(stages) is not list or len(stages) > MAX_STAGES):
            raise InvalidRecord("invalid_record")
        clean_stages = None if stages is None else []
        for stage in stages or ():
            _exact(stage, {"name", "jobs"})
            if type(stage["jobs"]) is not list:
                raise InvalidRecord("invalid_record")
            count += len(stage["jobs"])
            if count > MAX_JOBS:
                raise InvalidRecord("invalid_record")
            jobs = []
            for job in stage["jobs"]:
                _exact(job, {"id", "name", "status", "started_at", "finished_at"})
                jobs.append({"id": _id(job["id"]), "name": _label(job["name"]),
                             "status": _state(job["status"]),
                             "started_at": _time(job["started_at"], nullable=True),
                             "finished_at": _time(job["finished_at"], nullable=True)})
            clean_stages.append({"name": _label(stage["name"]), "jobs": jobs})
        result.append({**scope.as_dict(), "id": run_id,
                       "observed_at": _time(row["observed_at"]),
                       "status": _state(row["status"]), "stages": clean_stages})
    return result


def encode_snapshots(scope, snapshots):
    """Return bounded JSON for a trusted importer to place in one scoped resource.

    Statuses must be explicitly normalized before import; completed alone is not
    accepted as success. This function neither writes data nor authorizes callers.
    """
    body = {"version": 1, "scope": scope.as_dict() if type(scope) is PipelineScope else None,
            "runs": _runs(scope, snapshots)}
    text = json.dumps(body, ensure_ascii=True, separators=(",", ":"))
    if len(text.encode("ascii")) > MAX_BYTES:
        raise InvalidRecord("record_too_large")
    return text


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidRecord("duplicate_field")
        result[key] = value
    return result


def _envelope(text):
    """One bounded parse shared by exact reads and directory scope extraction."""
    try:
        if type(text) is not str or len(text) > MAX_BYTES or len(text.encode("utf-8")) > MAX_BYTES:
            raise InvalidRecord("record_too_large")
        body = json.loads(text, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(InvalidRecord("invalid_record")))
        _exact(body, {"version", "scope", "runs"})
        if type(body["version"]) is not int or body["version"] != 1:
            raise InvalidRecord("invalid_record")
        return body
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
        raise InvalidRecord("invalid_record") from None


def snapshot_scope(text):
    """Extract identifiers, NOT authority. Callers must bind and fully decode."""
    try:
        body = _envelope(text)
        _exact(body["scope"], {"workspace_id", "connection_id", "pipeline_id"})
        return PipelineScope(**body["scope"])
    except (ValueError, TypeError, KeyError):
        raise InvalidRecord("invalid_record") from None


def decode_snapshots(scope, text):
    """Reject malformed, out-of-scope or oversized data, without echoing it."""
    try:
        body = _envelope(text)
        if body["scope"] != scope.as_dict():
            raise InvalidRecord("invalid_record")
        return _runs(scope, body["runs"])
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
        raise InvalidRecord("invalid_record") from None
