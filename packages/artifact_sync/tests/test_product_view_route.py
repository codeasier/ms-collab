# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""A factory-only product view over actual synthetic grants and publications."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from packages.artifacts import Artifact, Scope, Snapshot
from packages.artifacts_api import ArtifactApplication
from packages.artifacts_api.tests import test_application as legacy
from packages.artifact_sync import ArtifactSynchronizer, IndexedArtifactApplication, initialize_indexes, regrant_index
from packages.artifact_sync import application as indexed
from packages.artifact_sync import product_views, records
from packages.artifact_sync.tests.authority_fixtures import seed_publication
from packages.artifact_sync.tests.fixtures import (BASE, CONFIG, NOW, POLICY, SCOPE, Verifier, call,
                                                  initialize, provider, user)
from packages.workspace_api import WorkspaceApplication


FORMAT = 'relative-key-ascii-substring-v1'
ROUTE = '/product-views/files'  # Intentionally the ID of a DIFFERENT legacy preset.
STAMP = '2026-04-02T00:00:00Z'
BODY = dict(format=1, quarter='2026Q2')
CONFIGURED = replace(POLICY, connection=replace(CONFIG, bucket='kit-only-store', prefix='pkg/'),
                     readers=('reader',))


def snapshot(scope=SCOPE, names=None):
    if names is None:
        names = ['pkg/Kit_alpha', 'pkg/Kit_alpha/z.bin', 'pkg/a//KiT_one.zip',
                 'pkg/ab/KIT_two.zip', 'pkg/notes-KIT_false.txt', 'pkg/ab/Other.bin',
                 'pkg/only-side/KIT_s.SHA256', 'pkg/Kit_alpha/', 'pkg/empty-candidate/KIT_marker/',
                 'pkg/KIT_not-ascii.txt', 'pkg/kitXone.txt']
    return Snapshot(scope, STAMP, tuple(Artifact(k, STAMP, 0 if k.endswith('/') else 7,
                                                k.endswith('/')) for k in names))


def record(store, scope=SCOPE):
    return store._transaction(lambda _, db: records.load(db, scope, read_candidate=False),
                              write=False, with_connection=True)


def descriptor(store, scope=SCOPE, view_id='files', token='kit_'):
    parent = record(store, scope)
    return dict(format=1, kind='product-view', match=FORMAT, scope=scope.as_dict(), view_id=view_id,
                product_token=token, parent=dict(grant_revision=parent.grant_revision,
                                                 policy_digest=records.digest(parent.policy_raw)))


