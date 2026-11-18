# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Disposable, synthetic product publications. Never imported by the runtime."""
from dataclasses import astuple, replace
from hashlib import sha256

from packages.application_host import ApplicationHost
from packages.application_host.tests.fixtures import setup
from packages.application_ui import ApplicationUI
from packages.artifact_download import (ArtifactDownloadConfiguration, ContentBinding, DownloadPolicy,
                                       initialize_downloads)
from packages.artifact_download.tests.session_fixtures import SyntheticObjects
from packages.artifact_sync import Connection, IndexPolicy, initialize_indexes, records, regrant_index
from packages.artifact_sync.tests.authority_fixtures import seed_publication
from packages.artifacts import Artifact, Condition, Definition, SavedView, Scope, Snapshot
from packages.artifacts.product_projection import MATCH
from packages.artifacts_api import view_resource


_S1 = Scope('alpha', 'source', 'bucket')
_S2 = Scope('alpha', 'source', 'second-bucket')
_BOUNDARY = '2026-04-02T00:00:00.000000Z'
_LATER = '2026-04-10T12:00:00Z'
_OBSERVED = '2026-04-11T00:00:00Z'
_ORDINARY = 'pkg/ordinary-only.bin'
_BYTES = b'only the separately bound synthetic ordinary file'


def _published():
    # Four derived child directories, including a directory with the same
    # basename as a direct file. A repeated separator is literal key data.
    return Snapshot(_S1, _OBSERVED, (
        Artifact('pkg/<svg onload=alert(1)>/KiT_safe.zip', _LATER, 11),
        Artifact('pkg/Kit_alpha/version.bin', _BOUNDARY, 12),
        Artifact('pkg/a//KiT_one.zip', _LATER, 13),
        Artifact('pkg/ab/KIT_two.zip', '2026-04-02T00:00:00.000001Z', 14),
        Artifact('pkg/ab/KIT_three.zip', _LATER, 15),
        Artifact('pkg/Kit_alpha', _BOUNDARY, 16),
        Artifact('pkg/notes-KIT_false.txt', _LATER, 17),
        *(Artifact(f'pkg/kit_{n:02d}', _LATER, n) for n in range(10)),
        Artifact(_ORDINARY, _LATER, len(_BYTES)),
        Artifact('pkg/no/KIT_x.SHA256', _LATER, 64),
        Artifact('pkg/Kit_marker/', _LATER, 0, True),
        Artifact('pkg/sensor_only.bin', _LATER, 18),
        Artifact('pkg/KIT_prev', '2026-03-31T12:00:00Z', 19),
    ))


def _record(store, scope):
    return store._transaction(lambda _, db: records.load(db, scope, read_candidate=False),
                              with_connection=True, write=False)


def _definition(store, scope, view_id, token):
    record = _record(store, scope)
    return dict(format=1, kind='product-view', match=MATCH, scope=scope.as_dict(),
                view_id=view_id, product_token=token,
                parent=dict(grant_revision=record.grant_revision,
                            policy_digest=records.digest(record.policy_raw)))


def _stage(policy, db, snapshot):
    scope = snapshot.scope
    record = records.authorized(policy, 'admin', db, scope, 'refresh')
    raw = records.snapshot_text(snapshot)
    records.save(db, 'admin', scope, record, raw, record.published, record.published_revision,
                 'refresh', records.digest(raw))


def product_application(directory, *, origin, clock, authorization_endpoint):
    """Two exact indexed scopes; no production manifest, provider or real account."""
    original, store, login, exchange = setup(directory, origin=origin, clock=clock,
        authorization_endpoint=authorization_endpoint, modules=('artifacts',))
    published = _published()
    second = IndexPolicy(Connection(_S2, 'synthetic-bucket-2', 'eu-west-101'),
                         'synthetic-reference', ('admin', 'reader'), ('admin',), ('admin',))
    initialize_indexes(store, (second,))
    seed_publication(store, _S1, published)
    seed_publication(store, _S2, Snapshot(_S2, _OBSERVED, (
        Artifact('other/KIT_second-bucket.zip', _LATER, 20),)))

    def seed_contrasts(policy, db):
        # Same ID, different facility: only this ordinary Named view selects
        # the independently byte-bound file. Neither is a product authority.
        named = view_resource(SavedView(_S1, 'kit', 'Ordinary kit', Definition(
            files_only=True, recursive=True, conditions=(Condition('ordinary-only.bin'),))))
        db.execute('INSERT INTO resources VALUES (?,?,?,?,?)', astuple(named))
        _stage(policy, db, replace(published, artifacts=(*published.artifacts,
            Artifact('pkg/KIT_private-candidate.txt', _LATER, 21))))
    store._transaction(seed_contrasts, with_connection=True, write=True)
    objects = SyntheticObjects()
    objects.data = _BYTES
    binding = ContentBinding(_ORDINARY, len(_BYTES), _LATER, '"synthetic-v1"',
                             sha256(_BYTES).hexdigest())
    initialize_downloads(store, (DownloadPolicy(_S1, ('admin',), (binding,)),))
    definitions = (_definition(store, _S1, 'kit', 'kit_'),
                   _definition(store, _S1, 'sensor', 'sensor_'),
                   _definition(store, _S2, 'kit', 'kit_'))
    host = ApplicationHost(config=replace(original._config, artifact_downloads=True),
        sessions=original._boundary._sessions, directory_cursors=original._directory_cursors,
        clock=clock, exchange=exchange, authorization_endpoint=original._boundary._authorization_endpoint,
        authorization_scopes=('synthetic_user',), artifact_product_views=definitions,
        artifact_downloads=ArtifactDownloadConfiguration(objects.loader, objects))
    return ApplicationUI(host=host), store, login, exchange


def republish_product(policy):
    """Stage only the reviewed replacement, never publish the private candidate."""
    initial = _published()
    replacement = replace(initial, observed_at='2026-04-12T00:00:00Z',
        artifacts=(*initial.artifacts, Artifact('pkg/KIT_after-republish.txt', _LATER, 22)))

    def operation(policy, db):
        _stage(policy, db, replacement)
        record = records.authorized(policy, 'admin', db, _S1, 'approve')
        reviewed = records.snapshot_text(replacement)
        if record.candidate != reviewed:
            raise AssertionError('synthetic reviewed publication changed')
        return records.save(db, 'admin', _S1, record, None, reviewed, record.revision + 1,
                            'approve', records.digest(reviewed),
                            provenance=records.capture_publication(policy, 'admin', record, reviewed))
    return policy._transaction(operation, with_connection=True, write=True)


def revoke_product_read(policy):
    """Replace only the S1 grant. Membership, live login and S2 are unchanged."""
    record = _record(policy, _S1)
    regrant_index(policy, replace(record.policy, readers=('reader',)), actor_id='admin',
                  expected_revision=record.revision, expected_grant_revision=record.grant_revision)
