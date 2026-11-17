"""Synthetic Cookie/SQLite directory fixtures, never production provisioning.

Both admins have the same tuples in three workspaces. Publications are approved
by second, so changing that approver does not change the requesting admin's pin.
"""
from dataclasses import replace
from datetime import timedelta

from packages.application_host import ApplicationHost
from packages.application_host.tests import fixtures
from packages.application_ui import ApplicationUI
from packages.artifacts import Scope, Snapshot
from packages.artifact_sync import Connection, IndexPolicy, initialize_indexes, records
from packages.artifact_sync.tests.authority_fixtures import configure_index_fixture
from packages.pipeline_api import encode_snapshots, resource_id
from packages.pipelines import PipelineScope
from packages.workspaces import Membership, Resource

SPACES = ('alpha', 'beta', 'commons')
NAMESPACES = ('pipelines', 'artifacts', 'artifact-admin')
ACTIONS = ('pipeline.scopes.read', 'artifact.scopes.read', 'artifact.scopes.admin')
HIDDEN = Scope('alpha', 'source', 'zz-hidden')
UNPUBLISHED = Scope('alpha', 'source', 'zz-unpublished')


class DirectoryExchange(fixtures.SyntheticExchange):
    """Synthetic verifier evidence lasts beyond the independent receipt TTL."""
    def exchange_bound_code(self, **kwargs):
        verified = super().exchange_bound_code(**kwargs)
        return replace(verified, expires_at=verified.issued_at + timedelta(minutes=30))


def config(scope, *, hidden=False):
    people = ('outsider',) if hidden else ('admin', 'second')
    readers = people if hidden else ('admin', 'second', 'reader')
    return IndexPolicy(Connection(scope, 'synthetic-bucket', 'eu-west-101'),
                       'synthetic-reference', readers, people, people)


def publish_many(store, scopes, *, actor='second'):
    """One temporary-fixture transaction, with real publication/proof codecs."""
    def operation(policy, db):
        for scope in scopes:
            record = records.load(db, scope)
            snapshot = Snapshot(scope, '2026-01-01T00:00:00Z', ())
            raw = records.snapshot_text(snapshot)
            proof = records.capture_publication(policy, actor, record, raw)
            db.execute('UPDATE artifact_indexes_v1 SET published=?,published_revision=revision '
                       'WHERE workspace=? AND connection=? AND bucket=?',
                       (records.publication_text(raw, proof), *records.scope_key(scope)))
    store._transaction(operation, write=True, with_connection=True)


def application(directory, *, clock):
    original, policy, login, exchange = fixtures.setup(directory, clock=clock)
    exchange = DirectoryExchange(login.binding, clock)

    def pipelines(current):
        for workspace in ('beta', 'commons'):
            current._grant_membership(Membership(workspace, 'admin', 'admin'))
        for workspace in SPACES:
            for index in range(26):
                scope = PipelineScope(workspace, 'source', f'p{index:03d}')
                row = Resource(workspace, 'pipelines', resource_id(scope), encode_snapshots(scope, []))
                current._resources[(workspace, 'pipelines', row.id)] = row
    policy._transaction(pipelines, write=True)

    pages = [Scope(workspace, 'source', f'p{index:03d}')
             for workspace in SPACES for index in range(26)]
    initialize_indexes(policy, [*(config(scope) for scope in pages),
                                config(HIDDEN, hidden=True), config(UNPUBLISHED)])
    buckets = [Scope(workspace, 'source', 'bucket') for workspace in SPACES]
    for scope in buckets:
        configure_index_fixture(policy, config(scope), actor='second', republish=False)
    publish_many(policy, [*buckets, *pages])

    host = ApplicationHost(config=replace(original._config, artifact_administration=True),
        sessions=original._boundary._sessions, clock=clock,
        directory_cursors=original._directory_cursors, exchange=exchange,
        authorization_endpoint=fixtures.ISSUER + '/authorize',
        authorization_scopes=('synthetic_user',))
    return ApplicationUI(host=host), policy, login, exchange
