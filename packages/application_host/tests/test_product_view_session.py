# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Product metadata over the actual Cookie boundary and a distinct mounted byte grant."""
from hashlib import sha256
import tempfile
import unittest
from unittest.mock import Mock, patch

from packages.application_host import ApplicationHost, HostConfig
from packages.application_host.tests.fixtures import NOW, ORIGIN, session, setup
from packages.artifact_download import (ArtifactDownloadConfiguration, ContentBinding, DownloadPolicy,
                                       initialize_downloads)
from packages.artifact_sync import initialize_product_presets, product_views, records
from packages.artifact_sync.tests.authority_fixtures import seed_publication
from packages.artifacts import Artifact, Scope, Snapshot
from packages.session_api.application import _CSRF, _SESSION
from packages.session_api.tests.test_application import call, cookie_header


SCOPE = Scope('alpha', 'source', 'bucket')
ROUTE = '/api/workspaces/alpha/artifacts/source/bucket/product-views/kit'
AVAILABILITY = '/api/workspaces/alpha/artifact-download/source/bucket/availability'
KEY = 'pkg/nested/KIT_one'
STAMP = '2026-01-01T00:00:00Z'
BODY = dict(format=1, quarter='2026Q1')


class ProductViewSessionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.now = NOW
        initial, self.store, self.login, self.exchange = setup(
            tmp.name, clock=lambda: self.now, modules=('artifacts',), directory_cursors=False)
        self.sessions = initial._boundary._sessions
        # One existing metadata reader lacks byte authority; admin has both.
        sample = b'synthetic'
        self.binding = ContentBinding(KEY, len(sample), STAMP, '"v1"', sha256(sample).hexdigest())
        initialize_downloads(self.store, (DownloadPolicy(SCOPE, ('admin',), (self.binding,)),))
        seed_publication(self.store, SCOPE, Snapshot(SCOPE, STAMP, (
            Artifact(KEY, self.binding.modified_at, len(sample)), Artifact('pkg/plain', STAMP, 1))))
        record = self.store._transaction(lambda _, db: records.load(db, SCOPE),
                                         write=False, with_connection=True)
        self.view = dict(format=1, kind='product-view', match='relative-key-ascii-substring-v1',
                         scope=SCOPE.as_dict(), view_id='kit', product_token='kit_',
                         parent=dict(grant_revision=record.grant_revision,
                                     policy_digest=records.digest(record.policy_raw)))
        self.loader = Mock(side_effect=AssertionError('not a byte request'))
        self.objects = Mock(spec=['open'])
        self.objects.open.side_effect = AssertionError('no object reads')
        self.host = ApplicationHost(config=HostConfig(ORIGIN, ('artifacts',), artifact_downloads=True),
                                    sessions=self.sessions, clock=lambda: self.now,
                                    artifact_downloads=ArtifactDownloadConfiguration(self.loader, self.objects),
                                    artifact_product_views=[self.view])
        self.reader = session(self.login, self.exchange, 'reader-sub')

    def post(self, path=ROUTE, *, app=None, who=None, **updates):
        who = who or self.reader
        cookies = cookie_header({_SESSION: who.token.value, _CSRF: who.csrf.value})
        options = dict(method='POST', body=BODY, origin=ORIGIN, csrf=who.csrf.value, cookies=cookies)
        return call(app or self.host, path, **{**options, **updates})

    def test_cookie_drilldown_and_refinements_are_real_metadata_not_byte_authority(self):
        root = self.post()
        self.assertEqual(root['status'], 200, root)
        data = root['body']['data']
        self.assertEqual((data['total'], [v['key'] for v in data['items']]), (1, ['pkg/']))
        headers = dict(root['headers'])
        self.assertEqual((headers['Vary'], headers['Cache-Control']), ('Cookie, Origin', 'no-store'))
        self.assertNotIn('WWW-Authenticate', headers)
        proof = data['observation']
        for query, wanted in [(dict(path='pkg/'), ['pkg/nested/']),
                              (dict(path='pkg/', mode='files'), []),
                              (dict(path='pkg/', mode='files', recursive=True, keyword='one'), [KEY])]:
            result = self.post(body={**BODY, 'if_observation': proof, **query})
            self.assertEqual(result['status'], 200, result)
            projected = result['body']['data']
            self.assertEqual((projected['observation'], [v['key'] for v in projected['items']]), (proof, wanted))
            self.assertIs(projected['source']['download_available'], False)
        leaf = projected['items'][0]
        self.assertEqual(leaf, dict(key=KEY, name='KIT_one', parent='pkg/nested/',
                                    modified_at=self.binding.modified_at, size=9, is_dir=False, virtual=False))
        options = dict(body={'key': leaf['key']}, revision='"1"')
        denied = self.post(AVAILABILITY, **options)
        self.assertEqual((denied['status'], denied['body']), (404, {'error': 'not_found'}))
        # Positive control on the same route, source, publication, and exact leaf.
        admin = session(self.login, self.exchange, 'admin-sub')
        allowed = self.post(AVAILABILITY, who=admin, **options)
        self.assertEqual(allowed['status'], 200, allowed)
        self.assertEqual(allowed['body']['data']['sha256'], self.binding.sha256)
        self.store.set_workspace_state('admin', 'alpha', state='archived', expected_revision=1)
        self.assertEqual(self.post(body={**BODY, 'if_observation': proof})['status'], 200)
        denied_archive = self.post(AVAILABILITY, who=admin, **options)
        self.assertEqual((denied_archive['status'], denied_archive['body']), (404, {'error': 'not_found'}))
        self.loader.assert_not_called()
        self.objects.open.assert_not_called()

    def test_cookie_admin_saves_active_query_reader_reopens_and_expired_write_rolls_back(self):
        initialize_product_presets(self.store)
        admin = session(self.login, self.exchange, 'admin-sub')
        host = ApplicationHost(config=HostConfig(ORIGIN, ('artifacts',)), sessions=self.sessions,
                               artifact_product_views=[self.view], artifact_product_presets=True)
        path = ROUTE + '/quarters/2026Q1/presets/active-kit'
        query = {**BODY, 'mode': 'files', 'path': 'pkg/', 'recursive': True, 'keyword': 'one',
                 'if_observation': self.post()['body']['data']['observation']}
        for changes in ({'csrf': None}, {'origin': 'https://other.invalid'}):
            self.assertEqual(self.post(path, app=host, who=admin, body=query, **changes)['status'], 401)
        self.assertEqual(self.post(path, app=host, body=query)['status'], 403)
        self.assertEqual(self.post(path, app=host, who=admin, body=query)['status'], 200)
        read = self.post(path, app=host, method='GET', body=None)
        self.assertEqual(read['status'], 200)
        self.assertEqual([r['key'] for r in read['body']['data']['result']['items']], [KEY])
        self.assertEqual(self.post(path, method='GET', body=None)['status'], 404)
        before = self.now
        project = product_views.project
        def expire(*args, **kwargs):
            result = project(*args, **kwargs)
            self.now = admin.expires_at
            return result
        second = path + '-expired'
        with patch.object(product_views, 'project', side_effect=expire):
            self.assertEqual(self.post(second, app=host, who=admin, body=query)['status'], 401)
        self.now = before
        self.assertEqual(self.store._transaction(lambda _, db: db.execute(
            'SELECT count(*) FROM artifact_product_presets_v1').fetchone()[0],
            write=False, with_connection=True), 1)

    def test_product_post_requires_current_session_same_origin_tls_and_csrf(self):
        other = session(self.login, self.exchange, 'admin-sub')
        # Matching header/cookie from a different session are not current CSRF.
        mixed = cookie_header({_SESSION: self.reader.token.value, _CSRF: other.csrf.value})
        for variant in [dict(csrf=None), dict(origin=None), dict(origin='https://other.invalid'),
                        dict(csrf=other.csrf.value, cookies=mixed),
                        dict(cookies=''), dict(extra={'wsgi.url_scheme': 'http'}),
                        dict(extra={'HTTP_HOST': 'other.invalid'}),
                        dict(extra={'HTTP_SEC_FETCH_SITE': 'cross-site'}),
                        dict(extra={'HTTP_AUTHORIZATION': 'Bearer synthetic-reader'}),
                        dict(extra={'HTTP_AUTHORIZATION': 'Bearer synthetic-reader'}, cookies='')]:
            with self.subTest(variant=list(variant)):
                response = self.post(**variant)
                self.assertEqual(response['status'], 401)
                self.assertEqual(set(response['body']), {'error'})
        self.assertEqual(self.post()['status'], 200)
        self.login.logout(self.reader.token, self.reader.csrf)
        self.assertEqual(self.post()['status'], 401)

    def test_cookie_expiry_while_projecting_discards_completed_rows(self):
        original = product_views.project
        def finish(*args, **kwargs):
            result = original(*args, **kwargs)
            self.now = self.reader.expires_at
            return result
        with patch.object(product_views, 'project', side_effect=finish) as projected:
            result = self.post()
        projected.assert_called_once()
        self.assertEqual(result['status'], 401)
        self.assertEqual(set(result['body']), {'error'})

    def test_cookie_rechecks_approver_membership_before_stale_observation(self):
        self.assertEqual(self.post()['status'], 200)
        self.store.set_membership('second', 'alpha', 'admin', role='viewer', state='active',
                                  expected_revision=1)
        with patch.object(product_views.ProductViewRegistry, 'lookup', autospec=True,
                          side_effect=product_views.ProductViewRegistry.lookup) as lookup, \
             patch.object(product_views, 'project', wraps=product_views.project) as projected:
            response = self.post(body={**BODY, 'page': 2, 'if_observation': '0'*64})
        lookup.assert_not_called()
        projected.assert_not_called()
        self.assertEqual((response['status'], response['body']), (404, {'error': 'not_found'}))

    def test_host_mount_is_explicit_and_never_a_generic_resource(self):
        for config in (HostConfig(ORIGIN), HostConfig(ORIGIN, ('artifacts',))):
            for args in ({}, {'artifact_product_views': []}):
                app = ApplicationHost(config=config, sessions=self.sessions, **args)
                self.assertEqual(self.post(app=app)['status'], 404)
        with self.assertRaises(ValueError):
            ApplicationHost(config=HostConfig(ORIGIN), sessions=self.sessions, artifact_product_views=[self.view])
        raw = '/api/workspaces/alpha/modules/artifacts/resources'
        for path in (raw, raw+'/kit'):
            self.assertEqual(self.post(path, method='GET', body=None)['status'], 404)
        self.assertEqual(self.post(extra={'HTTP_X_WORKSPACE_ID': 'beta'})['status'], 400)
        self.assertEqual(self.post(ROUTE.replace('/alpha/', '/beta/'))['status'], 404)
