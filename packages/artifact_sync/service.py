# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Current authorization, bounded acquisition, and explicit publication."""
from datetime import datetime, timezone
import hmac
import re

from packages.artifacts import Snapshot
from packages.artifacts.catalog import instant
from packages.identity import Credential
from packages.workspace_api import WorkspaceApplication
from packages.workspaces import PolicyError
from .provider import collect, ProviderError
from .records import authorized, decode_snapshot, digest, save, snapshot_text


class ArtifactSynchronizer(WorkspaceApplication):
    """Trusted Python service only, not callable as a WSGI write endpoint."""
    def __init__(self, *, credential_loader, transport=None, clock=lambda: datetime.now(timezone.utc), **kwargs):
        super().__init__(clock=clock, **kwargs)
        if not callable(credential_loader):
            raise ValueError('explicit secret provider required')
        self._credential_loader, self._transport, self._clock = credential_loader, transport, clock

    def __call__(self, *args, **kwargs):
        raise TypeError('not an HTTP application')

    def _assertion(self, user_credential):
        if type(user_credential) is not Credential:
            raise ValueError('opaque user Credential required')
        return self._verifier.verify(self._provider_key, user_credential)

    def refresh(self, user_credential, scope, *, expected_revision):
        assertion = self._assertion(user_credential)
        def inspect(policy, actor, connection):
            record = authorized(policy, actor, connection, scope, 'refresh')
            policy._match(expected_revision, record.revision)
            return actor, record
        actor, original = self._authenticated_transaction(assertion, inspect, with_connection=True)

        def current(policy, current_actor, connection):
            record = authorized(policy, current_actor, connection, scope, 'refresh')
            policy._match(expected_revision, record.revision)
            if actor != current_actor or record.policy_raw != original.policy_raw:
                raise PolicyError(412, 'stale_revision')
            return record
        def before_read():
            self._authenticated_transaction(assertion, current, with_connection=True)
        before_read()
        try:
            credential = self._credential_loader(original.policy.credential_ref, scope, original.policy.connection.origin)
        except Exception:
            raise ProviderError('credential_unavailable') from None
        observation = collect(original.policy.connection, credential, before_read=before_read,
                              clock=self._clock, transport=self._transport)
        # Incremental union retains unseen objects; omission is not deletion proof.
        old_text = original.candidate or original.published
        old = decode_snapshot(old_text, original.policy.connection) if old_text is not None else None
        if old is not None and instant(observation.observed_at) <= instant(old.observed_at):
            raise ProviderError('stale_observation')
        rows = {} if old is None else {r.key: r for r in old.artifacts}
        for row in observation.objects:
            previous = rows.get(row.key)
            if previous is not None and (instant(row.modified_at) < instant(previous.modified_at)
                    or (instant(row.modified_at) == instant(previous.modified_at) and row != previous)):
                raise ProviderError('moving_or_invalid_listing')
            rows[row.key] = row
        try:
            candidate = snapshot_text(Snapshot(scope, observation.observed_at,
                tuple(rows[k] for k in sorted(rows)), original.policy.connection.offset_minutes))
        except ValueError:
            raise ProviderError('index_limit_or_invalid_result') from None
        def publish(policy, current_actor, connection):
            record = current(policy, current_actor, connection)
            revision = save(connection, actor, scope, record, candidate, record.published,
                            record.published_revision, 'refresh', digest(candidate))
            return {'revision': revision, 'candidate_digest': digest(candidate), 'publication': 'pending',
                    'scanned': observation.scanned, 'pages': observation.pages, 'retained': len(rows)}
        return self._authenticated_transaction(assertion, publish, write=True, with_connection=True)

    def review(self, user_credential, scope, *, expected_revision):
        """Approver-only complete metadata preview; never return credentials/config."""
        assertion = self._assertion(user_credential)
        def operation(policy, actor, connection):
            record = authorized(policy, actor, connection, scope, 'review')
            policy._match(expected_revision, record.revision)
            if record.candidate is None:
                raise PolicyError(404, 'not_found')
            snapshot = decode_snapshot(record.candidate, record.policy.connection)
            return {'revision': record.revision, 'candidate_digest': digest(record.candidate),
                    'snapshot': snapshot, 'readers': record.policy.readers}
        return self._authenticated_transaction(assertion, operation, with_connection=True)

    def approve(self, user_credential, scope, *, expected_revision, candidate_digest):
        assertion = self._assertion(user_credential)
        if type(candidate_digest) is not str or not re.fullmatch(r'[0-9a-f]{64}', candidate_digest):
            raise PolicyError(400, 'invalid_digest')
        def operation(policy, actor, connection):
            record = authorized(policy, actor, connection, scope, 'approve')
            policy._match(expected_revision, record.revision)
            if record.candidate is None:
                raise PolicyError(404, 'not_found')
            if not hmac.compare_digest(candidate_digest, digest(record.candidate)):
                raise PolicyError(412, 'candidate_changed')
            revision = save(connection, actor, scope, record, None, record.candidate,
                            record.revision + 1, 'approve', candidate_digest)
            return {'revision': revision, 'publication': 'approved'}
        return self._authenticated_transaction(assertion, operation, write=True, with_connection=True)
