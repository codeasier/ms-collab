"""Synthetic directory cases. Not a provisioning route or production import."""
from packages.artifacts import Scope, Snapshot, Artifact
from packages.artifact_sync import Connection, IndexPolicy, initialize_indexes
from packages.artifact_sync.tests.authority_fixtures import seed_publication
from packages.workspaces import Membership


def seed_directory(store):
    def members(policy):
        for workspace in ('beta', 'commons'):
            policy._grant_membership(Membership(workspace, 'admin', 'admin'))
    store._transaction(members, write=True)
    policies = []
    for bucket in ('empty', 'unpublished', 'hidden'):
        people = ('outsider',) if bucket == 'hidden' else ('admin', 'reader')
        policies.append(IndexPolicy(Connection(Scope('alpha', 'source', bucket), 'synthetic-bucket', 'eu-west-101'),
            'synthetic-reference', people, ('second',) if bucket == 'hidden' else ('admin',),
            ('second',) if bucket == 'hidden' else ('admin',)))
    initialize_indexes(store, policies)
    seed_publication(store, Scope('alpha', 'source', 'empty'), Snapshot(Scope('alpha', 'source', 'empty'), '2026-01-01T00:00:00Z', ()))
    seed_publication(store, Scope('alpha', 'source', 'hidden'), Snapshot(Scope('alpha', 'source', 'hidden'), '2026-01-01T00:00:00Z', ()), actor='second')
    def private_candidate(policy, db):
        from packages.artifact_sync.records import snapshot_text
        scope = Scope('alpha', 'source', 'hidden')
        snapshot = Snapshot(scope, '2026-01-01T00:00:00Z', (Artifact('approver-only.txt','2026-01-01T00:00:00Z',1),))
        db.execute("UPDATE artifact_indexes_v1 SET candidate=? WHERE workspace='alpha' AND bucket='hidden'", (snapshot_text(snapshot),))
    store._transaction(private_candidate, write=True, with_connection=True)
    for workspace in ('beta', 'commons'):
        scope = Scope(workspace, 'source', 'bucket')
        seed_publication(store, scope, Snapshot(scope, '2026-01-01T00:00:00Z', (
            Artifact(workspace+'-only.txt', '2026-01-01T00:00:00Z', 1),)))


def seed_pages(store):
    scopes = [Scope(w, 'source', f'p{i:03d}') for w in ('alpha', 'beta') for i in range(26)]
    initialize_indexes(store, [IndexPolicy(Connection(s, 'synthetic-bucket', 'eu-west-101'),
        'synthetic-reference', ('admin',), ('admin',), ('admin',)) for s in scopes])
    for s in scopes:
        seed_publication(store, s, Snapshot(s, '2026-01-01T00:00:00Z', ()))
