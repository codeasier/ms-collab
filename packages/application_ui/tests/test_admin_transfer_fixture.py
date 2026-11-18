"""Fixed temporary-fixture controls and true host/static wiring, never production.

Socket substitution below only observes composition. Browser acceptance drives
actual TLS, real provider callbacks, current roles and retained historical replay.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from packages.application_host.tests import fixtures
from packages.application_ui import ApplicationUI
from packages.application_ui.tests import browser_server
from packages.application_ui.tests.browser_server import (
    TRANSFER_MODE, TRANSFER_PREFIX, TRANSFER_CONTROLS, dispatch_admin_transfer)
from packages.application_ui.tests.test_module_status_fixture import request
from packages.session_api.application import _SESSION, _CSRF
from packages.session_api.tests.test_application import cookie_header, cookie_values
from packages.workspace_api.tests.test_module_status import database_snapshot
from packages.workspace_admin_api.tests.transfer_fixtures import BROWSER_ACTOR, BROWSER_SUBJECT
from packages.workspace_ui.tests import browser_server as bearer_browser_server


OTHER_MODES = ('', 'closed', 'pipelines', 'assets', 'pipeline-tasks', 'pipeline-discovery', 'module-status')


class AdminTransferFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.host, self.policy, self.login, self.exchange = fixtures.setup(self.temp.name, admin_transfers=True)
        self.ui = ApplicationUI(host=self.host)
        self.session = fixtures.session(self.login, self.exchange, BROWSER_SUBJECT)
        self.cookies = cookie_header({_SESSION: self.session.token.value, _CSRF: self.session.csrf.value})
        self.app = self.wrapped(TRANSFER_MODE)

    def wrapped(self, mode):
        def app(env, start):
            value = dispatch_admin_transfer(self.policy, mode=mode, origin=fixtures.ORIGIN,
                                           environ=env, start_response=start)
            return self.ui(env, start) if value is None else value
        return app

    def read(self, path, **kwargs):
        return request(self.app, path, cookies=kwargs.pop('cookies', self.cookies), **kwargs)

    def control(self, name, **kwargs):
        values = {'method': 'POST', 'origin': fixtures.ORIGIN}
        values.update(kwargs)
        return self.read(TRANSFER_PREFIX + name, **values)

    def snapshots(self):
        return database_snapshot(self.policy._path), database_snapshot(self.login._path)

    def test_real_opaque_actor_and_exact_controller_static_route(self):
        self.assertEqual(len(BROWSER_ACTOR), 200)
        for character in ('\x00', '\x7f', '\\', '\ufeff', '\U0001f680'):
            self.assertIn(character, BROWSER_ACTOR)
        self.assertEqual(self.read('/auth/session')['body']['data']['principal_id'], BROWSER_ACTOR)
        self.assertEqual(self.read('/api/workspaces/alpha/admin-transfers/capabilities')['body'],
                         {'data': {'available': True}})
        controller = self.read('/manage/admin-transfer.mjs')
        self.assertEqual(controller['status'], 200)
        self.assertEqual(dict(controller['headers'])['Content-Type'], 'text/javascript; charset=utf-8')
        self.assertEqual(dict(controller['headers'])['Cache-Control'], 'no-store')
        self.assertIn(b'mountAdminTransfer', controller['raw'])
        for path in ('/manage/admin-transfer.mjs/', '/manage/../admin-transfer.mjs',
                     '/manage/%2e%2e/admin-transfer.mjs', '/manage/tests/admin-transfer.cjs',
                     '/manage/admin-transfer.mjs?x=1'):
            self.assertEqual(self.read(path)['status'], 404)

    def test_fixed_repromotion_is_current_reader_policy_edit_not_receipt_recovery(self):
        before = self.snapshots()
        self.assertEqual(self.control('repromote')['status'], 409)
        self.assertEqual(self.snapshots(), before)
        self.assertEqual(self.control('status')['body']['receipts'], 0)
        review = self.read('/api/workspaces/alpha/admin-transfers/selections/reader')['body']['data']
        self.assertEqual(review['actor_id'], BROWSER_ACTOR)
        submitted = self.read('/api/workspaces/alpha/admin-transfers', method='POST',
            origin=fixtures.ORIGIN, body={'replacement_id': 'reader', 'replacement_revision': str(review['replacement_revision'])},
            extra={'HTTP_X_CSRF_TOKEN': self.session.csrf.value,
                   'HTTP_IF_MATCH': '"' + str(review['actor_revision']) + '"',
                   'HTTP_IDEMPOTENCY_KEY': 'e52d59d8-ec9b-44a9-a7a6-4048c70ac12d',
                   'HTTP_IF_ADMIN_TRANSFER_MATCH': review['precondition']})
        self.assertEqual(submitted['status'], 200, submitted)
        committed = self.control('status')['body']
        self.assertEqual((committed['actor_role'], committed['replacement_role'],
                          committed['receipts'], committed['transfer_audits']), ('developer', 'admin', 1, 2))
        promoted = self.control('repromote')
        self.assertEqual(promoted['status'], 200)
        current = promoted['body']
        self.assertEqual((current['actor_role'], current['actor_revision'], current['replacement_revision']), ('admin', 3, 2))
        self.assertEqual(current['receipts'], committed['receipts'])
        self.assertEqual(current['transfer_audits'], committed['transfer_audits'])
        self.assertEqual(current['audits'], committed['audits'] + 1)
        before_repeat = self.snapshots()
        self.assertEqual(self.control('repromote')['status'], 409)
        self.assertEqual(self.snapshots(), before_repeat)

    def test_non_post_or_missing_wrong_origin_transport_never_changes_stores(self):
        before = self.snapshots()
        for path in TRANSFER_CONTROLS:
            for method in ('GET', 'HEAD', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'):
                self.assertEqual(self.read(path, method=method, origin=fixtures.ORIGIN)['status'], 403)
                self.assertEqual(self.snapshots(), before)
            for origin in (None, '', 'null', 'https://other.invalid', fixtures.ORIGIN + '/'):
                self.assertEqual(self.read(path, method='POST', origin=origin)['status'], 403)
                self.assertEqual(self.snapshots(), before)
            for extra in ({'wsgi.url_scheme': 'http'}, {'HTTP_HOST': 'other.invalid'}, {'HTTP_SEC_FETCH_SITE': 'cross-site'}):
                self.assertEqual(self.read(path, method='POST', origin=fixtures.ORIGIN, extra=extra)['status'], 403)
                self.assertEqual(self.snapshots(), before)

    def test_control_rejects_all_request_supplied_targets_and_authority_hints(self):
        before = self.snapshots()
        cases = [{'body': {}}, {'body': {'workspace': 'beta', 'actor': 'admin', 'sql': 'not executable'}}]
        cases += [{'extra': extra} for extra in (
            {'CONTENT_LENGTH': '-1'}, {'CONTENT_LENGTH': '00'}, {'CONTENT_LENGTH': '1'},
            {'CONTENT_TYPE': 'application/json'}, {'HTTP_TRANSFER_ENCODING': 'chunked'},
            {'SCRIPT_NAME': '/prefix'}, {'HTTP_AUTHORIZATION': 'Bearer synthetic'},
            {'HTTP_X_WORKSPACE_ID': 'beta'}, {'HTTP_X_PRINCIPAL_ID': 'admin'},
            {'HTTP_X_USER_ID': 'reader'}, {'HTTP_X_SUBJECT': 'reader-sub'}, {'REMOTE_USER': 'reader'})]
        for path in TRANSFER_CONTROLS:
            for query in ('workspace=beta', 'target=admin', 'key=x', 'proof=x', 'x=1', 'sql=not-executable'):
                self.assertEqual(self.read(path + '?' + query, method='POST', origin=fixtures.ORIGIN)['status'], 400)
                self.assertEqual(self.snapshots(), before)
            for options in cases:
                with self.subTest(path=path, options=options):
                    self.assertEqual(self.read(path, method='POST', origin=fixtures.ORIGIN, **options)['status'], 400)
                    self.assertEqual(self.snapshots(), before)

    def test_unknown_routes_and_wrong_modes_are_unavailable_without_mutation(self):
        before = self.snapshots()
        for name in ('', 'unknown', 'status/', 'repromote/reader', 'reset', 'receipt', 'regrant'):
            self.assertEqual(self.control(name)['status'], 404)
            self.assertEqual(self.snapshots(), before)
        for mode in (*OTHER_MODES, 'unknown'):
            for path in TRANSFER_CONTROLS:
                value = request(self.wrapped(mode), path, method='POST', origin=fixtures.ORIGIN, cookies=self.cookies)
                self.assertEqual(value['status'], 404)
                self.assertEqual(self.snapshots(), before)

    def test_controls_never_intercept_product_static_or_auth_boundaries(self):
        def unexpected(*args):
            self.fail('fixture intercepted a product route')
        for path in ('/', '/manage/', '/manage/admin-transfer.mjs', '/app/bootstrap',
                     '/auth/session', '/auth/logout', '/api/workspaces',
                     '/api/workspaces/alpha/admin-transfers'):
            self.assertIsNone(dispatch_admin_transfer(self.policy, mode=TRANSFER_MODE, origin=fixtures.ORIGIN,
                environ={'PATH_INFO': path}, start_response=unexpected))
        self.assertEqual(self.read('/api/workspaces/alpha/admin-transfers/capabilities', cookies='')['status'], 401)
        self.assertEqual(self.read('/api/workspaces/alpha/admin-transfers', method='POST', origin=fixtures.ORIGIN,
            body={'replacement_id': 'reader', 'replacement_revision': '1'})['status'], 401)
        self.assertEqual(self.read('/auth/logout', method='POST', origin=fixtures.ORIGIN, body={})['status'], 401)


class AdminTransferBrowserWiringTests(unittest.TestCase):
    def test_modes_and_temporary_certificates_require_explicit_opt_in(self):
        for opt_in, args in (('', ['fixture', 'cert', 'key', TRANSFER_MODE]),
                             ('1', ['fixture']), ('1', ['fixture', 'cert', 'key', 'unknown'])):
            with patch.dict(os.environ, {'MS_COLLAB_SYNTHETIC_APPLICATION_UI': opt_in}), \
                    patch.object(sys, 'argv', args), patch.object(browser_server, 'make_server') as make:
                with self.assertRaises(SystemExit):
                    browser_server.main()
                make.assert_not_called()
        for opt_in, args in (('', ['fixture', 'cert', 'key', TRANSFER_MODE]),
                             ('1', ['fixture', 'cert']), ('1', ['fixture', 'cert', 'key', 'unknown'])):
            with patch.dict(os.environ, {'MS_COLLAB_SYNTHETIC_ADMIN_TRANSFER': opt_in}), \
                    patch.object(sys, 'argv', args), patch.object(bearer_browser_server, 'make_server') as make:
                with self.assertRaises(SystemExit):
                    bearer_browser_server.main()
                make.assert_not_called()

    def test_actual_host_mode_and_fixed_reader_provider_keep_old_default_unmounted(self):
        class Listener:
            server_port = 45123
            socket = object()

            def set_app(self, app):
                self.app = app

        origin = 'https://127.0.0.1:45123'
        for mode in ('', TRANSFER_MODE):
            listener = Listener()

            def serve(server, *, on_ready):
                def call(path, *, provider=False, **kwargs):
                    return request(server.app, path, extra={'HTTP_HOST':
                        ('localhost' if provider else '127.0.0.1') + ':45123', **kwargs.pop('extra', {})}, **kwargs)

                for reader in (False, True):
                    if reader and mode != TRANSFER_MODE:
                        self.assertEqual(call('/synthetic-provider-reader', provider=True)['status'], 401)
                        continue
                    begin = call('/auth/start', method='POST', origin=origin, body={},
                                 extra={'HTTP_X_MS_COLLAB_LOGIN': '1'})
                    self.assertEqual(begin['status'], 200)
                    target = urlsplit(begin['body']['data']['authorization_url'])
                    provider_path = '/synthetic-provider-reader' if reader else target.path
                    if mode == TRANSFER_MODE:
                        for malformed in ('actor=admin', target.query + '&actor=admin',
                                          target.query + '&state=duplicate', target.query + '&bad'):
                            self.assertEqual(call(provider_path + '?' + malformed, provider=True)['status'], 400)
                    provider = call(provider_path + '?' + target.query, provider=True)
                    self.assertEqual(provider['status'], 303)
                    callback = urlsplit(dict(provider['headers'])['Location'])
                    completed = call(callback.path + '?' + callback.query,
                        cookies=cookie_header(cookie_values(begin)), extra={'HTTP_SEC_FETCH_SITE': 'cross-site'})
                    self.assertEqual(completed['status'], 303)
                    cookies = cookie_header(cookie_values(completed))
                    identity = call('/auth/session', cookies=cookies)['body']['data']['principal_id']
                    self.assertEqual(identity, 'reader' if reader else BROWSER_ACTOR if mode else 'admin')
                    capability = call('/api/workspaces/alpha/admin-transfers/capabilities', cookies=cookies)
                    self.assertEqual(capability['status'], 403 if reader else 200 if mode else 404)
                for path in TRANSFER_CONTROLS:
                    self.assertEqual(call(path, method='POST', origin=origin)['status'],
                                     (200 if path.endswith('status') else 409) if mode else 404)
                # General legacy controls are not reachable in transfer mode.
                if mode == TRANSFER_MODE:
                    for path in ('/synthetic/revoke-session', '/synthetic/revoke-membership'):
                        self.assertEqual(call(path, method='POST', origin=origin)['status'], 404)

            args = ['fixture', 'temporary-cert', 'temporary-key'] + ([mode] if mode else [])
            with self.subTest(mode=mode), patch.dict(os.environ, {'MS_COLLAB_SYNTHETIC_APPLICATION_UI': '1'}), \
                    patch.object(sys, 'argv', args), patch.object(browser_server, 'make_server', return_value=listener), \
                    patch.object(browser_server.ssl, 'SSLContext'), \
                    patch.object(browser_server, 'serve_synthetic', side_effect=serve) as serving:
                browser_server.main()
                serving.assert_called_once()


if __name__ == '__main__':
    unittest.main()
