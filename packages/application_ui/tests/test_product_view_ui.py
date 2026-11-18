# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Factory-gated static surface plus real Cookie reads of synthetic, explicitly published metadata."""
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urljoin

from packages.application_host import ApplicationHost, HostConfig
from packages.application_host.tests import fixtures
from packages.application_ui import ApplicationUI
from packages.application_ui.application import _FILES, _PRODUCT_FILES
from packages.application_ui.tests.artifact_product_fixtures import product_application, republish_product, revoke_product_read
from packages.application_ui.tests.test_application import request
from packages.artifact_sync import records
from packages.artifact_sync.product_views import ProductViewRegistry
from packages.artifacts import Scope
from packages.artifacts_api import ArtifactApplication
from packages.artifacts_api.tests import test_application as weak_fixture
from packages.artifacts_ui import ArtifactBrowserUI
from packages.identity import Provider
from packages.session_api.application import _SESSION, _CSRF
from packages.session_api.tests.test_application import cookie_header


PAGE = '/artifacts/product-views/'
MODULES = ('/app/artifact-product-codec.mjs', '/app/artifact-product-view.mjs')
ASSETS = {PAGE, *MODULES}
BASE = '/api/workspaces/alpha/artifacts/source/bucket'
ROUTE = BASE+'/product-views/kit'
BODY = {'format': 1, 'quarter': '2026Q2', 'page': 1, 'page_size': 10}
SUPPORT = 'artifact-product-views'
ENDPOINT = fixtures.ISSUER+'/authorize'


class Markup(HTMLParser):
    def __init__(self, raw):
        super().__init__()
        self.tags = []
        self.feed(raw.decode('utf-8'))

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def identified(self):
        return {attrs['id']: attrs for _, attrs in self.tags if 'id' in attrs}


