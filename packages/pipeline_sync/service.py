"""Current-policy checks around network reads and atomic local publication."""
import json
from datetime import datetime, timezone

from packages.identity import AuthenticationError, Credential
from packages.pipeline_api import decode_snapshots, resource_id
from packages.pipelines import timestamp
from packages.workspace_api import WorkspaceApplication
from packages.workspaces import PolicyError
from packages.workspaces.authority import principal_pin
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
        _bound(connection, upstream_credential)
        if type(user_credential) is not Credential:
            raise ValueError("verified user credential required")
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("invalid revision")
        assertion = self._authority._verify_request({"HTTP_AUTHORIZATION": "Bearer " + user_credential.value})
        scope = connection.scope
        baseline = {}

        def check(policy, actor):
            # This pin belongs to ONE refresh, never a durable credential or a
            # grant. Current authorization still runs in every SQLite snapshot.
            identity = principal_pin(policy, actor)
            if baseline and identity != baseline["identity"]:
                raise AuthenticationError()
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
            if record.revision != expected_revision:
                raise PolicyError(412, "revision_mismatch")
            # Pin only this actor/scope's authorization revisions, not the
            # global epoch: unrelated users/spaces must not cancel this flow.
            authority = (policy._workspaces[scope.workspace_id].revision,
                         policy._memberships[(scope.workspace_id, actor)].revision,
                         policy.get_module(actor, scope.workspace_id, "pipelines").revision,
                         module.revision)
            if baseline and (authority != baseline["authority"]
                             or config.revision != baseline["config"] or record.text != baseline["text"]):
                raise PolicyError(409, "refresh_changed")
            if not baseline:
                baseline.update(identity=identity, authority=authority, config=config.revision, text=record.text)
            return record

        def before_read():
            self._authority._authenticated_transaction(assertion, check)

        # Initial denial prevents provider access; later checks fence subsequent
        # requests/publication but cannot atomically cancel an approved dispatch.
        before_read()
        text = collect(connection, upstream_credential, transport=self._transport,
                       before_read=before_read, clock=self._clock)

        def publish(policy, actor):
            old = check(policy, actor)
            try:
                previous = decode_snapshots(scope, old.text)
                incoming = decode_snapshots(scope, text)
                old_times = {row["id"]: timestamp(row["observed_at"]) for row in previous}
                # Observation ordering, not immutable terminal-run assumptions.
                for row in incoming:
                    if row["id"] in old_times and timestamp(row["observed_at"]) <= old_times[row["id"]]:
                        raise ProviderError("observation_not_newer")
            except ValueError:
                raise ProviderError("invalid_previous_snapshot") from None
            updated = policy.update_resource(actor, scope.workspace_id, "pipelines", resource_id(scope),
                                             text=text, expected_revision=expected_revision)
            return {"scope": scope.as_dict(), "revision": updated.revision, "run_count": len(incoming),
                    "full_history_complete": False, "live_provider_verified": False}

        return self._authority._authenticated_transaction(assertion, publish, write=True)
