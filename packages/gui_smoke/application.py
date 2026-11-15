# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Only current-authorized saved task queries. No runner/provider transport."""
from datetime import datetime, timezone
import re
from urllib.parse import parse_qsl

from packages.workspace_api import WorkspaceApplication
from packages.workspace_api.application import _object
from packages.workspaces import PolicyError
from .model import MAX_REVISION, SmokeScope, SOURCES, STATES, project_task, timestamp
from .store import SmokeConnection, _now, read_snapshot

_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class GuiSmokeApplication(WorkspaceApplication):
    def __init__(self, *, store=None, providers=None, provider_key=None, connections,
                 clock=lambda: datetime.now(timezone.utc), session_boundary=None):
        super().__init__(store=store, providers=providers, provider_key=provider_key, clock=clock,
                         session_boundary=session_boundary)
        if type(connections) is not tuple or len(connections) > 100 or any(type(c) is not SmokeConnection for c in connections):
            raise ValueError("explicit_connections_required")
        self._connections = {c.scope: c for c in connections}
        if len(self._connections) != len(connections):
            raise ValueError("duplicate_connection")
        self._clock = clock

    def _dispatch(self, environ):
        assertion = self._verify_request(environ)
        scope, endpoint, identity, options = self._route(environ)
        configured = self._connections.get(scope)
        if configured is None:
            raise PolicyError(404, "not_found")
        def read(policy, actor, connection):
            record = read_snapshot(policy, connection, actor, configured, _now(self._clock))
            revision = record["revision"] if record else 0
            if options.get("offset", 0) and "revision" not in options:
                raise PolicyError(428, "precondition_required")
            if "revision" in options and options["revision"] != revision:
                raise PolicyError(412, "stale_revision")
            source = {"kind": "stored_observation", "revision": revision,
                      "observed_at": record["observed_at"] if record else None,
                      "live_provider_verified": False, "full_history_complete": False,
                      "execution_enabled": False}
            result = {"scope": scope.as_dict(), "available": record is not None, "source": source,
                      "capabilities": {"read": True, "submit": False, "cancel": False, "retry": False,
                                       "reproduce": False, "refresh": False, "download": False}}
            tasks = record["tasks"] if record else []
            if identity is not None:
                task = next((row for row in tasks if row["id"] == identity), None)
                if task is None:
                    raise PolicyError(404, "not_found")
                return dict(result, task=project_task(task, record["observed_at"]))
            if endpoint == "summary":
                counts = {state: sum(row["status"] == state for row in tasks) for state in sorted(STATES)}
                return dict(result, total=len(tasks), states=counts,
                            interpretation="published_snapshot_only;not_live_queue_or_execution_proof")
            ordered = sorted(tasks, key=lambda row: (timestamp(row["created_at"]), row["id"]), reverse=True)
            for key in ("status", "source", "branch"):
                if key in options:
                    ordered = [row for row in ordered if row[key] == options[key]]
            offset, limit = options["offset"], options["limit"]
            items = [{k: row[k] for k in ("id", "status", "source", "branch", "created_at")} | {
                "details_available": True, "stages_available": row["stages"] is not None,
                "summary_available": row["summary"] is not None,
            } for row in ordered[offset:offset + limit]]
            return dict(result, total=len(ordered), offset=offset, limit=limit, items=items,
                        next_offset=offset + len(items) if offset + len(items) < len(ordered) else None)
        return self._authenticated_transaction(assertion, read, with_connection=True)

    @staticmethod
    def _route(environ):
        path, query = environ.get("PATH_INFO", ""), environ.get("QUERY_STRING", "")
        if type(path) is not str or len(path) > 1000 or type(query) is not str or len(query) > 1024:
            raise PolicyError(400, "invalid_request")
        parts = path.split("/")
        if (environ.get("REQUEST_METHOD") != "GET" or len(parts) not in (8, 9)
                or parts[:3] != ["", "api", "workspaces"] or parts[4] != "gui-smoke"
                or parts[7] not in {"tasks", "summary"}
                or any(not _SEGMENT.fullmatch(p) for p in parts[3:])):
            raise PolicyError(404, "not_found")
        if len(parts) == 9 and parts[7] != "tasks":
            raise PolicyError(404, "not_found")
        try:
            values = _object(parse_qsl(query, keep_blank_values=True, strict_parsing=True, max_num_fields=6))
            listing = len(parts) == 8 and parts[7] == "tasks"
            allowed = {"offset", "limit", "status", "source", "branch", "revision"} if listing else {"revision"}
            if set(values) - allowed:
                raise ValueError()
            options = {"offset": 0, "limit": 20}
            for key, value in values.items():
                if key in {"offset", "limit", "revision"}:
                    if not re.fullmatch(r"[0-9]{1,16}", value):
                        raise ValueError()
                    options[key] = int(value)
                elif key in {"status", "source"}:
                    if value not in (STATES if key == "status" else SOURCES):
                        raise ValueError()
                    options[key] = value
                else:
                    if not value.strip() or len(value.encode("utf-8")) > 400 or any(ord(c) < 32 for c in value):
                        raise ValueError()
                    options[key] = value
            if (not 0 <= options["offset"] <= 500 or not 1 <= options["limit"] <= 100
                    or ("revision" in options and not 0 <= options["revision"] <= MAX_REVISION)):
                raise ValueError()
            return SmokeScope(parts[3], parts[5], parts[6]), parts[7], parts[8] if len(parts) == 9 else None, options
        except (ValueError, TypeError, UnicodeError):
            raise PolicyError(400, "invalid_request") from None
