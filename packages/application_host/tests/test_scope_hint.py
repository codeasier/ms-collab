"""W05 across the actual cookie routers; no live identity, data or providers."""
from dataclasses import replace
import sqlite3
import tempfile
import unittest

from packages.application_host import ApplicationHost
from packages.application_host.tests import fixtures
from packages.application_ui import ApplicationUI
from packages.community_api.records import read as community_record
from packages.community_board import Scope
from packages.gui_smoke_ui.tests import fixtures as gui_fixture
from packages.session_api.application import _SESSION, _CSRF
from packages.session_api.tests.test_application import call, cookie_header
from packages.workspace_connections import CommunityConnectionTemplate, initialize_connections

HINT = {'HTTP_X_WORKSPACE_ID': 'beta'}
DIRECTORIES = (
    '/api/workspaces/alpha/pipelines/scopes',
    '/api/workspaces/alpha/artifacts/scopes',
    '/api/workspaces/alpha/artifact-admin/scopes',
    '/api/workspaces/alpha/connections/community',
    '/api/workspaces/alpha/connections/community/manage',
)
MANAGEMENT = (
    '/api/workspaces/alpha/members', '/api/workspaces/alpha/invitations',
    '/api/workspaces/alpha/modules', '/api/workspaces/alpha/module-catalog',
    '/api/workspaces/alpha/artifact-admin/source/bucket/status',
)


class HostScopeHintTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.now = fixtures.NOW
        original, self.policy, self.login, self.exchange = fixtures.setup(self.temp.name, clock=lambda: self.now)
        config = self.policy._transaction(lambda p, db: community_record(db, Scope('alpha', 'source', 'sample/project')).policy,
                                          write=False, with_connection=True)
        template = CommunityConnectionTemplate('source-policy', config, ('admin',))
        initialize_connections(self.policy, (template,))
        self.app = ApplicationHost(config=replace(original._config, artifact_administration=True,
                                   community_connections=(template,)), sessions=original._boundary._sessions,
                                   clock=lambda: self.now)
        self.sign_in()

    def sign_in(self, subject='admin-sub'):
        self.session = fixtures.session(self.login, self.exchange, subject)
        self.cookies = cookie_header({_SESSION: self.session.token.value, _CSRF: self.session.csrf.value})

    def request(self, path, **kwargs):
        return call(self.app, path, cookies=kwargs.pop('cookies', self.cookies), **kwargs)

    def write(self, path, **kwargs):
        options = dict(method='POST', body={}, revision='"1"', origin=fixtures.ORIGIN, csrf=self.session.csrf.value)
        options.update(kwargs)
        return self.request(path, **options)

    def rejected(self, response, status=400, code='invalid_request'):
        self.assertEqual((response['status'], response['body']), (status, {'error': code}))
        self.assertEqual(dict(response['headers'])['Vary'], 'Cookie, Origin')

    def policy_dump(self):
        with sqlite3.connect(self.policy._path) as db: return tuple(db.iterdump())

    def test_six_modules_directories_and_management_share_rejection_boundary(self):
        for path in (*fixtures.READS, *DIRECTORIES, *MANAGEMENT):
            with self.subTest(path=path):
                expected = self.request(path)
                self.assertEqual(expected['status'], 200, expected)
                matching = self.request(path, extra={'HTTP_X_WORKSPACE_ID': 'alpha'})
                self.assertEqual((matching['status'], matching['body']), (200, expected['body']))
                self.rejected(self.request(path, extra=HINT))
                self.rejected(self.request(path, cookies='', extra=HINT), 401, 'authentication_required')
                for workspace in ('beta', 'commons', 'missing'):
                    self.rejected(self.request(path.replace('/alpha/', '/' + workspace + '/'),
                                  extra={'HTTP_X_WORKSPACE_ID': 'alpha'}), 404, 'not_found')
        # A real session in both products and commons does not make hints routing inputs.
        self.sign_in('second-sub')
        for workspace in ('alpha', 'beta', 'commons'):
            path = fixtures.READS[1].replace('/alpha/', '/' + workspace + '/')
            good = self.request(path, extra={'HTTP_X_WORKSPACE_ID': workspace})
            self.assertEqual(good['status'], 200)
            self.assertEqual(good['body']['data']['scope']['workspace_id'], workspace)
            self.rejected(self.request(path, extra={'HTTP_X_WORKSPACE_ID': 'wrong'}))
        self.sign_in('reader-sub')
        for path in fixtures.READS[:-1]:
            self.rejected(self.request(path, extra=HINT))
        self.sign_in('outsider-sub')
        for path in (*fixtures.READS, *DIRECTORIES):
            self.rejected(self.request(path, extra=HINT), 404, 'not_found')

    def test_mutations_have_no_business_audit_idempotency_or_policy_effects(self):
        before = self.policy_dump()
        mutations = (
            (fixtures.READS[0].replace('/items', '/approve'), 'POST', {'fingerprints': []}, '"1"'),
            (fixtures.READS[0].replace('/items', '/refresh'), 'POST', {}, '"1"'),
            (fixtures.READS[-2], 'PUT', {'fields': []}, '"1"'),
            ('/api/workspaces/alpha/assistant/conversations/one', 'PATCH', {'state': 'archived'}, '"2"'),
            ('/api/workspaces/alpha/members/reader', 'PATCH', {'role': 'viewer', 'state': 'revoked'}, '"1"'),
            ('/api/workspaces/alpha/invitations', 'POST', {'principal_id': 'outsider', 'role': 'viewer'}, '"1"'),
            ('/api/workspaces/alpha/modules', 'POST', {'key': 'pipelines'}, '"1"'),
            ('/api/workspaces/alpha/modules/pipelines', 'PATCH', {'state': 'disabling'}, '"1"'),
            ('/api/workspaces/alpha/connections/community/manage/source-policy', 'PATCH',
             {'reader_ids': [], 'refresher_ids': [], 'approver_ids': []}, '"1"'),
            ('/api/workspaces/alpha/artifact-admin/source/bucket/refresh', 'POST', {}, '"1"'),
            ('/api/workspaces/alpha/artifact-admin/source/bucket/approve', 'POST',
             {'candidate_digest': 'a' * 64, 'readers': ['admin', 'reader'],
              'confirmation': 'approve_complete_candidate_to_reviewed_readers'}, '"1"'),
            ('/api/workspaces/alpha/artifacts/source/bucket/query', 'POST',
             {'quarter': '2026Q1', 'definition': {'files_only': True}}, '"1"'),
        )
        extra = dict(HINT, HTTP_IDEMPOTENCY_KEY='11111111-1111-4111-8111-111111111111')
        for path, method, body, revision in mutations:
            with self.subTest(path=path):
                self.rejected(self.write(path, method=method, body=body, revision=revision, extra=extra))
                self.rejected(self.write(path, method=method, body=body, revision=revision, csrf=None, extra=extra),
                              401, 'authentication_required')
                self.assertEqual(self.policy_dump(), before)

    def test_control_scope_keeps_operator_visibility_without_membership(self):
        path = '/api/control/workspaces/beta'  # admin is operator, not a beta member.
        before = self.policy_dump()
        self.rejected(self.write(path, method='PATCH', body={'state': 'suspended'},
                                 extra={'HTTP_X_WORKSPACE_ID': 'alpha'}))
        self.assertEqual(self.policy_dump(), before)
        self.rejected(self.write(path.replace('/beta', '/missing'), method='PATCH', body={'state': 'suspended'},
                                 extra=HINT), 404, 'not_found')
        self.sign_in('reader-sub')
        self.rejected(self.write(path, method='PATCH', body={'state': 'suspended'}, extra=HINT), 403, 'forbidden')
        self.rejected(self.write(path, method='PATCH', body={'state': 'suspended'},
                                 extra={'HTTP_X_WORKSPACE_ID': 'alpha'}), 403, 'forbidden')
        self.sign_in()
        self.assertEqual(self.write(path, method='PATCH', body={'state': 'suspended'}, extra=HINT)['status'], 200)

    def test_unscoped_routes_and_unknown_or_unmounted_routes_are_not_reinterpreted(self):
        for path in ('/api/workspaces', '/api/control/workspaces', '/api/control/capabilities',
                     '/api/control/initial-admin-selections/reader', '/api/invitations', '/auth/session'):
            expected = self.request(path)
            result = self.request(path, extra={'HTTP_X_WORKSPACE_ID': None})
            self.assertEqual((result['status'], result['body']), (expected['status'], expected['body']))
            self.assertEqual(result['status'], 200)
        ui = ApplicationUI(host=self.app)
        self.assertEqual(call(ui, '/app/bootstrap', extra=HINT)['status'], 200)
        for path in ('/api/workspaces/alpha/unknown/items', '/api/workspaces/alpha/modules/pipelines/resources/raw',
                     '/api/workspaces/alpha/artifact-download/source/bucket/download'):
            self.rejected(self.request(path, extra=HINT), 404, 'not_found')
        created = self.write('/api/workspaces/alpha/invitations', body={'principal_id': 'outsider', 'role': 'viewer'},
            extra={'HTTP_IDEMPOTENCY_KEY': '11111111-1111-4111-8111-111111111111'})
        self.assertEqual(created['status'], 200, created)
        self.sign_in('outsider-sub')
        result = self.write('/api/invitations/' + created['body']['data']['id'], method='PATCH',
                            body={'state': 'accepted'}, extra=HINT)
        self.assertEqual(result['status'], 200, result)
        self.assertEqual(self.write('/auth/logout', extra=HINT)['status'], 200)

    def test_seventh_module_uses_same_boundary_without_changing_gui_grants(self):
        with tempfile.TemporaryDirectory() as directory:
            ui, policy, login, exchange, _ = gui_fixture.setup(directory)
            cookies, _ = gui_fixture.cookies(login, exchange)
            for workspace in ('alpha', 'beta'):
                path = gui_fixture.BASE.replace('/alpha/', '/' + workspace + '/') + '/tasks'
                matching = call(ui._host, path, cookies=cookies, extra={'HTTP_X_WORKSPACE_ID': workspace})
                self.assertEqual(matching['status'], 200)
                self.assertEqual(matching['body']['data']['scope']['workspace_id'], workspace)
                self.rejected(call(ui._host, path, cookies=cookies, extra={'HTTP_X_WORKSPACE_ID': 'wrong'}))
            # Matching hint still cannot replace the stored scope-specific reader grant.
            self.rejected(call(ui._host, gui_fixture.BASE.replace('/alpha/', '/commons/') + '/tasks',
                               cookies=cookies, extra={'HTTP_X_WORKSPACE_ID': 'commons'}), 404, 'not_found')
            policy.set_membership('admin', 'alpha', 'alice', role='viewer', state='revoked', expected_revision=1)
            self.rejected(call(ui._host, gui_fixture.BASE + '/tasks', cookies=cookies, extra=HINT), 404, 'not_found')
