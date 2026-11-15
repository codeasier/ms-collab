"""Real cookie/session dispatch, CSRF and fail-closed opt-in for management."""
from dataclasses import replace
from packages.application_ui.tests.test_application import UITests, request
from packages.application_host import HostConfig, ApplicationHost
from packages.application_ui import ApplicationUI
from packages.community_api.records import read
from packages.community_board import Scope
from packages.workspace_connections import CommunityConnectionTemplate, initialize_connections
from packages.session_api import SessionPolicyStore
import unittest

BASE='/api/workspaces/alpha/connections/community'
class ConnectionEntryTests(UITests):
    def setUp(self):
        super().setUp()
        config=self.policy._transaction(lambda p,c:read(c,Scope('alpha','source','sample/project')).policy,write=False,with_connection=True)
        self.template=CommunityConnectionTemplate('source-policy',replace(config,reader_ids=('admin','reader','second'),refresher_ids=('admin','second'),approver_ids=('admin','second')),('admin',))
        initialize_connections(self.policy,(self.template,))
        host=ApplicationHost(config=replace(self.host._config,community_connections=(self.template,)),sessions=SessionPolicyStore(policy=self.policy,login=self.login),clock=lambda:self.now)
        self.app=ApplicationUI(host=host)
    def test_connections_opt_in_and_exact_static_navigation(self):
        self.assertEqual(self.read('/connections/',cookies='')['status'],200)
        html=self.read('/connections/',cookies='')['raw'].decode()
        import re
        for tag in ('html','body','main','footer'):
            self.assertEqual(len(re.findall('<'+tag+r'(?:[ >])',html)),1,tag)
            self.assertEqual(html.count('</'+tag+'>'),1,tag)
        self.assertNotIn('id="app-data"',html);self.assertNotIn('id="app-workspaces"',html)
        self.assertIn('<h1>Control who can share.</h1>',html)
        self.assertIn('connections',self.read('/app/bootstrap')['body']['data']['ui_modules'])
        self.assertEqual(self.read('/connections/',extra={'HTTP_SEC_FETCH_SITE':'cross-site','HTTP_SEC_FETCH_MODE':'navigate','HTTP_SEC_FETCH_DEST':'document'})['status'],200)
        for path in ('/app/connections.mjs',BASE):self.assertEqual(self.read(path,extra={'HTTP_SEC_FETCH_SITE':'cross-site','HTTP_SEC_FETCH_MODE':'navigate','HTTP_SEC_FETCH_DEST':'document'})['status'],401)
        self.assertEqual(self.read('/connections/?workspace=alpha')['status'],404)
    def test_actual_cookie_manages_and_clears_approved_content(self):
        data=self.read(BASE+'/manage')['body']['data'];self.assertEqual(len(data['items']),1)
        r=self.write(BASE+'/manage/source-policy',method='PATCH',body=dict(reader_ids=['admin'],refresher_ids=['admin'],approver_ids=['admin']))
        self.assertEqual(r['status'],200);self.assertEqual(r['body']['data']['revision'],2)
        self.assertFalse(r['body']['data']['snapshot_available']);self.assertEqual(r['body']['data']['approved_count'],0)
        self.assertNotIn('synthetic-reference',str(r));self.assertNotIn('issuer',str(r));self.assertNotIn('subject',str(r))
    def test_no_bearer_or_forged_identity_fallback_and_csrf_required(self):
        body=dict(reader_ids=[],refresher_ids=[],approver_ids=[])
        self.assertEqual(self.read(BASE,cookies='')['status'],401)
        self.assertEqual(self.read(BASE,extra={'HTTP_AUTHORIZATION':'Bearer synthetic','REMOTE_USER':'admin'})['status'],401)
        self.assertEqual(self.write(BASE+'/manage/source-policy',method='PATCH',body=body,csrf=None)['status'],401)
        self.assertEqual(self.write(BASE+'/manage/source-policy',method='PATCH',body=body,origin='https://other.example.invalid')['status'],401)
    def test_current_session_and_membership_revocation(self):
        self.policy.set_membership('second','alpha','admin',role='admin',state='revoked',expected_revision=1)
        self.assertEqual(self.read(BASE+'/manage')['status'],404)
        self.login.revoke_subject('admin-sub');self.assertEqual(self.read(BASE)['status'],401)
    def test_existing_host_positional_refresh_flag_is_preserved(self):
        config=HostConfig('https://app.example.invalid',('artifacts',),(),(),True,False,True)
        self.assertTrue(config.artifact_refresh)
        self.assertEqual(config.community_connections,())

    def test_absent_config_routes_remain_unmounted(self):
        self.app=ApplicationUI(host=self.host)
        self.assertEqual(self.read(BASE)['status'],404);self.assertEqual(self.read('/connections/')['status'],404)
        self.assertNotIn('connections',self.read('/app/bootstrap')['body']['data']['ui_modules'])
        with self.assertRaises(ValueError):HostConfig('https://app.example.invalid',community_connections=(self.template,))


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(ConnectionEntryTests(name) for name in ConnectionEntryTests.__dict__ if name.startswith('test_'))
