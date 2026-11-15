"""Saved owner history keeps the real cookie/session transaction boundary."""
from datetime import timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from packages.application_host.tests import fixtures
from packages.application_ui import ApplicationUI
from packages.application_ui.tests.test_application import request
from packages.assistant_history import create_conversation, append_events, DisplayEvent
from packages.assistant_history.application import audit
from packages.session_api.application import _SESSION, _CSRF
from packages.session_api.tests.test_application import cookie_header

BASE='/api/workspaces/alpha/assistant/conversations'

class AssistantSessionTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup);self.directory=directory.name;self.now=fixtures.NOW
        self.host,self.policy,self.login,self.exchange=fixtures.setup(directory.name,clock=lambda:self.now);self.app=ApplicationUI(host=self.host)
        create_conversation(self.policy,owner='second',workspace='alpha',conversation='private-second',title='Other owner private title',clock=lambda:self.now)
        self.use('admin-sub')
    def use(self,subject):
        self.session=fixtures.session(self.login,self.exchange,subject)
        self.cookies=cookie_header({_SESSION:self.session.token.value,_CSRF:self.session.csrf.value})
    def read(self,suffix='',**kw):return request(self.app,BASE+suffix,cookies=self.cookies,**kw)
    def lifecycle(self,state='archived',**kw):
        options=dict(method='PATCH',body={'state':state},origin=fixtures.ORIGIN,csrf=self.session.csrf.value,revision='"2"');options.update(kw)
        return self.read('/one',**options)
    def test_static_page_never_provides_a_session_or_owner_override(self):
        self.assertEqual(request(self.app,'/assistant/')['status'],200)
        html=request(self.app,'/assistant/')['raw'].decode();self.assertNotIn('type="password"',html);self.assertNotIn('id="credential"',html)
        self.assertEqual(request(self.app,BASE)['status'],401)
        self.assertEqual(self.read(extra={'HTTP_AUTHORIZATION':'Bearer synthetic'})['status'],401)
        self.assertEqual(self.read('?owner=second')['status'],400)
    def test_workspace_admin_and_operator_cannot_inherit_another_owners_history(self):
        self.assertEqual([v['id'] for v in self.read()['body']['data']['items']],['one'])
        for suffix in ('/private-second','/private-second/events'):self.assertEqual(self.read(suffix)['status'],404)
        self.assertEqual(self.read('/private-second',method='PATCH',body={'state':'archived'},origin=fixtures.ORIGIN,csrf=self.session.csrf.value,revision='"1"')['status'],404)
        self.use('second-sub');self.assertEqual(self.read('/one')['status'],404);self.assertEqual([v['id'] for v in self.read()['body']['data']['items']],['private-second'])
        self.use('reader-sub');self.assertEqual(self.read()['body']['data']['items'],[])
    def test_real_session_lifecycle_requires_csrf_and_cas_and_never_starts_execution(self):
        for changes in ({'csrf':None},{'csrf':'B'*43},{'origin':'https://other.invalid'}):self.assertEqual(self.lifecycle(**changes)['status'],401)
        self.assertEqual(self.lifecycle(revision=None)['status'],428)
        result=self.lifecycle();self.assertEqual(result['status'],200);data=result['body']['data'];self.assertEqual((data['state'],data['revision']),('archived',3))
        self.assertEqual(data['capabilities'],dict(events=True,archive=False,restore=True,execute=False,resume=False,export=False))
        self.assertEqual(self.lifecycle()['status'],412);self.assertEqual(self.read()['body']['data']['items'],[])
        self.assertEqual(self.read('?state=archived')['body']['data']['items'][0]['id'],'one')
        self.assertEqual(self.lifecycle('active',revision='"3"')['status'],200)
        for suffix in ('/one/execute','/one/resume','/one/export','/one/prompt'):
            self.assertEqual(self.read(suffix,method='POST',body={},origin=fixtures.ORIGIN,csrf=self.session.csrf.value)['status'],404)
    def test_revision_fixed_continuation_rejects_concurrent_append(self):
        append_events(self.policy,owner='admin',workspace='alpha',conversation='one',events=tuple(DisplayEvent('notice',str(i)) for i in range(100)),expected_revision=2,clock=lambda:self.now)
        first=self.read('/one/events?after=0&limit=50&revision=3')['body']['data'];self.assertEqual((len(first['items']),first['next_after'],first['total']),(50,50,101))
        self.assertEqual(self.read('/one/events?after=50&limit=50')['status'],428)
        append_events(self.policy,owner='admin',workspace='alpha',conversation='one',events=(DisplayEvent('notice','new'),),expected_revision=3,clock=lambda:self.now)
        stale=self.read('/one/events?after=50&limit=50&revision=3');self.assertEqual(stale['status'],412);self.assertNotIn(b'Synthetic answer',stale['raw'])
    def test_membership_dependency_and_session_revocation_are_current(self):
        self.assertEqual(request(self.app,BASE.replace('/alpha/','/commons/'),cookies=self.cookies)['status'],404)
        self.policy.set_membership('second','alpha','admin',role='admin',state='revoked',expected_revision=1)
        for suffix in ('','/one','/one/events'):self.assertEqual(self.read(suffix)['status'],404)
        self.assertEqual(self.lifecycle()['status'],404)
        self.login.revoke_subject('admin-sub');self.assertEqual(self.read()['status'],401)
    def test_session_expiry_after_archive_save_rolls_back_state_and_audit(self):
        db=Path(self.directory)/'policy.sqlite'
        with sqlite3.connect(db) as connection:before=connection.execute('SELECT COUNT(*) FROM audit').fetchone()[0]
        def expire(*args):
            audit(*args);self.now+=timedelta(days=2)
        with patch('packages.assistant_history.application.audit',side_effect=expire):self.assertEqual(self.lifecycle()['status'],401)
        with sqlite3.connect(db) as connection:
            self.assertEqual(connection.execute("SELECT state,revision FROM assistant_conversations WHERE workspace_id='alpha' AND id='one'").fetchone(),('active',2))
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM audit').fetchone()[0],before)

if __name__=='__main__':unittest.main()