class ProductViewUITests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)
        self.now = datetime(2026, 4, 15, tzinfo=timezone.utc)
        self.app, self.policy, self.login, self.exchange = product_application(
            tmp.name, origin=fixtures.ORIGIN, clock=lambda: self.now, authorization_endpoint=ENDPOINT)
        self.session = fixtures.session(self.login, self.exchange, 'admin-sub')
        self.cookies = cookie_header({_SESSION: self.session.token.value, _CSRF: self.session.csrf.value})

    def post(self, path=ROUTE, *, fields=None, **changes):
        args = dict(method='POST', origin=fixtures.ORIGIN, csrf=self.session.csrf.value,
                    cookies=self.cookies, body={**BODY, **(fields or {})})
        return request(self.app, path, **{**args, **changes})

    def data(self, response):
        self.assertEqual(response['status'], 200)
        self.assertEqual(set(response['body']), {'data'})
        return response['body']['data']

    def test_bootstrap_discloses_only_host_support_not_scope_identity_or_authority(self):
        self.assertIs(self.app._host.artifact_product_views_enabled, True)
        with patch.object(ProductViewRegistry, 'lookup', side_effect=AssertionError('not a lookup')):
            public = self.data(request(self.app, '/app/bootstrap'))
            self.assertEqual(public, self.data(request(self.app, '/app/bootstrap', cookies=self.cookies,
                                                       extra={'HTTP_X_WORKSPACE_ID': 'not-configured'})))
        self.assertEqual(set(public), {'service', 'ui_modules', 'authorization_endpoint', 'login_configured'})
        self.assertEqual((public['service'], public['authorization_endpoint'], public['login_configured']),
                         ('ms-collab', ENDPOINT, True))
        modules = public['ui_modules']
        self.assertEqual(modules.count(SUPPORT), 1)
        self.assertIn('artifacts', modules)
        self.assertIn('artifact-downloads', modules)
        self.assertLessEqual(set(modules), {'workspaces','responsibility','pipelines','community','assistant',
                            'research-assets','gui-smoke','artifacts','artifact-admin','artifact-downloads',
                            'connections', SUPPORT})
        self.assertEqual(len(modules), len(set(modules)))
        with self.assertRaises(AttributeError):
            self.app._host.artifact_product_views_enabled = False

    def test_bootstrap_and_assets_reflect_the_frozen_registry_not_later_mutable_inputs(self):
        scope = Scope('alpha', 'source', 'bucket')
        parent = self.policy._transaction(lambda _, db: records.load(db, scope, read_candidate=False),
                                          with_connection=True, write=False)
        definition = {'format':1, 'kind':'product-view', 'match':'relative-key-ascii-substring-v1',
                      'scope':scope.as_dict(), 'view_id':'kit', 'product_token':'kit_',
                      'parent':{'grant_revision':parent.grant_revision, 'policy_digest':records.digest(parent.policy_raw)}}
        args = dict(config=HostConfig(fixtures.ORIGIN, ('artifacts',)), sessions=self.app._host._boundary._sessions)
        originally_on = [definition]
        originally_off = []
        enabled = ApplicationUI(host=ApplicationHost(**args, artifact_product_views=originally_on))
        disabled = ApplicationUI(host=ApplicationHost(**args, artifact_product_views=originally_off))
        originally_on.clear()
        originally_off.append(definition)
        with patch.object(ProductViewRegistry, 'lookup', side_effect=AssertionError('not discovery')):
            for app, support in ((enabled, True), (disabled, False)):
                self.assertIs(app._host.artifact_product_views_enabled, support)
                self.assertIs(SUPPORT in self.data(request(app, '/app/bootstrap'))['ui_modules'], support)
                self.assertEqual(request(app, PAGE)['status'], 200 if support else 404)

    def test_disabled_and_weak_factories_do_not_publish_product_assets(self):
        sessions = self.app._host._boundary._sessions
        for modules, args in [((), {}), (('artifacts',), {}), (('artifacts',), {'artifact_product_views': []}),
                              (('artifacts',), {'artifact_product_views': None})]:
            host = ApplicationHost(config=HostConfig(fixtures.ORIGIN, modules), sessions=sessions, **args)
            self.assertIs(host.artifact_product_views_enabled, False)
            app = ApplicationUI(host=host)
            self.assertNotIn(SUPPORT, self.data(request(app, '/app/bootstrap'))['ui_modules'])
            for asset in ASSETS:
                self.assertEqual(request(app, asset)['status'], 404)
            if modules:
                doc = Markup(request(app, '/artifacts/')['raw'])
                self.assertIn('hidden', doc.identified()['app-product-view-entry'])
                self.assertEqual(doc.identified()['app-product-view-entry']['href'], PAGE)
                self._static_graph(app, '/artifacts/', forbidden=ASSETS)
        store = weak_fixture.initialize(self.directory/'weak.sqlite')
        verifier = Provider('test', weak_fixture.ISSUER, 'test-artifacts', weak_fixture.TestVerifier())
        weak = ArtifactBrowserUI(api=ArtifactApplication(store=store, providers=(verifier,), provider_key='test',
                                 clock=lambda: weak_fixture.NOW), origin=fixtures.ORIGIN)
        ordinary = request(weak, '/artifacts/')
        self.assertEqual(ordinary['status'], 200)
        self.assertNotIn(PAGE.encode(), ordinary['raw'])
        for route in ASSETS:
            self.assertEqual(request(weak, route)['status'], 404)
        self.assertEqual(weak_fixture.call(weak._api, '/views/files', query='quarter=2026Q1')['status'], 200)
        self.assertEqual(weak_fixture.call(weak._api, '/product-views/files', method='POST', body=BODY)['status'], 404)

    def _static_graph(self, app, page, *, forbidden=()):
        """Walk actual browser eager dependencies (not navigation links or optional dynamic imports)."""
        pending, seen = [page], set()
        while pending:
            path = pending.pop()
            if path in seen:
                continue
            seen.add(path)
            self.assertNotIn(path, forbidden)
            response = request(app, path)
            self.assertEqual(response['status'], 200, path)
            media = dict(response['headers'])['Content-Type']
            if media.startswith('text/html'):
                targets = [a['src'] for tag, a in Markup(response['raw']).tags if tag == 'script' and 'src' in a]
                targets += [a['href'] for tag, a in Markup(response['raw']).tags
                            if tag == 'link' and a.get('rel') == 'stylesheet']
            elif media.startswith('text/javascript'):
                targets = re.findall(r"""(?:\bfrom\s*|^\s*import\s*)['"]([^'"]+)['"]""",
                                     response['raw'].decode(), flags=re.MULTILINE)
            else:
                targets = []
            for target in targets:
                dest = urljoin(path, target)
                self.assertIn(dest, set(_FILES) | ASSETS)
                pending.append(dest)
        return seen

    def test_exact_optional_static_allowlist_dependency_closure_and_transport(self):
        self.assertEqual(set(_PRODUCT_FILES), ASSETS)
        self.assertTrue(ASSETS.isdisjoint(_FILES))
        for path in ASSETS:
            graph = self._static_graph(self.app, path)
            self.assertIn(path, graph)
            response = request(self.app, path)
            headers = dict(response['headers'])
            self.assertEqual((headers['Cache-Control'], headers['Referrer-Policy'], headers['X-Content-Type-Options']),
                             ('no-store', 'no-referrer', 'nosniff'))
            self.assertEqual(headers['Content-Type'].split(';')[0], 'text/html' if path == PAGE else 'text/javascript')
            for directive in ("script-src 'self'", "connect-src 'self'", "frame-ancestors 'none'", "form-action 'none'"):
                self.assertIn(directive, headers['Content-Security-Policy'])
            self.assertNotIn("'unsafe-inline'", headers['Content-Security-Policy'])
            self.assertEqual(request(self.app, path, method='HEAD')['raw'], b'')
            self.assertEqual(request(self.app, path+'?debug=1')['status'], 404)
            self.assertEqual(request(self.app, path, method='POST', origin=fixtures.ORIGIN)['status'], 404)
            for invalid in ({'HTTP_AUTHORIZATION': 'Bearer synthetic'}, {'HTTP_HOST': 'foreign.invalid'},
                            {'HTTP_ORIGIN': 'https://foreign.invalid'}, {'wsgi.url_scheme': 'http'},
                            {'HTTP_SEC_FETCH_SITE': 'cross-site'}, {'SCRIPT_NAME': '/mounted'}):
                self.assertEqual(request(self.app, path, extra=invalid)['status'], 401)
        nav = {'HTTP_SEC_FETCH_MODE': 'navigate', 'HTTP_SEC_FETCH_SITE': 'cross-site', 'HTTP_SEC_FETCH_DEST': 'document'}
        self.assertEqual(request(self.app, PAGE, extra=nav)['status'], 200)
        for path in (*MODULES, '/app/bootstrap', ROUTE, PAGE+'?x=1'):
            self.assertEqual(request(self.app, path, extra=nav)['status'], 401)
        self.assertEqual(request(self.app, PAGE, method='HEAD', extra=nav)['status'], 401)
        for path in (PAGE.rstrip('/'), PAGE+'kit', MODULES[0]+'/extra', '/app/product-view.mjs'):
            self.assertEqual(request(self.app, path)['status'], 404)

    def test_product_document_starts_empty_fixed_and_has_no_named_private_form_data(self):
        markup = Markup(request(self.app, PAGE)['raw'])
        ids = markup.identified()
        self.assertEqual(len(ids), sum('id' in a for _, a in markup.tags))
        self.assertIn(('body', 'artifact-product-views'), [(t,a.get('data-page')) for t,a in markup.tags])
        self.assertIn('hidden', ids['pv-controls'])
        self.assertEqual(ids['pv-return']['href'], '/artifacts/')
        self.assertEqual(ids['app-artifacts-link']['href'], '/artifacts/')
        for name in ('workspace', 'connection', 'bucket', 'view', 'quarter', 'keyword', 'start', 'end'):
            item = ids['pv-'+name]
            self.assertFalse(item.get('value'))
            self.assertNotIn('name', item)
        for tag, attrs in markup.tags:
            self.assertNotEqual(attrs.get('type'), 'password')
            if tag == 'form':
                self.assertFalse(attrs.get('action'))
                self.assertFalse(attrs.get('method', '').lower() == 'get')
        raw = request(self.app, PAGE)['raw']
        for known in ('Kit_alpha', 'private-candidate.txt', 'synthetic-reference'):
            self.assertNotIn(known.encode(), raw)

    def test_current_cookie_reads_approved_rows_not_candidate_or_legacy_namesake(self):
        self.assertEqual(self.post(cookies='')['status'], 401)
        self.assertEqual(self.post(csrf=None)['status'], 401)
        self.assertEqual(self.post(origin='https://foreign.invalid')['status'], 401)
        root = self.data(self.post())
        self.assertEqual([r['key'] for r in root['items']], ['pkg/'])
        proof = root['observation']
        level = self.data(self.post(fields=dict(path='pkg/', if_observation=proof)))
        self.assertEqual((level['total'], len(level['items'])), (16, 10))
        last = self.data(self.post(fields=dict(path='pkg/', if_observation=proof, page=2)))
        combined = [r['key'] for r in level['items'] + last['items']]
        self.assertEqual(len(set(combined)), 16)
        self.assertIn('pkg/Kit_alpha/', combined)
        self.assertIn('pkg/Kit_alpha', combined)
        files = self.data(self.post(fields=dict(path='pkg/', mode='files', recursive=True, page_size=50)))
        self.assertEqual(files['total'], 17)
        self.assertNotIn('pkg/KIT_private-candidate.txt', [r['key'] for r in files['items']])
        legacy = self.data(request(self.app, BASE+'/views/kit?quarter=2026Q2', cookies=self.cookies))
        self.assertIn('pkg/ordinary-only.bin', [r['key'] for r in legacy['items']])
        self.assertNotIn('pkg/ordinary-only.bin', combined)
        other = self.data(self.post(ROUTE.replace('/bucket/', '/second-bucket/'),
                                    fields={'mode': 'files', 'recursive': True}))
        self.assertEqual([r['key'] for r in other['items']], ['other/KIT_second-bucket.zip'])
        self.assertEqual(self.post(ROUTE.replace('/bucket/', '/missing-bucket/'))['status'], 404)

    def test_true_republication_revocation_and_session_expiry_are_distinct_from_empty(self):
        empty = self.data(self.post(fields={'quarter': '2027Q1'}))
        self.assertEqual((empty['quarter_available'], empty['total'], empty['items']), (False, 0, []))
        proof = empty['observation']
        republish_product(self.policy)
        stale = self.post(fields={'if_observation': proof})
        self.assertEqual((stale['status'], stale['body']), (412, {'error':'stale_observation'}))
        fresh = self.data(self.post(fields={'mode':'files', 'path':'pkg/', 'keyword':'after-republish'}))
        self.assertNotEqual(fresh['observation'], proof)
        self.assertEqual([r['key'] for r in fresh['items']], ['pkg/KIT_after-republish.txt'])
        revoke_product_read(self.policy)
        denied = self.post(fields={'if_observation': proof})
        self.assertEqual((denied['status'], denied['body']), (404, {'error':'not_found'}))
        self.assertEqual(request(self.app, '/auth/session', cookies=self.cookies)['status'], 200)
        self.now = self.session.expires_at
        self.assertEqual(self.post()['status'], 401)
