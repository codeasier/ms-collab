"""A separate disposable, explicitly opted-in product-file browser fixture.

Synthetic metadata and inert text only. Never provisioned by a service runtime.
"""
from dataclasses import replace
from hashlib import sha256

from packages.application_host import ApplicationHost
from packages.application_host.tests.fixtures import setup
from packages.artifact_download import (ArtifactDownloadConfiguration, ContentBinding, DownloadPolicy,
                                       initialize_downloads)
from packages.artifact_download.tests.session_fixtures import SyntheticObjects
from packages.artifact_sync import records
from packages.artifact_sync.tests.authority_fixtures import seed_publication
from packages.artifacts import Artifact, Scope, Snapshot


SCOPE = Scope('alpha', 'source', 'bucket')
STAMP = '2026-04-02T00:00:00Z'
KEY = 'pkg/KIT_report.txt'
DATA = b'Inert synthetic product-file receipt.\n' * 4
BINDING = ContentBinding(KEY, len(DATA), STAMP, '"synthetic-report-v1"', sha256(DATA).hexdigest())


def _published():
    return Snapshot(SCOPE, STAMP, (
        Artifact(KEY, BINDING.modified_at, BINDING.size),
        Artifact('pkg/KIT_unbound.txt', STAMP, 19),  # A real published leaf, no trusted byte binding.
        Artifact('pkg/archives/KIT_notes.txt', STAMP, 13),
        Artifact('pkg/KIT_over-limit.txt', STAMP, 5 * 1024 * 1024 + 1),
        Artifact('pkg/ordinary-only.txt', STAMP, 11),
    ))


def setup_product_download(directory, *, origin, clock, authorization_endpoint):
    original, store, login, exchange = setup(directory, origin=origin, clock=clock, modules=('artifacts',),
                                            authorization_endpoint=authorization_endpoint)
    seed_publication(store, SCOPE, _published())
    initialize_downloads(store, (DownloadPolicy(SCOPE, ('admin',), (BINDING,)),))
    parent = store._transaction(lambda _, db: records.load(db, SCOPE, read_candidate=False),
                                write=False, with_connection=True)
    view = dict(format=1, kind='product-view', match='relative-key-ascii-substring-v1', scope=SCOPE.as_dict(),
                view_id='kit', product_token='kit_', discovery_label='Synthetic kit', parent=dict(grant_revision=parent.grant_revision,
                                                                 policy_digest=records.digest(parent.policy_raw)))
    manual = {key: value for key, value in view.items() if key != 'discovery_label'}
    manual['view_id'] = 'manual-kit'
    stale = {**view, 'view_id': 'obsolete-kit', 'discovery_label': 'Obsolete kit',
             'parent': {**view['parent'], 'policy_digest': '0' * 64}}
    objects = SyntheticObjects()
    objects.data = DATA
    host = ApplicationHost(config=replace(original._config, artifact_downloads=True),
                           sessions=original._boundary._sessions, clock=clock, directory_cursors=original._directory_cursors,
                           exchange=exchange, authorization_endpoint=authorization_endpoint, authorization_scopes=('synthetic_user',),
                           artifact_product_views=[view, manual, stale], artifact_downloads=ArtifactDownloadConfiguration(objects.loader, objects))
    return host, store, login, exchange, objects


def republish_product_file(store):
    """Stage and approve a new observation, keeping the original byte-bound leaf unchanged."""
    def replace_publication(policy, db):
        first = records.authorized(policy, 'admin', db, SCOPE, 'refresh')
        old = records.decode_snapshot(first.published, first.policy.connection)
        extra = Artifact(f'pkg/KIT_publication-{first.revision}.txt', STAMP, 1)
        pending = records.snapshot_text(replace(old, artifacts=(*old.artifacts, extra)))
        fingerprint = records.digest(pending)
        records.save(db, 'admin', SCOPE, first, pending, first.published, first.published_revision, 'refresh', fingerprint)
        next_record = records.authorized(policy, 'admin', db, SCOPE, 'approve')
        if next_record.candidate != pending:
            raise AssertionError('synthetic candidate changed')
        return records.save(db, 'admin', SCOPE, next_record, None, pending, next_record.revision+1, 'approve',
                            fingerprint, provenance=records.capture_publication(policy, 'admin', next_record, pending))
    return store._transaction(replace_publication, write=True, with_connection=True)
