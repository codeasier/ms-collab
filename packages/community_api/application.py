# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Current-user authorization, scoped sharing and two-phase refresh orchestration."""
from datetime import datetime, timezone
from dataclasses import replace
import json
import hashlib
import re
from urllib.parse import parse_qsl

from packages.community_board import BoardProjection
from packages.community_board.model import Scope, Snapshot, utc_time
from packages.community_board.delta import compare_snapshots
from packages.community_sync import BoundCredential, GitCodeProvider, ProviderError
from packages.workspace_api import WorkspaceApplication
from packages.workspace_api.application import _object
from packages.workspaces import PolicyError, StorageError
from packages.workspaces.authority import authority_current, principal_pin
from packages.identity import AuthenticationError, AuthenticationUnavailable
from .records import read, encode, snapshot_data, fingerprint, _HASH, approval_text, capture_approval, approval_current, _MAX_REVISION

_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class CommunityApplication(WorkspaceApplication):
    def __init__(self, *, credential_loader=None, provider=None,
                 clock=lambda: datetime.now(timezone.utc), **kwargs):
        super().__init__(clock=clock, **kwargs)
        if credential_loader is not None and not callable(credential_loader):
            raise ValueError("trusted credential loader required")
        self._credential_loader = credential_loader
        self._provider = provider if provider is not None else GitCodeProvider()
        self._clock = clock
        self._refresher = None

    @staticmethod
    def _state(policy, actor, connection, scope, action):
        policy.authorize(actor, scope.workspace_id, "community", "read" if action in {"items", "review"} else "write")
        state = read(connection, scope)
        if state is None:
            raise PolicyError(404, "not_found")
        allowed = {"items":state.policy.reader_ids, "review":state.policy.approver_ids,
                   "approve":state.policy.approver_ids, "refresh":state.policy.refresher_ids}[action]
        if actor not in allowed or not authority_current(policy, actor, state.authority):
            raise PolicyError(404, "not_found")
        if action != "items":
            policy._admin(actor, scope.workspace_id)
        if not approval_current(policy,state):
            state=replace(state,approvals=frozenset(),provenance=None)
        return state

    def _dispatch(self, environ):
        assertion = self._verify_request(environ)
        scope, action, filters = self._route(environ)
        if action == "capabilities":
            def capabilities(policy, actor, connection):
                granted, current = {}, None
                for name in ("items", "review", "refresh", "approve"):
                    try:
                        state = self._state(policy, actor, connection, scope, name)
                        granted[name] = name != "refresh" or self._credential_loader is not None
                        current = state
                    except PolicyError:
                        granted[name] = False
                if current is None or not any(granted.values()):
                    raise PolicyError(404, "not_found")
                return {"scope":scope.as_dict(), "revision":current.revision, "capabilities":granted}
            return self._authenticated_transaction(assertion, capabilities, with_connection=True)
        if action == "refresh":
            expected = self._revision(environ)
            self._empty_body(environ)
            if self._refresher is not None:
                return self._refresher.refresh(assertion, scope, expected)
            return self._refresh(assertion, scope, expected, None)
        if action == "approve":
            expected = self._revision(environ)
            approved = self._approvals_body(environ)
            def approve(policy, actor, connection):
                state = self._state(policy, actor, connection, scope, action)
                policy._match(expected, state.revision)
                available = {fingerprint(scope, item) for item in state.snapshot.items} if state.snapshot else set()
                if not approved <= available:
                    raise PolicyError(400, "invalid_approval")
                proof=capture_approval(policy,state,actor) if approved else None
                self._save(policy, actor, connection, state, state.snapshot, approved, proof)
                return {"scope":scope.as_dict(), "revision":state.revision + 1, "approved_count":len(approved)}
            return self._authenticated_transaction(assertion, approve, write=True, with_connection=True)
        def query(policy, actor, connection):
            state = self._state(policy, actor, connection, scope, action)
            if state.snapshot is None:
                return {"scope":scope.as_dict(), "revision":state.revision, "available":False, "items":[]}
            items = state.snapshot.items if action == "review" else tuple(item for item in state.snapshot.items if fingerprint(scope, item) in state.approvals)
            visible = Snapshot(scope, state.snapshot.observed_at, items)
            result = BoardProjection((visible,)).list_items(scope.workspace_id, **filters)
            result.update(scope=scope.as_dict(), revision=state.revision, available=True,
                          sharing="explicit_content_approval", view=action)
            if action == "review":
                by_key = {item.key:item for item in items}
                for item in result["items"]:
                    digest = fingerprint(scope, by_key[(item["kind"], item["number"])])
                    item["fingerprint"] = digest
                    item["approved"] = digest in state.approvals
            return result
        return self._authenticated_transaction(assertion, query, with_connection=True)

    def _refresh(self, assertion, scope, expected, _, *, lifecycle=None):
        def flow_pin(policy, actor):
            return (principal_pin(policy,actor),
                    policy._memberships[(scope.workspace_id,actor)].revision,
                    policy._workspaces[scope.workspace_id].revision,
                    policy._modules[(scope.workspace_id,"community")].revision)
        def preflight(policy, actor, connection):
            state = self._state(policy, actor, connection, scope, "refresh")
            policy._match(expected, state.revision)
            if state.revision >= _MAX_REVISION:
                raise PolicyError(409, "connection_revision_exhausted")
            if lifecycle is not None:
                lifecycle.guard(policy, actor, connection)
            return flow_pin(policy,actor), state
        actor_pin, prior = self._authenticated_transaction(assertion, preflight, with_connection=True)
        def current(policy, actor, connection):
            state=self._state(policy,actor,connection,scope,"refresh")
            if flow_pin(policy,actor)!=actor_pin:raise PolicyError(409,"refresh_authority_changed")
            policy._match(prior.revision,state.revision)
            if state.config_raw!=prior.config_raw:raise PolicyError(409,"connection_changed")
            if lifecycle is not None:
                lifecycle.guard(policy, actor, connection)
            return state
        def before_read():
            self._authenticated_transaction(assertion,current,with_connection=True)
        if self._credential_loader is None:
            raise PolicyError(503, "refresh_unconfigured")
        before_read()
        try:
            credential = self._credential_loader(scope, prior.policy.credential_ref)
            if type(credential) is not BoundCredential or credential.scope != scope:
                raise ProviderError("credential_scope_mismatch")
            cutoff = self._clock()
            if cutoff.tzinfo is None or cutoff.utcoffset() is None:
                raise ValueError("aware_clock_required")
            observed_at = cutoff.astimezone(timezone.utc).isoformat()
            before_read()
            snapshot = (self._provider if lifecycle is None else lifecycle.provider).observe(prior.policy.connection, credential, observed_at=observed_at, before_read=before_read)
            before_read()
            if type(snapshot) is not Snapshot or snapshot.scope != scope:
                raise ProviderError("snapshot_scope_mismatch")
            # Enforce the dedicated record bound before opening a write lock.
            encode(snapshot_data(snapshot))
        except (PolicyError,AuthenticationError,AuthenticationUnavailable,StorageError):
            raise
        except Exception:
            if lifecycle is not None:
                lifecycle.remaining()
            raise PolicyError(502, "provider_unavailable") from None
        def publish(policy, current_actor, connection):
            state = current(policy, current_actor, connection)
            if state.snapshot is not None:
                try:
                    compare_snapshots(state.snapshot, snapshot)
                    before = {item.key:item for item in state.snapshot.items}
                    for item in snapshot.items:
                        old = before.get(item.key)
                        if old and utc_time(item.updated_at) == utc_time(old.updated_at) and item != old:
                            raise ValueError()
                    if snapshot != state.snapshot and utc_time(snapshot.observed_at) == utc_time(state.snapshot.observed_at):
                        raise ValueError()
                except (ValueError, OverflowError):
                    raise PolicyError(409, "stale_observation") from None
            approvals = state.approvals & {fingerprint(scope, item) for item in snapshot.items}
            self._save(policy, current_actor, connection, state, snapshot, approvals, state.provenance if approvals else None)
            if lifecycle is not None:
                saved = replace(state, snapshot=snapshot, approvals=frozenset(approvals),
                                provenance=state.provenance if approvals else None, revision=state.revision + 1)
                lifecycle.complete(policy, connection, saved)
            return {"scope":scope.as_dict(), "revision":state.revision + 1,
                    "observed_at":snapshot.observed_at, "approved_count":len(approvals)}
        return self._authenticated_transaction(assertion, publish, write=True, with_connection=True,
            before_commit=None if lifecycle is None else lifecycle.verify_commit)

    @staticmethod
    def _save(policy, actor, connection, state, snapshot, approvals, provenance):
        if state.revision >= _MAX_REVISION:
            raise PolicyError(409, "connection_revision_exhausted")
        scope = state.policy.scope
        changed = connection.execute("UPDATE community_connections SET snapshot=?,approvals=?,revision=revision+1 WHERE workspace_id=? AND connection_id=? AND repository=? AND revision=?",
                           (encode(snapshot_data(snapshot)) if snapshot else None, approval_text(approvals,provenance),
                            scope.workspace_id, scope.connection_id, scope.repository, state.revision)).rowcount
        if changed != 1:
            raise PolicyError(412, "stale_revision")
        # Audit metadata only. The core persists this in the SAME transaction.
        policy._changed(actor, scope.workspace_id, "resource.update",
                        "community:" + hashlib.sha256(encode(scope.as_dict()).encode("ascii")).hexdigest(), state.revision + 1)

    @staticmethod
    def _route(environ):
        path, verb, query = environ.get("PATH_INFO", ""), environ.get("REQUEST_METHOD", ""), environ.get("QUERY_STRING", "")
        if type(path) is not str or len(path) > 1000 or type(query) is not str or len(query) > 500:
            raise PolicyError(400, "invalid_request")
        parts = path.split("/")
        if (len(parts) != 9 or parts[:3] != ["", "api", "workspaces"] or parts[4] != "community"
                or any(not _SEGMENT.fullmatch(p) for p in parts[3:])):
            raise PolicyError(404, "not_found")
        scope = Scope(parts[3], parts[5], parts[6] + "/" + parts[7])
        action = parts[8]
        if action in {"refresh", "approve"} and verb == "POST" and not query:
            return scope, action, {}
        if action == "capabilities" and verb == "GET" and not query:
            return scope, action, {}
        if action not in {"items", "review"} or verb != "GET":
            raise PolicyError(404, "not_found")
        try:
            values = _object(parse_qsl(query, keep_blank_values=True, strict_parsing=True, max_num_fields=5))
            if set(values) - {"kind", "state", "query", "limit", "offset"}:
                raise ValueError()
            for key in ("limit", "offset"):
                if key in values:
                    if not re.fullmatch(r"[0-9]{1,7}", values[key]):
                        raise ValueError()
                    values[key] = int(values[key])
            # Validate filters even before the first snapshot exists.
            BoardProjection((Snapshot(scope, "2000-01-01T00:00:00Z", ()),)).list_items(scope.workspace_id, **values)
        except (ValueError, TypeError):
            raise PolicyError(400, "invalid_request") from None
        return scope, action, values

    @classmethod
    def _empty_body(cls, environ):
        cls._body(environ, set())

    @classmethod
    def _approvals_body(cls, environ):
        # Reuse bounded JSON framing but validate a list (base helper allows strings).
        if environ.get("CONTENT_TYPE", "").lower() not in {"application/json", "application/json; charset=utf-8"}:
            raise PolicyError(415, "json_required")
        length = environ.get("CONTENT_LENGTH", "")
        if type(length) is not str or not re.fullmatch(r"[0-9]{1,6}", length):
            raise PolicyError(411, "length_required")
        if int(length) > 16384:
            raise PolicyError(413, "body_too_large")
        try:
            raw = environ["wsgi.input"].read(int(length))
            if type(raw) is not bytes or len(raw) != int(length):
                raise ValueError()
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
            if type(data) is not dict or set(data) != {"fingerprints"} or type(data["fingerprints"]) is not list:
                raise ValueError()
            values = data["fingerprints"]
            if len(values) > 200 or any(type(value) is not str or not _HASH.fullmatch(value) for value in values) or len(set(values)) != len(values):
                raise ValueError()
            return frozenset(values)
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise PolicyError(400, "invalid_request") from None
