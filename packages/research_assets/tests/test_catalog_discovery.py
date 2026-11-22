# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Published-only directory; all data stays in disposable synthetic stores."""
from contextlib import closing
import sqlite3
import unittest
from unittest.mock import patch

from packages.application_host.tests import test_host as host_fixtures
from packages.research_assets import AssetScope, provision_catalog
from packages.research_assets.tests import test_assets as fixtures


ROOT = '/api/workspaces/alpha/research-assets'


class CatalogDiscoveryTests(unittest.TestCase):
    setUp = fixtures.ServiceTests.setUp
    get = fixtures.ServiceTests.get
    sql = fixtures.ServiceTests.sql
    refresh = fixtures.ServiceTests.refresh

    def listing(self, workspace='alpha', **kwargs):
        return self.get(path=f'/api/workspaces/{workspace}/research-assets', **kwargs)

    def publish(self, workspace, catalog):
        scope = AssetScope(workspace, catalog)
        provision_catalog(self.store, fixtures.binding(scope))
        self.refresh(scope=scope)

    def snapshot(self):
        with closing(sqlite3.connect(self.path)) as db:
            return tuple(db.iterdump())

    def unavailable(self):
        before = self.snapshot()
        response = self.listing()
        self.assertEqual((response['status'], response['body']), (503, {'error': 'asset_data_unavailable'}))
        self.assertEqual(self.snapshot(), before)

    def test_only_published_complete_ids_sorted_even_when_publication_is_empty(self):
        self.assertEqual(self.listing()['body'], {'data': {'workspace_id': 'alpha', 'items': []}})
        self.publish('alpha', 'z-last')
        self.publish('commons', 'shared-example')
        self.publish('beta', 'other-example')
        self.provider.batches['primary'] = fixtures.batch(docs=[])
        self.publish('alpha', 'A-empty')
        self.publish('alpha', 'a-empty')
        # A corrupt foreign publication must never be fetched or validated.
        self.sql("UPDATE research_catalog SET evidence='{}' WHERE workspace_id='beta' AND available=1")
        before = self.snapshot()
        with patch.object(self.provider, 'prepare', side_effect=AssertionError('read called the source')):
            for workspace, ids in (('alpha', ['A-empty', 'a-empty', 'z-last']),
                                   ('commons', ['shared-example'])):
                response = self.listing(workspace)
                self.assertEqual(response['status'], 200)
                self.assertEqual(response['body'], {'data': {'workspace_id': workspace, 'items': ids}})
                self.assertEqual(response['headers']['Cache-Control'], 'no-store')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.listing('beta')['status'], 404)
        # Exact-scope manual reads retain their existing unpublished-state semantics.
        self.assertFalse(self.get()['body']['data']['available'])

    def test_current_authority_and_query_rejection_before_discovery_sql(self):
        self.refresh()
        original = self.store._connect
        queries = []

        def traced():
            db = original()
            db.set_trace_callback(queries.append)
            return db

        self.store.set_membership('admin', 'alpha', 'reader', role='viewer', state='revoked', expected_revision=1)
        self.store.set_module_state('admin', 'commons', 'research_assets', state='disabling', expected_revision=1)
        self.store.set_module_state('admin', 'commons', 'research_assets', state='disabled', expected_revision=2)
        self.sql("UPDATE modules SET can_read=0,revision=revision+1 WHERE workspace_id='beta' AND key='research_assets'")
        with patch.object(self.store, '_connect', side_effect=traced):
            for workspace, token, status in (
                ('alpha', None, 401), ('alpha', 'synthetic-disabled', 401),
                ('alpha', 'synthetic-reader', 404), ('commons', 'synthetic-other', 404),
                ('commons', 'synthetic-reader', 409), ('beta', 'synthetic-reader', 404), ('beta', 'synthetic-other', 403),
                ('missing', 'synthetic-admin', 404),
            ):
                for query in ('', 'after=bogus'):
                    with self.subTest(workspace=workspace, token=token, query=query):
                        self.assertEqual(self.listing(workspace, token=token, query=query)['status'], status)
            for query in ('revision=1', 'page=1', 'after=', 'q=one&q=two', 'junk', '%', '&', ' ', 'x'*4097, None):
                response = self.listing(token='synthetic-admin', query=query)
                self.assertEqual((response['status'], response['body']), (400, {'error': 'invalid_query'}))
        self.assertFalse(any('research_catalog' in sql or 'FROM research_asset' in sql for sql in queries))

    def test_no_directory_aliases_or_writes(self):
        before = self.snapshot()
        for method in ('HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'):
            self.assertEqual(self.listing(method=method)['status'], 404)
        for path in (ROOT+'/', ROOT+'/engineering', ROOT+'/catalogs', ROOT.replace('alpha', '%61lpha')):
            self.assertEqual(self.get(path=path)['status'], 404)
        self.assertEqual(self.snapshot(), before)

    def test_installed_unpublished_and_never_installed_are_both_empty(self):
        self.sql('DROP TABLE research_asset')
        self.unavailable()  # Partial installation is not an empty directory.
        self.sql('DROP TABLE research_catalog')
        before = self.sql('SELECT name FROM sqlite_schema ORDER BY name')
        self.assertEqual(self.listing()['body'], {'data': {'workspace_id': 'alpha', 'items': []}})
        self.assertEqual(self.sql('SELECT name FROM sqlite_schema ORDER BY name'), before)

    def test_wrong_schema_with_no_published_rows_is_not_an_empty_success(self):
        self.sql('DROP INDEX research_asset_query')
        self.unavailable()
        self.sql('CREATE INDEX research_asset_query ON research_asset(id)')
        self.unavailable()

    def test_install_layout_whitespace_equivalence_matches_runtime(self):
        self.refresh()
        self.sql('DROP INDEX research_asset_query')
        self.sql('CREATE INDEX research_asset_query\n    ON research_asset(workspace_id,catalog_id,type_id,entity_id,title,id)')
        self.assertEqual(self.listing()['body'],
                         {'data': {'workspace_id': 'alpha', 'items': ['engineering']}})

    def test_binding_evidence_and_count_are_revalidated_without_partial_output(self):
        self.publish('alpha', 'A-valid')
        self.provider.batches['primary'] = fixtures.batch(docs=[])
        self.refresh()
        for column, value in (('binding', fixtures.binding(AssetScope('beta', 'engineering')).encode()),
                              ('evidence', '{}'), ('revision', 1), ('revision', 1000000000), ('catalog_id', 'bad/id'),
                              ('catalog_id', 'a' * 129)):
            with self.subTest(column=column):
                key = ('alpha', 'engineering')
                old, = self.sql(f'SELECT {column} FROM research_catalog WHERE workspace_id=? AND catalog_id=?', key)[0]
                self.sql(f'UPDATE research_catalog SET {column}=? WHERE workspace_id=? AND catalog_id=?', (value, *key))
                self.unavailable()
                self.sql(f'UPDATE research_catalog SET {column}=? WHERE workspace_id=? AND catalog_id=?',
                         (old, 'alpha', value if column == 'catalog_id' else 'engineering'))
        self.sql("DELETE FROM research_asset WHERE workspace_id='alpha' AND catalog_id='A-valid'")
        self.unavailable()

    def test_overflow_fails_closed_and_hidden_rows_do_not_consume_limit(self):
        self.provider.batches['primary'] = fixtures.batch(docs=[])
        expected = [f'group-{i:02}' for i in range(32)]
        for name in reversed(expected):
            self.publish('alpha', name)
        self.publish('beta', 'another-publication')
        self.assertEqual(self.listing()['body'], {'data': {'workspace_id': 'alpha', 'items': expected}})
        self.refresh()  # The 33rd publication in this workspace.
        self.unavailable()


class CookieCatalogDiscoveryTests(unittest.TestCase):
    setUp = host_fixtures.HostTests.setUp
    request = host_fixtures.HostTests.request

    def test_real_host_session_scope_hint_and_logout(self):
        self.assertEqual(self.request(ROOT)['body'], {'data': {'workspace_id': 'alpha', 'items': []}})
        self.assertEqual(self.request(ROOT, cookies='')['status'], 401)
        self.assertEqual(self.request(ROOT, extra={'HTTP_AUTHORIZATION': 'Bearer invalid'})['status'], 401)
        self.assertEqual(self.request(ROOT.replace('/alpha/', '/commons/'))['status'], 404)
        self.assertEqual(self.request(ROOT, extra={'HTTP_X_WORKSPACE_ID': 'beta'})['status'], 400)
        logout = self.request('/auth/logout', method='POST', origin=host_fixtures.ORIGIN,
                              csrf=self.session.csrf.value, body={})
        self.assertEqual(logout['status'], 200)
        self.assertEqual(self.request(ROOT)['status'], 401)


if __name__ == '__main__':
    unittest.main()
