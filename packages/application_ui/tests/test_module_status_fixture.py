"""Real SQLite/Cookie fixture checks; no product authentication is replaced.

The listener-only wiring probe below substitutes the socket and TLS context, not
the host, login coordinator, bound-code exchange, policy or HTTP application.
The browser suite separately drives the real temporary HTTPS listener.
"""
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from packages.application_host.tests import fixtures
from packages.application_ui import ApplicationUI
from packages.application_ui.tests import browser_server
from packages.application_ui.tests.module_status_fixture import MODE, LONG_MODULE_KEY, dispatch, provision
from packages.session_api.application import _SESSION, _CSRF
from packages.session_api.tests.test_application import cookie_header, cookie_values
from packages.workspace_api.tests.test_module_status import database_snapshot


PREFIX = '/synthetic/module-status/'
CONTROLS = tuple(PREFIX + name for name in (
    'empty-capabilities-commons', 'suspend-alpha', 'archive-beta', 'revoke-beta-reader'))
OTHER_MODES = ('', 'closed', 'pipelines', 'assets', 'pipeline-tasks', 'pipeline-discovery')


def request(app, path, *, method='GET', cookies='', origin=None, body=None, extra=None):
    raw = b'' if body is None else json.dumps(body).encode()
    path, _, query = path.partition('?')
    env = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': query,
        'SCRIPT_NAME': '', 'wsgi.url_scheme': 'https', 'HTTP_HOST': 'app.example.invalid',
        'wsgi.input': io.BytesIO(raw), 'CONTENT_LENGTH': str(len(raw)),
        'CONTENT_TYPE': 'text/plain', 'HTTP_COOKIE': cookies}
    if body is not None:
        env['CONTENT_TYPE'] = 'application/json'
    if origin is not None:
        env['HTTP_ORIGIN'] = origin
    env.update(extra or {})
    result = {}

    def start(status, headers):
        result.update(status=int(status.split()[0]), headers=headers)

    result['raw'] = b''.join(app(env, start))
    if dict(result['headers']).get('Content-Type', '').startswith('application/json'):
        result['body'] = json.loads(result['raw'])
    return result


class ModuleStatusFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.host, self.policy, self.login, self.exchange = fixtures.setup(self.temp.name)
        provision(self.policy)
        self.ui = ApplicationUI(host=self.host)
        self.session = fixtures.session(self.login, self.exchange, 'reader-sub')
        self.cookies = cookie_header({_SESSION: self.session.token.value, _CSRF: self.session.csrf.value})
        self.app = self.wrapped(MODE)

    def wrapped(self, mode):
        def app(env, start):
            response = dispatch(self.policy, mode=mode, origin=fixtures.ORIGIN,
                                environ=env, start_response=start)
            return self.ui(env, start) if response is None else response
        return app

    def read(self, path, **kwargs):
        return request(self.app, path, cookies=kwargs.pop('cookies', self.cookies), **kwargs)

    def control(self, name, **kwargs):
        values = {'method': 'POST', 'origin': fixtures.ORIGIN}
        values.update(kwargs)
        return self.read(PREFIX + name, **values)

    def projected(self, workspace, key, state, revision):
        response = self.read(f'/api/workspaces/{workspace}/modules/{key}')
        self.assertEqual(response['status'], 200, response)
        self.assertEqual(response['body'], {'data': {
            'workspace_id': workspace, 'key': key, 'state': state, 'revision': revision}})
        return response

    def snapshots(self):
        return database_snapshot(self.policy._path), database_snapshot(self.login._path)

    def test_seed_binds_three_reader_roles_and_real_status_business_distinctions(self):
        self.assertEqual(self.read('/auth/session')['body']['data']['principal_id'], 'reader')
        self.assertEqual([w['id'] for w in self.read('/api/workspaces')['body']['data']],
                         ['alpha', 'beta', 'commons'])
        for workspace, state, revision, business in (
                ('alpha', 'disabled', 3, 409), ('beta', 'disabling', 2, 409),
                ('commons', 'enabled', 1, 200)):
            with self.subTest(workspace=workspace):
                self.projected(workspace, 'pipelines', state, revision)
                path = fixtures.READS[1].replace('/alpha/', '/' + workspace + '/')
                self.assertEqual(self.read(path)['status'], business)
        with sqlite3.connect(self.policy._path) as db:
            self.assertEqual(db.execute("SELECT workspace_id,role,state,revision FROM memberships "
                "WHERE principal_id='reader' ORDER BY workspace_id").fetchall(),
                [('alpha', 'viewer', 'active', 1), ('beta', 'developer', 'active', 1),
                 ('commons', 'admin', 'active', 1)])
            # These are actual bound grants, not merely new membership rows.
            self.assertEqual(db.execute("SELECT workspace_id,subject,generation FROM membership_authority "
                "WHERE principal_id='reader' ORDER BY workspace_id").fetchall(),
                [('alpha', 'reader-sub', 1), ('beta', 'reader-sub', 1), ('commons', 'reader-sub', 1)])
        for suffix in ('members', 'modules', 'module-catalog'):
            self.assertEqual(self.read('/api/workspaces/alpha/' + suffix)['status'], 403)
        self.assertEqual(self.read('/api/workspaces/commons/modules/missing')['status'], 404)

    def test_long_key_is_real_disabled_module_without_resources_or_extra_projection(self):
        self.assertEqual(len(LONG_MODULE_KEY), 200)
        self.projected('commons', LONG_MODULE_KEY, 'disabled', 1)
        with sqlite3.connect(self.policy._path) as db:
            self.assertEqual(db.execute('SELECT can_read,can_write,state,revision FROM modules '
                'WHERE workspace_id=? AND key=?', ('commons', LONG_MODULE_KEY)).fetchall(),
                [(0, 0, 'disabled', 1)])
            self.assertEqual(db.execute('SELECT count(*) FROM resources WHERE module_key=?',
                                       (LONG_MODULE_KEY,)).fetchone()[0], 0)

    def test_empty_capabilities_changes_only_commons_pipelines_not_long_key_or_receipts(self):
        before = database_snapshot(self.policy._path)
        self.assertEqual(self.control('empty-capabilities-commons')['status'], 200)
        after = database_snapshot(self.policy._path)
        self.assertEqual(after[0], before[0])
        for table in before[1]:
            if table != 'modules':
                self.assertEqual(after[1][table], before[1][table], table)
        changed = [row for row in after[1]['modules'] if row not in before[1]['modules']]
        removed = [row for row in before[1]['modules'] if row not in after[1]['modules']]
        self.assertEqual(removed, [('commons', 'pipelines', 1, 1, 'enabled', 1)])
        self.assertEqual(changed, [('commons', 'pipelines', 0, 0, 'enabled', 1)])
        self.projected('commons', 'pipelines', 'enabled', 1)
        self.projected('commons', LONG_MODULE_KEY, 'disabled', 1)
        self.assertEqual(self.read(fixtures.READS[1].replace('/alpha/', '/commons/'))['status'], 403)
        before_repeat = self.snapshots()
        self.assertEqual(self.control('empty-capabilities-commons')['status'], 409)
        self.assertEqual(self.snapshots(), before_repeat)

    def test_suspend_archive_then_revoke_use_current_policy_and_leave_commons_intact(self):
        before = self.snapshots()
        self.assertEqual(self.control('revoke-beta-reader')['status'], 409)
        self.assertEqual(self.snapshots(), before)
        self.assertEqual(self.control('suspend-alpha')['status'], 200)
        self.assertEqual(self.read('/api/workspaces/alpha/modules/pipelines')['status'], 409)
        self.assertEqual(self.control('archive-beta')['status'], 200)
        self.projected('beta', 'pipelines', 'disabling', 2)
        self.assertEqual(self.control('revoke-beta-reader')['status'], 200)
        # No workspace-list reload is needed before the exact current denial.
        denied = self.read('/api/workspaces/beta/modules/pipelines')
        missing = self.read('/api/workspaces/commons/modules/missing')
        self.assertEqual((denied['status'], denied['body']), (missing['status'], missing['body']))
        self.assertEqual(denied['status'], 404)
        self.projected('commons', LONG_MODULE_KEY, 'disabled', 1)
        with sqlite3.connect(self.policy._path) as db:
            self.assertEqual(db.execute("SELECT state,revision FROM workspaces WHERE id='beta'").fetchone(),
                             ('archived', 2))
            self.assertEqual(db.execute("SELECT role,state,revision FROM memberships "
                "WHERE workspace_id='beta' AND principal_id='reader'").fetchone(),
                ('developer', 'revoked', 2))
            self.assertEqual(db.execute("SELECT actor_id,workspace_id,action,object_id FROM audit "
                "WHERE action IN ('workspace.state','membership.update') ORDER BY sequence").fetchall(),
                [('admin', 'alpha', 'workspace.state', 'alpha'),
                 ('admin', 'beta', 'workspace.state', 'beta'),
                 ('second', 'beta', 'membership.update', 'reader')])

    def test_non_post_missing_or_wrong_origin_never_changes_either_store(self):
        before = self.snapshots()
        for path in CONTROLS:
            for method in ('GET', 'HEAD', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'):
                with self.subTest(path=path, method=method):
                    result = self.read(path, method=method, origin=fixtures.ORIGIN)
                    self.assertEqual(result['status'], 403)
                    self.assertEqual(self.snapshots(), before)
            for origin in (None, '', 'null', 'https://other.invalid', fixtures.ORIGIN + '/'):
                with self.subTest(path=path, origin=origin):
                    self.assertEqual(self.read(path, method='POST', origin=origin)['status'], 403)
                    self.assertEqual(self.snapshots(), before)

    def test_unexpected_parameters_transport_or_authority_hints_never_mutate(self):
        cases = [({'body': {}}, 400), ({'body': {'workspace_id': 'beta', 'principal': 'admin',
            'sql': 'not executable'}}, 400)]
        cases += [({'extra': extra}, status) for extra, status in (
            ({'CONTENT_LENGTH': '-1'}, 400), ({'CONTENT_LENGTH': '00'}, 400),
            ({'CONTENT_LENGTH': '1'}, 400), ({'CONTENT_TYPE': 'application/json'}, 400),
            ({'HTTP_TRANSFER_ENCODING': 'chunked'}, 400), ({'SCRIPT_NAME': '/prefix'}, 400),
            ({'wsgi.url_scheme': 'http'}, 403), ({'HTTP_HOST': 'other.invalid'}, 403),
            ({'HTTP_AUTHORIZATION': 'Bearer synthetic'}, 400),
            ({'HTTP_X_WORKSPACE_ID': 'beta'}, 400), ({'HTTP_X_PRINCIPAL_ID': 'admin'}, 400),
            ({'HTTP_X_USER_ID': 'admin'}, 400), ({'HTTP_X_SUBJECT': 'admin-sub'}, 400),
            ({'REMOTE_USER': 'admin'}, 400))]
        before = self.snapshots()
        for path in CONTROLS:
            for query in ('workspace=beta', 'principal=admin', 'sql=not-executable', 'x=1', 'x='):
                with self.subTest(path=path, query=query):
                    self.assertEqual(self.read(path + '?' + query, method='POST', origin=fixtures.ORIGIN)['status'], 400)
                    self.assertEqual(self.snapshots(), before)
            for options, status in cases:
                with self.subTest(path=path, options=options):
                    self.assertEqual(self.read(path, method='POST', origin=fixtures.ORIGIN, **options)['status'], status)
                    self.assertEqual(self.snapshots(), before)

    def test_unknown_control_paths_and_every_other_mode_are_unavailable_without_changes(self):
        before = self.snapshots()
        for suffix in ('unknown', 'suspend-beta', 'archive-beta/extra', 'revoke-beta-reader/',
                       'empty-capabilities-commons/pipelines', ''):
            self.assertEqual(self.read(PREFIX + suffix, method='POST', origin=fixtures.ORIGIN)['status'], 404)
            self.assertEqual(self.snapshots(), before)
        for mode in (*OTHER_MODES, 'unknown'):
            for path in CONTROLS:
                with self.subTest(mode=mode, path=path):
                    result = request(self.wrapped(mode), path, method='POST', origin=fixtures.ORIGIN,
                                     cookies=self.cookies)
                    self.assertEqual(result['status'], 404)
                    self.assertEqual(self.snapshots(), before)

    def test_helper_passes_api_auth_and_static_requests_to_unchanged_cookie_host(self):
        def unexpected_start(*args):
            self.fail('helper intercepted a product request')
        for path in ('/', '/app/bootstrap', '/auth/session', '/auth/logout',
                     '/api/workspaces', '/api/workspaces/commons/modules/pipelines'):
            self.assertIsNone(dispatch(self.policy, mode=MODE, origin=fixtures.ORIGIN,
                                      environ={'PATH_INFO': path}, start_response=unexpected_start))
        path = '/api/workspaces/commons/modules/pipelines'
        self.assertEqual(self.read(path, cookies='')['status'], 401)
        self.assertEqual(self.read(path, extra={'HTTP_AUTHORIZATION': 'Bearer synthetic'})['status'], 401)
        self.assertEqual(self.read('/auth/logout', method='POST', origin=fixtures.ORIGIN, body={})['status'], 401)
        self.assertEqual(self.read('/auth/logout', method='POST', origin=fixtures.ORIGIN, body={},
            extra={'HTTP_X_CSRF_TOKEN': self.session.csrf.value})['status'], 200)
        self.assertEqual(self.read(path)['status'], 401)


class BrowserModeWiringTests(unittest.TestCase):
    def test_opt_in_and_certificate_arguments_still_fail_closed_before_setup(self):
        for opt_in, args in (('', ['fixture', 'cert', 'key', MODE]),
                             ('1', ['fixture']), ('1', ['fixture', 'cert', 'key', 'unknown'])):
            with self.subTest(opt_in=opt_in, args=args), \
                    patch.dict(os.environ, {'MS_COLLAB_SYNTHETIC_APPLICATION_UI': opt_in}), \
                    patch.object(sys, 'argv', args), patch.object(browser_server, 'make_server') as create:
                with self.assertRaises(SystemExit):
                    browser_server.main()
                create.assert_not_called()

    def test_actual_fixture_dispatch_selects_reader_only_in_new_mode_and_keeps_closed_default(self):
        class Listener:
            server_port = 45123
            socket = object()

            def set_app(self, app):
                self.app = app

        origin = 'https://127.0.0.1:45123'
        for mode in ('', 'closed', MODE):
            with self.subTest(mode=mode):
                listener = Listener()

                def serve(server, *, on_ready):
                    self.assertIs(server, listener)

                    def call(path, *, provider=False, **kwargs):
                        return request(server.app, path, extra={'HTTP_HOST':
                            'localhost:45123' if provider else '127.0.0.1:45123',
                            **kwargs.pop('extra', {})}, **kwargs)

                    bootstrap = call('/app/bootstrap')['body']['data']
                    self.assertEqual(bootstrap['login_configured'], mode != 'closed')
                    self.assertEqual(bootstrap['ui_modules'], ['workspaces', 'responsibility', 'pipelines',
                        'community', 'assistant', 'research-assets', 'artifacts'])
                    begin = call('/auth/start', method='POST', origin=origin, body={},
                                 extra={'HTTP_X_MS_COLLAB_LOGIN': '1'})
                    if mode == 'closed':
                        self.assertEqual(begin['status'], 503)
                        for path in CONTROLS:
                            self.assertEqual(call(path, method='POST', origin=origin)['status'], 404)
                        self.assertEqual(call('/synthetic-provider', provider=True)['status'], 401)
                        return
                    self.assertEqual(begin['status'], 200)
                    target = urlsplit(begin['body']['data']['authorization_url'])
                    provider = call(target.path + '?' + target.query, provider=True)
                    self.assertEqual(provider['status'], 303)
                    callback_url = urlsplit(dict(provider['headers'])['Location'])
                    callback = call(callback_url.path + '?' + callback_url.query,
                                    cookies=cookie_header(cookie_values(begin)),
                                    extra={'HTTP_SEC_FETCH_SITE': 'cross-site'})
                    self.assertEqual(callback['status'], 303)
                    cookies = cookie_header(cookie_values(callback))
                    session = call('/auth/session', cookies=cookies)
                    self.assertEqual(session['body']['data']['principal_id'],
                                     'reader' if mode == MODE else 'admin')
                    spaces = call('/api/workspaces', cookies=cookies)['body']['data']
                    self.assertEqual([w['id'] for w in spaces],
                                     ['alpha', 'beta', 'commons'] if mode == MODE else ['alpha'])
                    status = call('/api/workspaces/alpha/modules/pipelines', cookies=cookies)['body']['data']
                    self.assertEqual((status['state'], status['revision']),
                                     ('disabled', 3) if mode == MODE else ('enabled', 1))
                    if mode == MODE:
                        self.assertEqual(call(CONTROLS[0], method='POST', origin=origin)['status'], 200)
                    else:
                        for path in CONTROLS:
                            self.assertEqual(call(path, method='POST', origin=origin)['status'], 404)

                args = ['fixture', 'temporary-cert', 'temporary-key'] + ([mode] if mode else [])
                with patch.dict(os.environ, {'MS_COLLAB_SYNTHETIC_APPLICATION_UI': '1'}), \
                        patch.object(sys, 'argv', args), \
                        patch.object(browser_server, 'make_server', return_value=listener), \
                        patch.object(browser_server.ssl, 'SSLContext'), \
                        patch.object(browser_server, 'serve_synthetic', side_effect=serve) as serving:
                    browser_server.main()
                    serving.assert_called_once()


if __name__ == '__main__':
    unittest.main()
