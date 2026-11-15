"""Explicitly configured, transactional, read-only pipeline snapshot WSGI API."""
from datetime import datetime, timezone
import re
from urllib.parse import parse_qsl

from packages.pipelines import InvalidSnapshot, PipelineScope, summarize_durations, timestamp
from packages.workspace_api import WorkspaceApplication
from packages.workspaces import PolicyError
from .records import InvalidRecord, decode_snapshots, resource_id

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_STATES = frozenset({"success", "failure", "cancelled", "timed_out", "skipped", "queued", "running", "unknown"})


class PipelineApplication(WorkspaceApplication):
    """Factory-only service. No listener, default identity or provider connection.

    A reviewed verifier and current local identity bindings are required. The
    'pipelines' capability grants access to the approved snapshot resources in
    that workspace, not to external CI credentials or arbitrary live pipelines.
    """
    def __init__(self, *, store=None, providers=None, provider_key=None,
                 clock=lambda: datetime.now(timezone.utc), session_boundary=None):
        super().__init__(store=store, providers=providers, provider_key=provider_key, clock=clock, session_boundary=session_boundary)
        self._clock = clock

    def _dispatch(self, environ):
        assertion = self._verify_request(environ)
        scope, endpoint, run_id, options = self._route(environ)

        def read(policy, actor):
            # No record lookup, parsing or count calculation before current policy.
            record = policy.read_resource(actor, scope.workspace_id, "pipelines", resource_id(scope))
            try:
                rows = decode_snapshots(scope, record.text)
                now = self._clock()
                if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
                    raise InvalidRecord("invalid_clock")
                if any(timestamp(row["observed_at"]) > now.astimezone(timezone.utc) for row in rows):
                    raise InvalidRecord("future_observation")
                source = {"kind": "stored_snapshot", "revision": record.revision,
                          "full_history_complete": False, "live_provider_verified": False}
                if endpoint == "durations":
                    result = summarize_durations(scope, rows, limit=options["limit"])
                    return {**result, "source": source}
                if run_id is not None:
                    row = next((row for row in rows if row["id"] == run_id), None)
                    if row is None:
                        raise PolicyError(404, "not_found")
                    return {"scope": scope.as_dict(), "source": source, "run": row}
                ordered = sorted(rows, key=lambda r: (timestamp(r["observed_at"]), r["id"]), reverse=True)
                if options.get("status"):
                    ordered = [row for row in ordered if row["status"] == options["status"]]
                start, limit = options["offset"], options["limit"]
                items = [{key: row[key] for key in ("id", "status", "observed_at")} | {
                    "stage_count": len(row["stages"] or ()),
                    "job_count": sum(len(s["jobs"]) for s in row["stages"] or ()),
                    "details_available": row["stages"] is not None,
                } for row in ordered[start:start + limit]]
                return {"scope": scope.as_dict(), "source": source,
                        "total": len(ordered), "offset": start, "limit": limit, "items": items}
            except (InvalidRecord, InvalidSnapshot):
                # Malformed state must never become an empty successful history.
                raise PolicyError(503, "pipeline_data_unavailable") from None

        return self._authenticated_transaction(assertion, read)

    @staticmethod
    def _route(environ):
        path, query = environ.get("PATH_INFO", ""), environ.get("QUERY_STRING", "")
        if type(path) is not str or len(path) > 1000 or type(query) is not str or len(query) > 256:
            raise PolicyError(400, "invalid_request")
        parts = path.split("/")
        if (environ.get("REQUEST_METHOD") != "GET" or len(parts) not in (8, 9)
                or parts[:3] != ["", "api", "workspaces"] or parts[4] != "pipelines"
                or any(_ID.fullmatch(p) is None for p in (parts[3], parts[5], parts[6]))
                or parts[7] not in {"runs", "durations"}):
            raise PolicyError(404, "not_found")
        run_id = None
        if len(parts) == 9:
            if parts[7] != "runs" or _ID.fullmatch(parts[8]) is None or query:
                raise PolicyError(404, "not_found")
            run_id = parts[8]
        try:
            values = {}
            for key, value in parse_qsl(query, strict_parsing=True, keep_blank_values=True, max_num_fields=3):
                if key in values:
                    raise ValueError()
                values[key] = value
            allowed = {"limit"} if parts[7] == "durations" else {"offset", "limit", "status"}
            if not values.keys() <= allowed:
                raise ValueError()
            options = {"offset": 0, "limit": 30 if parts[7] == "durations" else 20}
            for key, value in values.items():
                if key == "status":
                    if value not in _STATES:
                        raise ValueError()
                    options[key] = value
                else:
                    if not re.fullmatch(r"[0-9]{1,6}", value):
                        raise ValueError()
                    options[key] = int(value)
            if not 1 <= options["limit"] <= 100 or not 0 <= options["offset"] <= 100000:
                raise ValueError()
            return PipelineScope(parts[3], parts[5], parts[6]), parts[7], run_id, options
        except (ValueError, InvalidSnapshot):
            raise PolicyError(400, "invalid_request") from None
