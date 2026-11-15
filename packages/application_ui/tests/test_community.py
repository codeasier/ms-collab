"""Community approval remains current cookie/CSRF/CAS authority, not a UI grant."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from datetime import timedelta

from packages.application_host.tests import fixtures
from packages.application_ui import ApplicationUI
from packages.application_ui.tests.test_application import request
from packages.session_api.application import _SESSION, _CSRF
from packages.session_api.tests.test_application import cookie_header

BASE='/api/workspaces/alpha/community/source/sample/project'

class CommunitySessionTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup);self.directory=directory.name;self.now=fixtures.NOW
        self.host,self.policy,self.login,self.exchange=fixtures.setup(directory.name,clock=lambda:self.now);self.app=ApplicationUI(host=self.host)
        self.use('admin-sub')
    def use(self,subject):
        self.session=fixtures.session(self.login,self.exchange,subject)
        self.cookies=cookie_header({_SESSION:self.session.token.value,_CSRF:self.session.csrf.value})
    def read(self,action='items',**kw):return request(self.app,BASE+'/'+action,cookies=self.cookies,**kw)
    def approve(self,body=None,**kw):
        options=dict(method='POST',body=body if body is not None else {'fingerprints':[]},origin=fixtures.ORIGIN,csrf=self.session.csrf.value,revision='"1"');options.update(kw)
        return self.read('approve',**options)
    def test_shared_entry_requires_config_but_static_source_grants_no_data(self):
        self.assertEqual(request(self.app,'/community/')['status'],200)
        self.assertEqual(request(self.app,'/community/entry.mjs')['status'],404)
        self.assertEqual(request(self.app,BASE+'/items')['status'],401)
        self.assertNotIn('type="password"',request(self.app,'/community/')['raw'].decode())
        self.assertEqual(self.read(extra={'HTTP_AUTHORIZATION':'Bearer synthetic'})['status'],401)
    def test_capabilities_match_real_host_no_external_refresh(self):
        caps=self.read('capabilities')['body']['data']['capabilities'];self.assertEqual(caps,dict(items=True,review=True,refresh=False,approve=True))
        self.assertEqual(self.read('refresh',method='POST',body={},origin=fixtures.ORIGIN,csrf=self.session.csrf.value,revision='"1"')['status'],503)
        self.use('reader-sub');self.assertEqual(self.read('capabilities')['body']['data']['capabilities'],dict(items=True,review=False,refresh=False,approve=False))
        self.assertEqual(self.read('review')['status'],404);self.assertEqual(self.approve()['status'],403)
    def test_unlisted_admin_is_not_an_implicit_reader_or_approver(self):
        self.use('second-sub');self.assertEqual(self.read('capabilities')['status'],404);self.assertEqual(self.read()['status'],404);self.assertEqual(self.approve()['status'],404)
    def test_approval_is_exact_complete_set_and_requires_csrf_current_revision(self):
        rows=self.read('review?limit=100&offset=0')['body']['data']['items'];self.assertEqual(len(rows),2)
        selected=next(x for x in rows if not x['approved'])['fingerprint']
        self.assertEqual(self.approve({'fingerprints':[selected]},csrf=None)['status'],401)
        self.assertEqual(self.approve({'fingerprints':[selected]},csrf='B'*43)['status'],401)
        self.assertEqual(self.approve({'fingerprints':[selected]},origin='https://other.invalid')['status'],401)
        self.assertEqual(self.approve({'fingerprints':[selected]})['status'],200)
        self.assertEqual(self.approve({'fingerprints':[selected]})['status'],412)
        self.assertEqual([x['number'] for x in self.read()['body']['data']['items']],[2])
        self.assertEqual(self.approve({'fingerprints':['f'*64]},revision='"2"')['status'],400)
    def test_scope_and_revocation_remain_current_for_reads_and_approval(self):
        self.assertEqual(request(self.app,BASE.replace('/alpha/','/commons/')+'/items',cookies=self.cookies)['status'],404)
        self.policy.set_membership('second','alpha','admin',role='admin',state='revoked',expected_revision=1)
        for action in ('capabilities','items','review'):self.assertEqual(self.read(action)['status'],404)
        self.assertEqual(self.approve()['status'],404)
        self.assertEqual(request(self.app,'/community/')['status'],200)
    def test_expired_or_revoked_session_cannot_approve_old_content(self):
        self.login.revoke_subject('admin-sub');self.assertEqual(self.approve()['status'],401)
        self.use('admin-sub');self.now+=timedelta(days=2)
        self.assertEqual(self.read()['status'],401);self.assertEqual(self.approve()['status'],401)

if __name__=='__main__':unittest.main()
