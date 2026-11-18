"""Current-policy checks around network reads and atomic local publication."""
import json
import math
import re
import time
from types import SimpleNamespace
from datetime import datetime, timezone

from packages.identity import AuthenticationError, Credential
from packages.pipeline_api import decode_snapshots, resource_id
from packages.pipelines import timestamp
from packages.workspace_api import WorkspaceApplication
from packages.workspaces import PolicyError
from packages.workspaces.authority import principal_pin
from . import review
from .provider import Connection, BoundCredential, ProviderError, collect, _pairs, _bound


def connection_record(connection):
    """Pure trusted-setup helper. It neither provisions nor authorizes anything."""
    if type(connection) is not Connection:
        raise ValueError("invalid connection")
    return json.dumps({"version": 1, "origin": connection.origin,
                       "pipeline_id": connection.scope.pipeline_id}, separators=(",", ":"))


def connection_resource_id(scope):
    # Each exact pipeline binding is independent, even under one connection.
    return resource_id(scope)


class PipelineSynchronizer:
    """No HTTP route or scheduler. Construct only in the trusted host factory.

    pipeline_connections must be a read-only module with trusted provisioning;
    its records must not be writable by the users allowed to run a refresh.
    """
    def __init__(self, *, store, providers, provider_key, clock=lambda: datetime.now(timezone.utc), transport=None):
        self._authority = WorkspaceApplication(store=store, providers=providers, provider_key=provider_key, clock=clock)
        self._clock, self._transport = clock, transport

    def refresh(self, *, user_credential, connection, upstream_credential, expected_revision):
        """Legacy trusted acquisition. No change to its authority or transport."""
        return self._refresh(user_credential, connection, upstream_credential, expected_revision,
                             review_policy=None, transport=self._transport)

    def reviewed_refresh(self, *, user_credential, connection, upstream_credential, expected_revision, review_policy):
        """One private terminal decision for this exact replacement, never HTTP."""
        if type(review_policy) is not review.PipelineReviewPolicy or review_policy.connection != connection:
            raise ValueError("explicit matching review policy required")
        if not self._transport or not callable(getattr(self._transport, "get", None)):
            raise ValueError("explicit review transport required")
        # collect() historically falls back to HTTPS for falsey transports.
        # Capture the chosen method in an always-truthy private adapter.
        transport = SimpleNamespace(get=self._transport.get)
        return self._refresh(user_credential, connection, upstream_credential, expected_revision,
                             review_policy=review_policy, transport=transport)

    def _refresh(self, user_credential, connection, upstream_credential, expected_revision, *, review_policy, transport):
        _bound(connection, upstream_credential)
        if type(user_credential) is not Credential:
            raise ValueError("verified user credential required")
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("invalid revision")
        assertion = self._authority._verify_request({"HTTP_AUTHORIZATION": "Bearer " + user_credential.value})
        scope = connection.scope
        baseline = {}

        def valid_observations(text):
            now = self._clock()
            if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
                raise ProviderError("invalid_clock")
            try:
                rows = decode_snapshots(scope, text)
                if any(timestamp(row["observed_at"]) > now for row in rows):
                    raise ProviderError("future_observation")
            except ValueError:
                raise ProviderError("invalid_snapshot") from None
            return rows

        def check(policy, actor, *, published_text=None):
            # This pin belongs to ONE refresh, never a durable credential or a
            # grant. Current authorization still runs in every SQLite snapshot.
            identity = principal_pin(policy, actor)
            if baseline and identity != baseline["identity"]:
                raise AuthenticationError()
            if review_policy is not None:
                member = policy._admin(actor, scope.workspace_id)
                if (identity != review_policy.reviewer_pin
                        or policy._principals[actor].authority_epoch != review_policy.reviewer_epoch
                        or member.revision != review_policy.membership_revision):
                    raise PolicyError(403, "reviewer_not_authorized")
            policy.authorize(actor, scope.workspace_id, "pipelines", "write")
            # Configuration is a separate, explicitly enabled module, never an
            # arbitrary request URL. Reject writable connection configuration.
            module = policy.get_module(actor, scope.workspace_id, "pipeline_connections")
            if module.capabilities != ("read",):
                raise PolicyError(409, "connection_unavailable")
            config = policy.read_resource(actor, scope.workspace_id, "pipeline_connections", connection_resource_id(scope))
            try:
                value = json.loads(config.text, object_pairs_hook=_pairs)
            except (ValueError, TypeError, RecursionError):
                raise PolicyError(409, "connection_unavailable") from None
            expected = {"version": 1, "origin": connection.origin, "pipeline_id": scope.pipeline_id}
            if (type(value) is not dict or value != expected or type(value.get("version")) is not int):
                raise PolicyError(409, "connection_unavailable")
            record = policy.read_resource(actor, scope.workspace_id, "pipelines", resource_id(scope))
            # Normal admission compares the old revision; only the final,
            # in-transaction persisted readback expects the newly written one.
            revision = expected_revision if published_text is None else expected_revision + 1
            if record.revision != revision:
                raise PolicyError(412, "revision_mismatch")
            # Pin only this actor/scope's authorization revisions, not the
            # global epoch: unrelated users/spaces must not cancel this flow.
            authority = (policy._workspaces[scope.workspace_id].revision,
                         policy._memberships[(scope.workspace_id, actor)].revision,
                         policy.get_module(actor, scope.workspace_id, "pipelines").revision,
                         module.revision)
            if baseline:
                expected_text = baseline["text"] if published_text is None else published_text
                if (authority != baseline["authority"] or config.revision != baseline["config"]
                        or record.text != expected_text
                        or (review_policy is not None and config.text != baseline["binding"])):
                    raise PolicyError(409, "refresh_changed")
            if review_policy is not None and published_text is None:
                # The prior bytes must be safe to inspect before the first GET.
                # A request already unable to increment this CAS/audit cannot
                # consume provider access in the reviewed path.
                policy._check_change(record.revision)
                valid_observations(record.text)
            if not baseline:
                baseline.update(identity=identity, authority=authority, config=config.revision,
                                binding=config.text, text=record.text)
            return record

        def before_read():
            self._authority._authenticated_transaction(assertion, check)

        # Initial denial prevents provider access; later checks fence subsequent
        # requests/publication but cannot atomically cancel an approved dispatch.
        before_read()
        text = collect(connection, upstream_credential, transport=transport,
                       before_read=before_read, clock=self._clock)

        def replacement(old):
            try:
                previous = decode_snapshots(scope, old.text)
                incoming = (decode_snapshots(scope, text) if review_policy is None
                            else valid_observations(text))
                if review_policy is not None and not incoming:
                    # An empty bounded observation cannot justify clearing a
                    # reader-visible publication in this reviewed workflow.
                    raise ProviderError("empty_observation")
                old_times = {row["id"]: timestamp(row["observed_at"]) for row in previous}
                # Observation ordering, not immutable terminal-run assumptions.
                for row in incoming:
                    if row["id"] in old_times and timestamp(row["observed_at"]) <= old_times[row["id"]]:
                        raise ProviderError("observation_not_newer")
            except ValueError:
                raise ProviderError("invalid_previous_snapshot") from None
            return incoming

        def publish(policy, actor):
            old = check(policy, actor)
            incoming = replacement(old)
            updated = policy.update_resource(actor, scope.workspace_id, "pipelines", resource_id(scope),
                                             text=text, expected_revision=expected_revision)
            return {"scope": scope.as_dict(), "revision": updated.revision, "run_count": len(incoming),
                    "full_history_complete": False, "live_provider_verified": False}

        if review_policy is None:
            return self._authority._authenticated_transaction(assertion, publish, write=True)

        def stage(policy, actor):
            replacement(check(policy, actor))

        # No prompt or candidate is exposed to the terminal before successful
        # current authority + complete replacement validation following collection.
        self._authority._authenticated_transaction(assertion, stage)
        started = last = None

        def tick():
            current = time.monotonic()
            if type(current) not in (float, int) or not math.isfinite(current):
                raise ProviderError("review_expired")
            return current

        def before_display():
            nonlocal started, last
            if started is not None:
                raise ProviderError("review_unavailable")
            self._authority._authenticated_transaction(assertion, stage)
            started = last = tick()

        def within_window():
            nonlocal last
            current = tick()
            if started is None or current < last or current - started >= 300:
                raise ProviderError("review_expired")
            last = current

        # No policy connection/writer lock is held during terminal I/O. The
        # trusted helper binds these immutable old/new bytes to the real
        # operator's explicit answer, or raises without publishing anything.
        digest = review.confirm_replacement(connection, review_policy, expected_revision,
                                            baseline["text"], text, verifier=before_display)
        if type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ProviderError("review_not_confirmed")
        within_window()

        persisted = None

        def publish_reviewed(policy, actor, db):
            nonlocal persisted
            within_window()  # Includes time spent waiting for the writer.
            result = publish(policy, actor)
            persisted = (db, actor, tuple(policy._audit))
            return result

        def verify_commit():
            # This is the same writer connection, after _persist, never a
            # separate read after COMMIT. Re-read the exact published text,
            # binding, reviewer/scope fences and the audit including its tail.
            db, actor, audit = persisted
            saved = self._authority._store._load(db)
            check(saved, actor, published_text=text)
            if tuple(saved._audit) != audit:
                raise PolicyError(503, "refresh_commit_changed")
            within_window()
            # WorkspaceApplication retains its own final assertion-expiry check.

        return self._authority._authenticated_transaction(assertion, publish_reviewed, write=True,
                                                         with_connection=True, before_commit=verify_commit)