def expected_observation(view, publication):
    digest = sha256(publication.published_raw.encode('ascii')).hexdigest()
    array = ['product-observation-v1', view, publication.published_revision, digest]
    canonical = json.dumps(array, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    return sha256(canonical.encode('utf-8')).hexdigest()


def keys(data):
    return [row['key'] for row in data['items']]


@contextmanager
def trace_read():
    """Observe the real gates, never replace their decisions or their results."""
    events = []
    gate, lookup, projection = indexed.authorized, product_views.ProductViewRegistry.lookup, product_views.project
    def authorize(policy, actor, db, scope, action, **kwargs):
        events.append(('authorize', actor, scope, action))
        return gate(policy, actor, db, scope, action, **kwargs)
    def select(registry, scope, view_id):
        events.append(('lookup', scope, view_id))
        return lookup(registry, scope, view_id)
    def project(*args, **kwargs):
        events.append(('project',))
        return projection(*args, **kwargs)
    with patch.object(indexed, 'authorized', authorize), \
         patch.object(product_views.ProductViewRegistry, 'lookup', select), \
         patch.object(product_views, 'project', project):
        yield events


class IndexedProductRouteTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir/'index.sqlite'
        self.store = initialize(self.path)
        initialize_indexes(self.store, (CONFIGURED,))
        self.snap = snapshot()
        seed_publication(self.store, SCOPE, self.snap, actor='approver')
        self.view = descriptor(self.store)
        # The synthetic assertion is issued after the published Q2 metadata.
        class AprilVerifier(Verifier):
            def verify(self, credential):
                identity = super().verify(credential)
                return replace(identity, issued_at=identity.issued_at + timedelta(days=93),
                               expires_at=identity.expires_at + timedelta(days=93))
        self.now = NOW + timedelta(days=93)
        self.args = dict(store=self.store, providers=(provider(AprilVerifier()),), provider_key='test',
                         clock=lambda: self.now)
        self.app = IndexedArtifactApplication(**self.args, product_views=[self.view])

    def post(self, *, app=None, suffix=ROUTE, body=None, **kwargs):
        return call(app or self.app, suffix, method='POST', body=BODY if body is None else body, **kwargs)

    def raw(self, raw, **kwargs):
        extra = {'wsgi.input': io.BytesIO(raw), 'CONTENT_LENGTH': str(len(raw)), **kwargs.pop('extra', {})}
        return self.post(extra=extra, **kwargs)

    def ok(self, response):
        self.assertEqual(response['status'], 200, response)
        self.assertEqual(set(response['body']), {'data'})
        return response['body']['data']

    def error(self, response, status, code):
        self.assertEqual((response['status'], response['body']), (status, {'error': code}))

    def test_exact_response_and_three_page_tree_drilldown_then_files(self):
        response = self.post()
        self.assertEqual(response['headers']['Cache-Control'], 'no-store')
        root = self.ok(response)
        observation = expected_observation(self.view, record(self.store))
        self.assertEqual(root, dict(
            format=1, kind='product-view', match=FORMAT, scope=SCOPE.as_dict(), view_id='files',
            quarter='2026Q2', path='', mode='tree', observed_at=STAMP, quarter_available=True,
            catalog_revision=1, observation=observation, page=1, page_size=50, total=1,
            items=[dict(key='pkg/', name='pkg', parent='', modified_at=None, size=0,
                        virtual=True, is_dir=True)],
            source=dict(kind='stored_index', full_history_complete=False, live_provider_verified=False,
                        download_available=False)))
        for name in ['format', 'page', 'page_size', 'total', 'catalog_revision']:
            self.assertIs(type(root[name]), int)
        self.assertIs(root['quarter_available'], True)
        for name in ['full_history_complete', 'live_provider_verified', 'download_available']:
            self.assertIs(root['source'][name], False)

        def read(**changes):
            return self.ok(self.post(body={**BODY, 'if_observation': observation, **changes}))
        pages = [['pkg/Kit_alpha/', 'pkg/a/'], ['pkg/ab/', 'pkg/Kit_alpha'],
                 ['pkg/notes-KIT_false.txt'], []]
        for page, wanted in enumerate(pages, 1):
            data = read(path='pkg/', page_size=2, page=page)
            self.assertEqual((data['total'], keys(data), data['observation']), (5, wanted, observation))
        self.assertEqual(keys(read(path='pkg/a/')), ['pkg/a//'])
        self.assertEqual(keys(read(path='pkg/a//')), ['pkg/a//KiT_one.zip'])
        self.assertEqual(keys(read(path='pkg/', mode='files')), ['pkg/Kit_alpha', 'pkg/notes-KIT_false.txt'])
        self.assertEqual(keys(read(path='pkg/', mode='files', recursive=True, keyword='one.zip')),
                         ['pkg/a//KiT_one.zip'])
        self.assertEqual(keys(read(path='pkg/', mode='files', recursive=True, keyword='Other')), [])
        time = dict(start_time='2026-04-02T02:00:00+02:00', end_time=STAMP)
        self.assertEqual(keys(read(path='pkg/', mode='files', **time)), ['pkg/Kit_alpha', 'pkg/notes-KIT_false.txt'])
        self.assertEqual(keys(read(path='pkg/', mode='files', recursive=True, keyword='one.zip', **time)),
                         ['pkg/a//KiT_one.zip'])
        self.assertEqual(keys(read(path='pkg/', mode='files', recursive=True, keyword='one.zip',
                                   start_time='2026-04-02T00:00:00.000001Z')), [])
        missing = read(path='absent/')
        self.assertEqual((missing['quarter_available'], missing['total'], missing['items']), (True, 0, []))
        absent = read(quarter='2026Q3')
        self.assertEqual((absent['quarter_available'], absent['total'], absent['items']), (False, 0, []))

    def test_product_choices_only_current_explicit_labels_and_published_quarters(self):
        listing = '/product-views'
        listed = {**self.view, 'discovery_label': 'Synthetic Kit'}
        unlisted = {**self.view, 'view_id': 'manual-only'}
        stale = {**self.view, 'view_id': 'stale', 'discovery_label': 'Do not list',
                 'parent': {**self.view['parent'], 'policy_digest': '0' * 64}}
        wrong_scope = Scope('alpha', 'source', 'another-bucket')
        other = {**self.view, 'view_id': 'other', 'scope': wrong_scope.as_dict(),
                 'discovery_label': 'Other bucket'}
        app = IndexedArtifactApplication(**self.args, product_views=[other, stale, unlisted, listed])
        result = self.ok(call(app, listing))
        self.assertEqual(result, {
            'format': 1, 'kind': 'product-view-choices', 'scope': SCOPE.as_dict(),
            'catalog_revision': record(self.store).published_revision,
            'views': [{'view_id': 'files', 'label': 'Synthetic Kit'}], 'quarters': ['2026Q2'],
        })
        self.assertEqual(self.ok(self.post(app=app))['observation'],
                         self.ok(self.post())['observation'])  # Label is not the read fence.
        self.ok(self.post(app=app, suffix=listing+'/manual-only'))
        self.error(self.post(app=app, suffix=listing+'/stale'), 404, 'not_found')
        self.error(call(app, listing, token='other'), 404, 'not_found')
        self.error(call(app, listing, token='approver'), 404, 'not_found')
        self.error(call(app, listing, base=BASE.replace('/bucket', '/ungranted')), 404, 'not_found')
        self.error(call(app, listing, query='view=files'), 400, 'invalid_request')
        self.error(call(IndexedArtifactApplication(**self.args), listing), 404, 'not_found')
        self.assertEqual(self.ok(call(self.app, listing))['views'], [])
        self.assertEqual(self.ok(call(self.app, listing))['quarters'], [])
        for label in ('', ' spaced', 'line\nbreak', '\u202eto', 'x'*81, None):
            with self.subTest(label=label), self.assertRaises(ValueError):
                IndexedArtifactApplication(**self.args, product_views=[{**self.view, 'discovery_label': label}])

    def test_exact_authorization_and_lookup_order_not_just_denial(self):
        with trace_read() as trace:
            self.ok(self.post())
        self.assertEqual(trace, [('authorize', 'reader', SCOPE, 'read'), ('lookup', SCOPE, 'files'), ('project',)])
        for who in ('other', 'developer', 'approver', 'admin'):
            # Nonmember, ordinary member, approver, and admin/refresher lacking explicit reader.
            with self.subTest(who=who), trace_read() as trace:
                self.error(self.post(token=who, body={'not': 'a query'}), 404, 'not_found')
                self.assertEqual(trace, [('authorize', who, SCOPE, 'read')])
        with trace_read() as trace:
            self.error(self.post(extra={'HTTP_X_WORKSPACE_ID': 'beta'}), 400, 'invalid_request')
            self.assertEqual(trace, [])
        for scope in (Scope('beta', 'source', 'bucket'), Scope('alpha', 'other', 'bucket'),
                      Scope('alpha', 'source', 'ungranted')):
            base = '/api/workspaces/{}/artifacts/{}/{}'.format(*records.scope_key(scope))
            with self.subTest(scope=scope), trace_read() as trace:
                self.error(self.post(base=base, body={**BODY, 'if_observation': '0'*64}), 404, 'not_found')
                self.assertEqual(trace, [('authorize', 'reader', scope, 'read')])
        with trace_read() as trace:
            self.error(self.post(suffix='/product-views/missing', body={'not': 'a query'}), 404, 'not_found')
            self.assertEqual([e[0] for e in trace], ['authorize', 'lookup'])

    def test_denied_actor_does_not_decode_hidden_publication_or_read_candidate(self):
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE artifact_indexes_v1 SET candidate=?,published=?',
                       ('synthetic-pending', 'synthetic-corrupt-publication'))
        with patch.object(records, '_publication', wraps=records._publication) as decoded, trace_read() as trace:
            self.error(self.post(token='approver'), 404, 'not_found')
            decoded.assert_not_called()
            self.assertEqual([e[0] for e in trace], ['authorize'])
        with trace_read() as trace:
            self.error(self.post(), 503, 'storage_unavailable')
            self.assertEqual([e[0] for e in trace], ['authorize'])
        # Restore a valid current publication but keep the opaque candidate.
        seed_publication(self.store, SCOPE, self.snap, actor='approver')
        reads, writes = set(), []
        connect = self.store._connect
        def observe(action, table, column, database, cause):
            if action == sqlite3.SQLITE_READ and table == 'artifact_indexes_v1':
                reads.add(column)
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
                writes.append(table)
            return sqlite3.SQLITE_OK
        def traced():
            db = connect()
            db.set_authorizer(observe)
            return db
        with patch.object(self.store, '_connect', side_effect=traced):
            self.ok(self.post())
        self.assertIn('published', reads)
        self.assertNotIn('candidate', reads)
        self.assertEqual(writes, [])

    def test_invalid_stored_proof_is_not_a_successful_empty_result(self):
        envelope = json.loads(record(self.store).published_raw)
        envelope['provenance']['snapshot_digest'] = '0'*64
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE artifact_indexes_v1 SET published=?', (records.dump(envelope),))
        with trace_read() as trace:
            self.error(self.post(body={**BODY, 'quarter': '2026Q3'}), 503, 'storage_unavailable')
            self.assertEqual([e[0] for e in trace], ['authorize'])

    def test_candidate_without_any_publication_does_not_lookup_view(self):
        scope = Scope('alpha', 'pending', 'bucket')
        initialize_indexes(self.store, (replace(CONFIGURED, connection=replace(CONFIGURED.connection, scope=scope)),))
        raw = records.snapshot_text(snapshot(scope, ['pkg/KIT_pending']))
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE artifact_indexes_v1 SET candidate=?,revision=2 WHERE workspace=? AND connection=? AND bucket=?',
                       (raw, *records.scope_key(scope)))
        app = IndexedArtifactApplication(**self.args, product_views=[descriptor(self.store, scope)])
        with trace_read() as trace:
            self.error(self.post(app=app, base=BASE.replace('/source/', '/pending/'),
                                 body={'not': 'a query'}), 404, 'not_found')
            self.assertEqual(trace, [('authorize', 'reader', scope, 'read')])

    def test_candidate_change_new_approval_and_subsequent_reader_revocation(self):
        next_snap = snapshot(names=['pkg/KIT_next'])
        raw = records.snapshot_text(next_snap)
        opts = {**BODY, 'path': 'pkg/', 'mode': 'files'}
        before = self.ok(self.post(body=opts))
        self.assertEqual(keys(before), ['pkg/Kit_alpha', 'pkg/notes-KIT_false.txt'])
        proof = before['observation']
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE artifact_indexes_v1 SET revision=2,candidate=?', (raw,))
        self.assertEqual(self.ok(self.post(body={**opts, 'if_observation': proof})), before)
        no_loader = Mock(side_effect=AssertionError('no provider access'))
        sync = ArtifactSynchronizer(**self.args, credential_loader=no_loader)
        approval = sync.approve(user('approver'), SCOPE, expected_revision=2, candidate_digest=records.digest(raw))
        self.assertEqual(approval['revision'], 3)
        stale = {**opts, 'page': 2, 'if_observation': proof}
        self.error(self.post(body=stale), 412, 'stale_observation')
        after = self.ok(self.post(body=opts))
        self.assertEqual((after['catalog_revision'], keys(after)), (3, ['pkg/KIT_next']))
        self.assertNotEqual(proof, after['observation'])
        self.store.set_membership('admin', 'alpha', 'reader', role='viewer', state='revoked', expected_revision=1)
        with trace_read() as trace:
            self.error(self.post(body=stale), 404, 'not_found')
            self.assertEqual([e[0] for e in trace], ['authorize'])
        no_loader.assert_not_called()

    def test_changed_bytes_or_definition_at_same_revision_invalidates_observation(self):
        before = self.ok(self.post())
        obs = before['observation']
        stale = {**BODY, 'if_observation': obs}
        seed_publication(self.store, SCOPE, snapshot(names=['pkg/KIT_new', 'pkg/plain']), actor='approver')
        self.error(self.post(body=stale), 412, 'stale_observation')
        changed = self.ok(self.post())
        self.assertEqual(changed['catalog_revision'], before['catalog_revision'])
        self.assertEqual(changed['observation'], expected_observation(self.view, record(self.store)))
        self.assertNotEqual(changed['observation'], obs)
        new = {**self.view, 'product_token': 'plain'}
        replacement = IndexedArtifactApplication(**self.args, product_views=(new,))
        self.error(self.post(app=replacement, body={**BODY, 'if_observation': changed['observation']}),
                   412, 'stale_observation')
        data = self.ok(self.post(app=replacement, body={**BODY, 'path': 'pkg/'}))
        self.assertEqual((data['observation'], keys(data)), (expected_observation(new, record(self.store)), ['pkg/plain']))

    def test_regrant_and_foreign_realm_same_counter_need_explicit_new_parent(self):
        old = record(self.store)
        self.ok(self.post())
        regrant_index(self.store, CONFIGURED, actor_id='admin', expected_revision=old.revision,
                      expected_grant_revision=old.grant_revision)
        self.error(self.post(), 404, 'not_found')
        seed_publication(self.store, SCOPE, self.snap, actor='approver')
        with trace_read() as trace:
            self.error(self.post(body={'not': 'a query'}), 404, 'not_found')
            self.assertEqual([e[0] for e in trace], ['authorize', 'lookup'])
        self.ok(self.post(app=IndexedArtifactApplication(**self.args, product_views=[descriptor(self.store)])))
        # A different real policy realm and provider bucket, same public scope and numeric parent.
        other = initialize(self.dir/'second.sqlite')
        initialize_indexes(other, (replace(CONFIGURED, connection=replace(CONFIGURED.connection, bucket='alternate-bucket')),))
        seed_publication(other, SCOPE, self.snap, actor='approver')
        fresh = descriptor(other)
        self.assertEqual(fresh['parent']['grant_revision'], self.view['parent']['grant_revision'])
        self.assertNotEqual(fresh['parent']['policy_digest'], self.view['parent']['policy_digest'])
        args = {**self.args, 'store': other}
        self.error(self.post(app=IndexedArtifactApplication(**args, product_views=[self.view])), 404, 'not_found')
        self.ok(self.post(app=IndexedArtifactApplication(**args, product_views=[fresh])))

    def test_each_parent_pin_is_checked_before_observation_and_projection(self):
        for changed in (dict(grant_revision=2), dict(policy_digest='0'*64)):
            wrong = {**self.view, 'parent': {**self.view['parent'], **changed}}
            app = IndexedArtifactApplication(**self.args, product_views=[wrong])
            with self.subTest(field=list(changed)), trace_read() as trace:
                self.error(self.post(app=app, body={**BODY, 'if_observation': '0'*64}), 404, 'not_found')
                self.assertEqual([e[0] for e in trace], ['authorize', 'lookup'])

    def test_approval_committed_during_read_changes_only_the_next_observation(self):
        # WAL enables a second DB connection to approve while the first one is
        # projecting its own consistent read snapshot. No timing/sleep or live I/O.
        opts = {**BODY, 'path': 'pkg/', 'mode': 'files'}
        first = self.ok(self.post(body=opts))
        pending = records.snapshot_text(snapshot(names=['pkg/KIT_after']))
        with sqlite3.connect(self.path) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('UPDATE artifact_indexes_v1 SET candidate=?,revision=2', (pending,))
        loader = Mock(side_effect=AssertionError('no provider access'))
        sync = ArtifactSynchronizer(**self.args, credential_loader=loader)
        original = product_views.project
        def approve_while_reading(*args, **kwargs):
            sync.approve(user('approver'), SCOPE, expected_revision=2, candidate_digest=records.digest(pending))
            return original(*args, **kwargs)
        with patch.object(product_views, 'project', side_effect=approve_while_reading) as projected:
            self.assertEqual(self.ok(self.post(body=opts)), first)
        projected.assert_called_once()
        self.error(self.post(body={**opts, 'if_observation': first['observation']}), 412, 'stale_observation')
        after = self.ok(self.post(body=opts))
        self.assertEqual((keys(after), after['catalog_revision']), (['pkg/KIT_after'], 3))
        loader.assert_not_called()

    def test_same_view_id_distinct_visible_scopes_and_no_bucket_name_matching(self):
        second = Scope('alpha', 'other', 'bucket')
        initialize_indexes(self.store, (replace(CONFIGURED, connection=replace(CONFIGURED.connection, scope=second)),))
        # Overlapping coordinates and exact filename, but different approved metadata.
        seed_publication(self.store, second, Snapshot(second, STAMP, (
            Artifact('pkg/KIT_other', STAMP, 7), Artifact('pkg/Kit_alpha', STAMP, 19))), actor='approver')
        views = [self.view, descriptor(self.store, second)]
        for token in ('kit-only', 'bucket', 'source'):
            views.append(descriptor(self.store, view_id=token, token=token))
        app = IndexedArtifactApplication(**self.args, product_views=views)
        opts = {**BODY, 'path': 'pkg/', 'mode': 'files'}
        main = self.ok(self.post(app=app, body=opts))
        alternate = BASE.replace('/source/', '/other/')
        other = self.ok(self.post(app=app, base=alternate, body=opts))
        self.assertEqual(keys(other), ['pkg/KIT_other', 'pkg/Kit_alpha'])
        self.assertEqual([r['size'] for r in other['items'] if r['key'] == 'pkg/Kit_alpha'], [19])
        self.assertEqual([r['size'] for r in main['items'] if r['key'] == 'pkg/Kit_alpha'], [7])
        self.assertNotIn('pkg/KIT_other', keys(main))
        self.assertEqual(other['scope'], second.as_dict())
        self.error(self.post(app=app, base=alternate, body={**opts, 'if_observation': main['observation']}),
                   412, 'stale_observation')
        for token in ('kit-only', 'bucket', 'source'):
            data = self.ok(self.post(app=app, suffix='/product-views/'+token, body={**opts, 'recursive': True}))
            self.assertEqual((data['quarter_available'], data['total'], data['items']), (True, 0, []))

    def test_factory_registry_bounds_detachment_and_explicit_removal(self):
        mutable = deepcopy(self.view)
        configs = [mutable]
        app = IndexedArtifactApplication(**self.args, product_views=configs)
        mutable['product_token'] = 'Other'
        mutable['scope']['workspace_id'] = 'beta'
        mutable['parent']['grant_revision'] = 12
        configs.clear()
        proof = self.ok(self.post(app=app))['observation']
        self.assertEqual(proof, expected_observation(self.view, record(self.store)))
        maximum = [{**self.view, 'view_id': 'view'+str(i)} for i in range(128)]
        self.ok(self.post(app=IndexedArtifactApplication(**self.args, product_views=maximum),
                          suffix='/product-views/view127'))
        longest = {**self.view, 'view_id': 'v'*128, 'product_token': 'k'*64}
        bounded = IndexedArtifactApplication(**self.args, product_views=(longest,))
        self.assertEqual(self.ok(self.post(app=bounded, suffix='/product-views/'+'v'*128))['total'], 0)
        for configs in ([self.view, deepcopy(self.view)], maximum + [{**self.view, 'view_id': 'one-more'}],
                        {}, 'files', lambda: [self.view], iter([self.view])):
            with self.subTest(config_type=type(configs).__name__), self.assertRaises(ValueError):
                IndexedArtifactApplication(**self.args, product_views=configs)
        for name, value in [
            ('format', True), ('format', 2), ('kind', 'preset'), ('match', 'full-uri'), ('extension', 'x'),
            ('view_id', 'v'*129), ('view_id', 'Δ'), ('view_id', ''), ('view_id', 'two/ids'),
            ('product_token', ''), ('product_token', 'a'*65), ('product_token', ' kit'),
            ('product_token', 'x\n'), ('product_token', 'Kit'), ('product_token', 'kit*'),
            ('product_token', 'kit/'), ('product_token', '_lead'),
            ('scope', {**self.view['scope'], 'extra': True}), ('scope', {'workspace_id': 'alpha'}),
            ('scope', {**self.view['scope'], 'bucket_id': 'two/buckets'}),
            ('parent', {**self.view['parent'], 'extra': True}), ('parent', {}),
            *(('parent', {**self.view['parent'], 'grant_revision': n}) for n in (False, 0, 2**53, '1')),
            *(('parent', {**self.view['parent'], 'policy_digest': d}) for d in ('A'*64, 'a'*63, None)),
        ]:
            with self.subTest(field=name, value=value), self.assertRaises(ValueError):
                IndexedArtifactApplication(**self.args, product_views=[self.view, {**self.view, 'view_id': 'other',
                                                                                   name: value}])
        for args in ({}, {'product_views': None}, {'product_views': []}, {'product_views': ()}):
            with trace_read() as trace:
                self.error(self.post(app=IndexedArtifactApplication(**self.args, **args),
                                     body={**BODY, 'if_observation': proof}), 404, 'not_found')
                self.assertEqual(trace, [])

    def test_strict_json_framing_and_exact_media_and_route(self):
        good = b'{"quarter":"2026Q2","format":1}'
        for raw in [b'', b'[]', b'null', good[:-1], b'{"format":1,"format":1,"quarter":"2026Q2"}',
                    b'{"format":1,"quarter":"2026Q2","format":true}',
                    b'{"format":1,"quarter":"2026Q2","path":{"x":1,"x":2}}',
                    b'{"format":NaN,"quarter":"2026Q2"}', good+b'{}',
                    b'{"format":1,"quarter":"2026Q2","path":"\xff/"}',
                    b'{"format":1,"quarter":"2026Q2","path":"\\ud800/"}',
                    b'['*1050+b'0'+b']'*1050]:
            with self.subTest(length=len(raw)):
                self.error(self.raw(raw), 400, 'invalid_request')
        self.ok(self.raw(good.ljust(16384), extra={'CONTENT_TYPE': 'application/json; charset=utf-8'}))
        self.error(self.raw(good.ljust(16385)), 413, 'body_too_large')
        self.error(self.raw(good, extra={'CONTENT_TYPE': 'text/plain'}), 415, 'json_required')
        for length in ('', '+33', ' 33', '1000000', None):
            self.error(self.raw(good, extra={'CONTENT_LENGTH': length}), 411, 'length_required')
        self.error(self.raw(good, extra={'CONTENT_LENGTH': str(len(good)+1)}), 400, 'invalid_request')
        self.error(self.post(query='page=1'), 400, 'invalid_request')
        for method in ('GET', 'PUT', 'DELETE'):
            self.error(call(self.app, ROUTE, method=method, body=BODY), 404, 'not_found')
        for malformed in ('/product-views/', ROUTE+'/', ROUTE+'/more', '/product-view/files',
                          '/product-views/'+'v'*129, '/product-views/ünicode', '/product-views/id%2Fid'):
            self.error(self.post(suffix=malformed), 404, 'not_found')
        for ident in ('alpha', 'source', 'bucket'):
            self.error(self.post(base=BASE.replace(ident, 'a'*129)), 404, 'not_found')
        self.error(self.post(token=None), 401, 'authentication_required')

    def test_exact_request_fields_types_time_bounds_and_no_authority_overrides(self):
        invalid = [{}, {'format': 1}, {'quarter': '2026Q2'}]
        invalid += [{**BODY, **changes} for changes in [
            {'format': True}, {'format': 1.0}, {'format': 2}, {'mode': None}, {'mode': 'all'},
            {'quarter': '2026q2'}, {'quarter': '２０２６Q2'}, {'quarter': 20262},
            {'page': True}, {'page': 0}, {'page': 1_000_001}, {'page_size': 101}, {'page_size': 0},
            {'page_size': '1'}, {'page': 2}, {'if_observation': None}, {'if_observation': 'A'*64},
            {'if_observation': '0'*63}, {'recursive': False}, {'keyword': None}, {'start_time': STAMP},
            {'end_time': None}, {'path': None}, {'path': 'pkg'}, {'path': '/pkg/'},
            {'path': 'pkg/./'}, {'path': 'pkg/../'}, {'path': 'pkg\\x/'}, {'path': 'test://x/'},
            {'path': 'pkg/\n/'}, {'path': 'é'*512+'/'}, {'path': 'a/'*32},
            {'mode': 'files', 'keyword': ''}, {'mode': 'files', 'keyword': '\u2003'},
            {'mode': 'files', 'keyword': 'w'*257}, {'mode': 'files', 'keyword': None},
            {'mode': 'files', 'recursive': 1}, {'mode': 'files', 'start_time': None},
            {'mode': 'files', 'start_time': STAMP, 'end_time': '2026-03-01T00:00:00Z'},
            *({k: v} for k, v in [('actor', 'admin'), ('workspace_id', 'beta'), ('bucket_id', 'other'),
                                    ('product_token', 'Other'), ('definition', {}), ('files_only', True),
                                    ('conditions', []), ('sort_by', 'size'), ('order', 'desc')]),
        ]]
        for timestamp in ('2026-04-02T00:00:00', '2026-02-31T00:00:00Z', '2026-04-02T24:00:00Z',
                          '2026-04-02T00:60:00Z', '2026-04-02T00:00:60Z', '2026-04-02T00:00:00+08:60',
                          '2026-04-02T00:00:00+24:00', '2026-04-02T00:00:00.1234567Z',
                          '2026-04-02 00:00:00Z', '２０２６-04-02T00:00:00Z', STAMP+' ',
                          '0001-01-01T00:00:00+23:59', '9999-12-31T23:59:59-23:59'):
            invalid.append({**BODY, 'mode': 'files', 'start_time': timestamp})
        for body in invalid:
            with self.subTest(body=body):
                self.error(self.post(body=body), 400, 'invalid_request')
        for name in ('start_time', 'end_time'):
            duplicate = ('{"format":1,"quarter":"2026Q2","mode":"files","%s":"%s","%s":"%s"}'
                         % (name, STAMP, name, STAMP))
            self.error(self.raw(duplicate.encode('ascii')), 400, 'invalid_request')
        proof = self.ok(self.post())['observation']
        self.error(self.post(body={**BODY, 'if_observation': '0'*64}), 412, 'stale_observation')
        boundary = self.ok(self.post(body={**BODY, 'mode': 'files', 'keyword': '测'*256, 'page_size': 100,
                                          'page': 1_000_000, 'if_observation': proof}))
        self.assertEqual((boundary['page'], boundary['page_size'], boundary['items']), (1_000_000, 100, []))

    def test_no_module_read_and_unavailable_module_or_workspace_stop_before_lookup(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE modules SET can_read=0 WHERE workspace_id='alpha' AND key='artifacts'")
        with trace_read() as trace:
            self.error(self.post(), 403, 'forbidden')
            self.assertEqual([e[0] for e in trace], ['authorize'])
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE modules SET can_read=1 WHERE workspace_id='alpha' AND key='artifacts'")
        for revision, state in enumerate(('disabling', 'disabled'), 1):
            self.store.set_module_state('admin', 'alpha', 'artifacts', state=state, expected_revision=revision)
            with trace_read() as trace:
                self.error(self.post(), 409, 'module_unavailable')
                self.assertEqual([e[0] for e in trace], ['authorize'])
        self.store.set_module_state('admin', 'alpha', 'artifacts', state='enabled', expected_revision=3)
        self.store.set_workspace_state('admin', 'alpha', state='suspended', expected_revision=1)
        with trace_read() as trace:
            self.error(self.post(), 409, 'workspace_unavailable')
            self.assertEqual([e[0] for e in trace], ['authorize'])
        self.store.set_workspace_state('admin', 'alpha', state='archived', expected_revision=2)
        self.ok(self.post())

    def test_legacy_namespaces_case_sort_markers_sidecars_and_no_generic_configuration(self):
        old = self.ok(call(self.app, '/views/files', query='quarter=2026Q2'))
        self.assertEqual(old['view_revision'], 1)
        self.assertIn('pkg/only-side/KIT_s.SHA256', keys(old))
        self.assertIn('pkg/ab/Other.bin', keys(old))
        self.assertNotIn('observation', old)
        definition = {'path': 'pkg/', 'conditions': [{'keyword': 'Kit_alpha'},
                      {'operator': 'OR', 'keyword': 'KIT_'}, {'operator': 'AND NOT', 'keyword': 'false'}],
                      'sort_by': 'name', 'order': 'desc'}
        result = self.ok(self.post(suffix='/query', body={'quarter': '2026Q2', 'definition': definition,
                                                         'page': 1, 'page_size': 2}))
        # Directories sort first in the legacy full tree; the real markers retain metadata.
        self.assertEqual(keys(result), ['pkg/Kit_alpha/', 'pkg/empty-candidate/KIT_marker/'])
        self.assertIs(result['items'][0]['virtual'], False)
        self.assertEqual(result['items'][0]['modified_at'], STAMP)
        self.assertEqual(result['total'], 6)
        last = self.ok(self.post(suffix='/query', body={'quarter': '2026Q2', 'definition': definition,
                                                       'page': 3, 'page_size': 2}))
        self.assertEqual(keys(last), ['pkg/ab/KIT_two.zip', 'pkg/only-side/KIT_s.SHA256'])
        for name in ('product_token', 'match'):
            self.error(self.post(suffix='/query', body={'quarter': '2026Q2', 'definition': {name: 'kit_'}}),
                       400, 'invalid_request')
        self.error(call(self.app, '/views/files', query='quarter=2026Q2&mode=tree'), 400, 'invalid_request')
        only_product = IndexedArtifactApplication(**self.args, product_views=[{**self.view, 'view_id': 'only-product'}])
        self.error(self.post(app=only_product), 404, 'not_found')  # The legacy files preset still exists.
        self.error(call(only_product, '/views/only-product', query='quarter=2026Q2'), 404, 'not_found')
        self.ok(call(IndexedArtifactApplication(**self.args), '/views/files', query='quarter=2026Q2'))
        generic = WorkspaceApplication(**self.args)
        raw = self.ok(call(generic, '', base='/api/workspaces/alpha/modules/artifacts/resources'))
        self.assertEqual(raw['total'], 1)
        self.assertEqual(json.loads(raw['items'][0]['text'])['view_id'], 'files')
        self.assertNotIn('product_token', raw['items'][0]['text'])
        # A weak reader with both a real generic catalog and a same-ID preset still has no product route.
        weak_store = legacy.initialize(self.dir/'legacy.sqlite')
        weak = ArtifactApplication(**{**self.args, 'store': weak_store})
        self.ok(call(weak, '/views/files', query='quarter=2026Q1'))
        self.error(self.post(app=weak), 404, 'not_found')

    def test_last_page_of_real_500_file_publication_is_not_pruned(self):
        # Reverse input order; all match through the DIRECTORY token, not their basenames.
        names = [f'pkg/KIT_all/f{i:03d}' for i in reversed(range(500))]
        seed_publication(self.store, SCOPE, snapshot(names=names), actor='approver')
        opts = {**BODY, 'path': 'pkg/', 'mode': 'files', 'recursive': True, 'page_size': 100}
        first = self.ok(self.post(body=opts))
        self.assertEqual((first['total'], keys(first)), (500, sorted(names)[:100]))
        proof = first['observation']
        for page in range(2, 7):
            result = self.ok(self.post(body={**opts, 'page': page, 'if_observation': proof}))
            self.assertEqual((result['total'], keys(result)), (500, sorted(names)[(page-1)*100:page*100]))

    def test_bearer_expiry_during_projection_does_not_release_rows(self):
        original = product_views.project
        def finish(*args, **kwargs):
            result = original(*args, **kwargs)
            self.now += timedelta(minutes=5)
            return result
        with patch.object(product_views, 'project', side_effect=finish) as projected:
            self.error(self.post(), 401, 'authentication_required')
        projected.assert_called_once()
