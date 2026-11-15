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
from .records import authorized, decode_snapshot, digest, save, snapshot_text, flow_pin, capture_publication, MAX_REVISION, PUBLICATION_RESERVE, _MAX_BYTES


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
        return self._refresh_verified(self._assertion(user_credential), scope, expected_revision=expected_revision)

    def _refresh_verified(self, assertion, scope, *, expected_revision, lifecycle=None):
        # Trusted adapter seam only; assertions are never accepted from HTTP fields.
        def inspect(policy, actor, connection):
            record = authorized(policy, actor, connection, scope, 'refresh')
            policy._match(expected_revision, record.revision)
            if record.revision >= MAX_REVISION:
                raise PolicyError(409,'index_revision_exhausted')
            if lifecycle is not None:
                lifecycle.guard(policy, actor, connection)
            return actor, flow_pin(policy,actor,scope), record
        actor, original_pin, original = self._authenticated_transaction(assertion, inspect, with_connection=True)

        def current(policy, current_actor, connection):
            record = authorized(policy, current_actor, connection, scope, 'refresh')
            policy._match(expected_revision, record.revision)
            if flow_pin(policy,current_actor,scope) != original_pin or record.policy_raw != original.policy_raw:
                raise PolicyError(412, 'stale_revision')
            if lifecycle is not None:
                lifecycle.guard(policy, current_actor, connection)
            return record
        def before_read():
            self._authenticated_transaction(assertion, current, with_connection=True)
        before_read()
        try:
            credential = self._credential_loader(original.policy.credential_ref, scope, original.policy.connection.origin)
        except Exception:
            raise ProviderError('credential_unavailable') from None
        try:
            observation = collect(original.policy.connection, credential, before_read=before_read,
                clock=self._clock, transport=self._transport if lifecycle is None else lifecycle.transport,
                after_read=before_read)
        except ProviderError:
            if lifecycle is not None:
                lifecycle.remaining()
            raise
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
            if len(candidate) > _MAX_BYTES - PUBLICATION_RESERVE:
                raise ValueError('candidate lacks publication budget')
        except ValueError:
            raise ProviderError('index_limit_or_invalid_result') from None
        def publish(policy, current_actor, connection):
            record = current(policy, current_actor, connection)
            revision = save(connection, actor, scope, record, candidate, record.published,
                            record.published_revision, 'refresh', digest(candidate))
            if lifecycle is not None:
                lifecycle.complete(connection, revision)
            return {'revision': revision, 'candidate_digest': digest(candidate), 'publication': 'pending',
                    'scanned': observation.scanned, 'pages': observation.pages, 'retained': len(rows)}
        return self._authenticated_transaction(assertion, publish, write=True, with_connection=True,
            before_commit=None if lifecycle is None else lifecycle.remaining)

    def review(self, user_credential, scope, *, expected_revision):
        """Approver-only complete metadata preview; never return credentials/config."""
        return self._review_verified(self._assertion(user_credential), scope, expected_revision=expected_revision)

    def _review_verified(self, assertion, scope, *, expected_revision):
        def operation(policy, actor, connection):
            record = authorized(policy, actor, connection, scope, 'review')
            policy._match(expected_revision, record.revision)
            if record.candidate is None:
                raise PolicyError(404, 'not_found')
            snapshot = decode_snapshot(record.candidate, record.policy.connection)
            return {'revision': record.revision, 'candidate_digest': digest(record.candidate),
                    'snapshot': snapshot, 'readers': record.policy.readers}
        return self._authenticated_transaction(assertion, operation, with_connection=True)

    def approve(self, user_credential, scope, *, expected_revision, candidate_digest, expected_readers=None):
        return self._approve_verified(self._assertion(user_credential), scope, expected_revision=expected_revision,
                                      candidate_digest=candidate_digest, expected_readers=expected_readers)

    def _approve_verified(self, assertion, scope, *, expected_revision, candidate_digest, expected_readers=None, require_review=False):
        if expected_readers is not None:
            if (type(expected_readers) not in (list, tuple) or not 1 <= len(expected_readers) <= 100
                    or any(type(v) is not str or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', v) for v in expected_readers)
                    or list(expected_readers) != sorted(set(expected_readers))):
                raise PolicyError(400, 'invalid_readers')
            expected_readers = tuple(expected_readers)
        if type(candidate_digest) is not str or not re.fullmatch(r'[0-9a-f]{64}', candidate_digest):
            raise PolicyError(400, 'invalid_digest')
        def operation(policy, actor, connection):
            record = authorized(policy, actor, connection, scope, 'approve')
            if require_review:
                policy.authorize(actor, scope.workspace_id, 'artifacts', 'read')
            policy._match(expected_revision, record.revision)
            if record.candidate is None:
                raise PolicyError(404, 'not_found')
            if expected_readers is not None and expected_readers != record.policy.readers:
                raise PolicyError(412, 'readers_changed')
            if not hmac.compare_digest(candidate_digest, digest(record.candidate)):
                raise PolicyError(412, 'candidate_changed')
            revision = save(connection, actor, scope, record, None, record.candidate,
                            record.revision + 1, 'approve', candidate_digest,
                            provenance=capture_publication(policy,actor,record,record.candidate))
            return {'revision': revision, 'publication': 'approved'}
        return self._authenticated_transaction(assertion, operation, write=True, with_connection=True)
