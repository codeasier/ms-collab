# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Exercise saved product filters at the WSGI entry point with synthetic identities."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import io
import sqlite3
import tempfile
import unittest

from packages.artifact_sync import IndexedArtifactApplication, initialize_indexes, initialize_product_presets
from packages.artifact_sync.tests.authority_fixtures import configure_index_fixture, seed_publication
from packages.artifact_sync.tests.fixtures import BASE, NOW, SCOPE, Verifier, call, initialize, provider
from packages.artifact_sync.tests.test_product_view_route import CONFIGURED, descriptor, snapshot
from packages.workspaces import SQLiteWorkspaceService

VIEW = '/product-views/files'
PRESET = VIEW + '/quarters/2026Q2/presets/linux-kit'
QUERY = {'format': 1, 'quarter': '2026Q2', 'mode': 'files', 'path': 'pkg/',
         'recursive': True, 'keyword': 'linux', 'start_time': '2026-04-01T00:00:00Z'}


class ProductPresets(unittest.TestCase):
    def setUp(self):
        path = tempfile.TemporaryDirectory()
        self.addCleanup(path.cleanup)
        self.db = path.name + '/index.sqlite'
        self.store = initialize(self.db)
        self.policy = replace(CONFIGURED, readers=('admin', 'reader', 'developer'))
        initialize_indexes(self.store, (self.policy,))
        seed_publication(self.store, SCOPE, snapshot(names=['pkg/Kit_linux', 'pkg/Other_linux', 'pkg/KIT_mac']),
                         actor='approver')
        self.view = descriptor(self.store)
        initialize_product_presets(self.store)
        self.app = self.app_for()

    def app_for(self, views=None, enabled=True):
        return IndexedArtifactApplication(store=SQLiteWorkspaceService(self.db),
            providers=(provider(Verifier()),), provider_key='test', clock=lambda: NOW,
            product_views=[self.view] if views is None else views, product_presets=enabled)

    def active(self, **kw):
        out = call(self.app, VIEW, method='POST', body=QUERY)
        self.assertEqual(out['status'], 200, out)
        return dict(QUERY, if_observation=out['body']['data']['observation']) | kw

    def test_save_reopen_as_second_reader_after_factory_restart_and_new_publication(self):
        q = self.active()
        out = call(self.app, PRESET, method='POST', body=q, token='admin')
        self.assertEqual(out['status'], 200, out)
        original = out['body']['data']
        self.assertEqual(original['query'], QUERY)
        self.assertEqual(original['result']['items'][0]['key'], 'pkg/Kit_linux')
        self.assertEqual(len(original['result']['items']), 1)
        self.assertNotIn('if_observation', original['query'])
        # Real storage, new factory/connection; neither rows nor observations were persisted.
        seed_publication(self.store, SCOPE, snapshot(names=['pkg/kit_linux_new', 'pkg/not_linux']), actor='approver')
        out = call(self.app_for(), PRESET, token='reader')
        self.assertEqual(out['status'], 200, out)
        reopened = out['body']['data']
        self.assertEqual(reopened['query'], QUERY)
        self.assertEqual([row['key'] for row in reopened['result']['items']], ['pkg/kit_linux_new'])
        self.assertNotEqual(reopened['result']['observation'], original['result']['observation'])
        self.assertEqual(out['headers']['Cache-Control'], 'no-store')

    def test_cannot_save_as_non_admin_or_as_admin_without_reader_grant(self):
        for actor in ('reader', 'developer', 'approver', 'other'):
            with self.subTest(actor=actor):
                r = call(self.app, PRESET, method='POST', body=self.active(), token=actor)
                self.assertIn(r['status'], (403, 404), r)
        self.assertEqual(call(self.app, PRESET)['status'], 404)

    def test_duplicate_stale_and_invalid_query_never_replace(self):
        q = self.active()
        self.assertEqual(call(self.app, PRESET, method='POST', body=q, token='admin')['status'], 200)
        bad = [({k: v for k, v in q.items() if k != 'if_observation'}, 400),
               (q | {'if_observation': 'a'*64}, 412),
               (q | {'expected_revision': 1}, 400), (q | {'workspace_id': 'beta'}, 400),
               (q | {'quarter': '2026Q1'}, 400), (q | {'page': 1}, 400),
               (q | {'product_token': 'Other_'}, 400), (q | {'mode': 'tree'}, 400),
               (q | {'keyword': 'mac'}, 409)]
        for body, status in bad:
            with self.subTest(body=body):
                self.assertEqual(call(self.app, PRESET, method='POST', body=body, token='admin')['status'], status)
        raw = b'{"format":1,"format":1}'
        r = call(self.app, PRESET, method='POST', token='admin',
                 extra={'wsgi.input': io.BytesIO(raw), 'CONTENT_LENGTH': str(len(raw))})
        self.assertEqual(r['status'], 400)
        self.assertEqual(call(self.app, PRESET)['body']['data']['query'], QUERY)

    def test_exact_coordinates_current_auth_and_reconfigured_binding(self):
        self.assertEqual(call(self.app, PRESET, method='POST', body=self.active(), token='admin')['status'], 200)
        for app, suffix, base in (
            (self.app, PRESET.replace('2026Q2', '2026Q1'), BASE),
            (self.app, PRESET.replace('/files/', '/other/'), BASE),
            (self.app, PRESET, BASE.replace('source', 'another')),
            (self.app, PRESET, BASE.replace('alpha', 'beta')),
            (self.app_for(views=[dict(self.view, product_token='Other_')]), PRESET, BASE),
            (self.app_for(enabled=False), PRESET, BASE)):
            r = call(app, suffix, base=base)
            self.assertIn(r['status'], (403, 404), r)
            self.assertNotIn('linux', str(r['body']))
        self.assertNotIn('linux', str(call(self.app, PRESET, token='other')['body']))
        configure_index_fixture(self.store, replace(self.policy, readers=('admin',)), actor='approver')
        self.assertEqual(call(self.app, PRESET)['status'], 404)
        # Even with fresh definition under the next grant, do not expose old filters.
        r = call(self.app_for(views=[descriptor(self.store)]), PRESET, token='admin')
        self.assertEqual(r['status'], 404)

    def test_concurrent_creation_has_one_winner_and_scope_quota_is_bounded(self):
        q = self.active()
        def save(name):
            return call(self.app_for(), PRESET + name, method='POST', token='admin', body=q)['status']
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(save, ['', ''])), [200, 409])
        # Populate the known schema with 127 additional synthetic rows, without
        # changing the real authorization or quota check exercised by the final POST.
        with sqlite3.connect(self.db) as db:
            row = list(db.execute('SELECT * FROM artifact_product_presets_v1').fetchone())
            for i in range(127):
                row[6] = f'p-{i}'
                db.execute('INSERT INTO artifact_product_presets_v1 VALUES (?,?,?,?,?,?,?,?,?)', row)
        result = call(self.app, PRESET + '-overflow', method='POST', body=q, token='admin')
        self.assertEqual((result['status'], result['body']), (409, {'error': 'preset_limit'}))
        self.assertEqual(call(self.app, PRESET)['status'], 200)

    def test_same_name_in_two_readable_workspaces_is_not_a_shared_query(self):
        from packages.artifacts import Scope
        second = Scope('beta', 'source', 'bucket')
        config = replace(self.policy, connection=replace(self.policy.connection, scope=second),
                         readers=('admin',), refreshers=('admin',), approvers=('admin',))
        initialize_indexes(self.store, (config,))
        seed_publication(self.store, second, snapshot(second, ['pkg/Kit_linux_beta', 'pkg/Kit_mac']), actor='admin')
        host = self.app_for(views=[self.view, descriptor(self.store, second)])
        self.assertEqual(call(host, PRESET, method='POST', body=self.active(), token='admin')['status'], 200)
        beta = BASE.replace('alpha', 'beta')
        self.assertEqual(call(host, PRESET, base=beta, token='admin')['status'], 404)
        beta_read = call(host, VIEW, method='POST', base=beta, body=QUERY, token='admin')
        self.assertEqual(beta_read['status'], 200, beta_read)
        proof = beta_read['body']['data']['observation']
        beta_query = QUERY | {'keyword': 'mac', 'if_observation': proof}
        self.assertEqual(call(host, PRESET, method='POST', base=beta, body=beta_query, token='admin')['status'], 200)
        alpha = call(host, PRESET, token='admin')['body']['data']
        selected = call(host, PRESET, base=beta, token='admin')['body']['data']
        self.assertEqual((alpha['query']['keyword'], selected['query']['keyword']), ('linux', 'mac'))
        self.assertEqual([x['key'] for x in selected['result']['items']], ['pkg/Kit_mac'])

    def test_no_runtime_ddl_no_resource_discovery_and_no_stored_rows(self):
        self.assertEqual(call(self.app, PRESET, method='POST', body=self.active(), token='admin')['status'], 200)
        with sqlite3.connect(self.db) as db:
            value, = db.execute('SELECT query FROM artifact_product_presets_v1').fetchone()
            self.assertNotIn('Kit_linux', value)
            self.assertNotIn('observation', value)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM resources WHERE text LIKE ?', ('%linux%',)).fetchone(), (0,))
            db.execute('DROP TABLE artifact_product_presets_v1')
        self.assertEqual(call(self.app, PRESET)['status'], 503)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM sqlite_schema WHERE name='artifact_product_presets_v1'").fetchone(), (0,))
