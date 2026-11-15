"""Session/CSRF/current policy integration for published artifacts and approval."""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from packages.application_host import HostConfig
from packages.application_host.tests import fixtures
from packages.application_ui.tests.artifact_fixtures import application, stage
from packages.application_ui.tests.test_application import request
from packages.session_api.application import _SESSION, _CSRF
from packages.session_api.tests.test_application import cookie_header
from packages.artifact_sync.records import decode_policy, policy_text
from packages.artifacts import Scope

BASE='/api/workspaces/alpha/artifact-admin/source/bucket/'
CAT='/api/workspaces/alpha/artifacts/source/bucket/'


class ArtifactSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.now=fixtures.NOW
        self.app,self.policy,self.login,self.exchange=application(self.tmp.name,clock=lambda:self.now)
        self.sign_in()
    def sign_in(self,subject='admin-sub'):
        self.session=fixtures.session(self.login,self.exchange,subject)
        self.cookies=cookie_header({_SESSION:self.session.token.value,_CSRF:self.session.csrf.value})
    def read(self,path,**kw):return request(self.app,path,cookies=kw.pop('cookies',self.cookies),**kw)
    def write(self,path,**kw):
        options=dict(method='POST',body={},origin=fixtures.ORIGIN,csrf=self.session.csrf.value,revision='"2"');options.update(kw)
        return self.read(path,**options)
    def review(self):return self.read(BASE+'review',revision='"2"')['body']['data']
    def approval(self):
        r=self.review();return {'candidate_digest':r['candidate_digest'],'readers':r['readers'],'confirmation':'approve_complete_candidate_to_reviewed_readers'}
    def query(self,**kw):return self.write(CAT+'query',body={'quarter':'2026Q1','definition':{'recursive':True,'files_only':True}},**kw)

    def test_explicit_page_mount_no_password_and_default_admin_gate(self):
        for path in ('/artifacts/','/artifact-admin/'):
            r=self.read(path,cookies='');self.assertEqual(r['status'],200)
            self.assertNotIn(b'id="credential"',r['raw']);self.assertNotIn(b'type="password"',r['raw']);self.assertNotIn(b'/entry.mjs',r['raw'])
        self.assertEqual(self.read('/app/bootstrap')['body']['data']['ui_modules'],['workspaces','responsibility','pipelines','artifacts','artifact-admin'])
        for path in ('/artifacts/entry.mjs','/artifact-admin/entry.mjs'):
            self.assertEqual(self.read(path)['status'],404)
        with tempfile.TemporaryDirectory() as d:
            app,*_=application(d,administration=False)
            self.assertEqual(request(app,'/artifact-admin/')['status'],404)
            self.assertEqual(request(app,BASE+'status')['status'],404)
        for config in ({'artifact_administration':True},{'modules':('artifacts',),'artifact_administration':1}):
            with self.assertRaises(ValueError):HostConfig(fixtures.ORIGIN,**config)

    def test_no_bearer_or_actor_fallback_and_all_unsafe_queries_need_csrf(self):
        for path in (BASE+'status',CAT+'quarters'):
            self.assertEqual(self.read(path,cookies='',extra={'HTTP_X_USER':'admin'})['status'],401)
            self.assertEqual(self.read(path,extra={'HTTP_AUTHORIZATION':'Bearer synthetic-admin'})['status'],401)
        for csrf in (None,'B'*43):self.assertEqual(self.query(csrf=csrf)['status'],401)
        self.assertEqual(self.query(origin='https://other.invalid')['status'],401)
        self.assertEqual(self.query()['status'],200)

    def test_published_reader_fence_named_view_and_complete_admin_review(self):
        self.assertNotIn('private-candidate',str(self.query()['body']))
        self.assertNotIn('private-candidate',str(self.read(CAT+'views/files?quarter=2026Q1')['body']))
        r=self.review();self.assertEqual(r['count'],3);self.assertTrue(r['complete']);self.assertIn('private-candidate',str(r));self.assertEqual(r['readers'],['admin','reader'])
        self.assertEqual(self.read(BASE+'status')['body']['data']['capabilities'],{'refresh':False,'review':True,'approve':True})
        self.sign_in('reader-sub');self.assertEqual(self.read(BASE+'status')['status'],404)
        self.assertEqual(self.read(BASE+'review',revision='"2"')['status'],403)
        self.assertNotIn('private-candidate',str(self.query()['body']))

    def test_approval_cookie_csrf_exact_digest_reader_set_and_revision(self):
        body=self.approval()
        self.assertEqual(self.write(BASE+'approve',body=body,csrf=None)['status'],401)
        self.assertEqual(self.write(BASE+'approve',body={**body,'readers':['reader']})['status'],412)
        self.assertEqual(self.write(BASE+'approve',body={**body,'candidate_digest':'0'*64})['status'],412)
        self.assertEqual(self.write(BASE+'approve',body=body)['status'],200)
        self.assertIn('private-candidate',str(self.query()['body']))
        self.assertEqual(self.write(BASE+'approve',body=body)['status'],412)
        with sqlite3.connect(Path(self.tmp.name)/'policy.sqlite') as db:self.assertEqual(db.execute("SELECT count(*) FROM artifact_index_events_v1 WHERE action='approve'").fetchone()[0],1)

    def test_provider_refresh_and_download_stay_unmounted_or_unavailable(self):
        service=self.app._host._artifact_admin._service
        with patch.object(service,'_credential_loader') as loader,patch.object(service,'_refresh_verified') as refresh:
            self.assertEqual(self.write(BASE+'refresh')['status'],503);loader.assert_not_called();refresh.assert_not_called()
        self.assertEqual(self.write('/api/workspaces/alpha/artifact-download/source/bucket/download',body={'key':'builds/private-candidate.txt'})['status'],404)

    def test_cas_and_reader_change_require_full_rereview(self):
        body=self.approval();stage(self.tmp.name)
        self.assertEqual(self.write(BASE+'approve',body=body)['status'],412)
        self.assertNotIn('private-candidate',str(self.query()['body']))
        with sqlite3.connect(Path(self.tmp.name)/'policy.sqlite') as db:
            old=decode_policy(db.execute("SELECT policy FROM artifact_indexes_v1 WHERE workspace='alpha'").fetchone()[0],Scope('alpha','source','bucket'))
            db.execute("UPDATE artifact_indexes_v1 SET policy=? WHERE workspace='alpha'",(policy_text(replace(old,readers=('admin',))),))
        self.assertEqual(self.write(BASE+'approve',body=body,revision='"3"')['status'],412)

    def test_cross_scope_and_membership_revoke_deny_old_cookie(self):
        for path in (BASE.replace('/alpha/','/beta/')+'status',BASE.replace('/alpha/','/commons/')+'review',CAT.replace('/source/','/other/')+'quarters'):
            self.assertEqual(self.read(path,revision='"2"')['status'],404)
        body=self.approval();self.policy.set_membership('second','alpha','admin',role='admin',state='revoked',expected_revision=1)
        self.assertEqual(self.write(BASE+'approve',body=body)['status'],404);self.assertEqual(self.query()['status'],404)

    def test_current_logout_and_expiry_revoke_admin_and_reader_routes(self):
        body=self.approval();self.login.revoke_subject('admin-sub')
        self.assertEqual(self.write(BASE+'approve',body=body)['status'],401);self.assertEqual(self.query()['status'],401)
        self.sign_in();self.now+=timedelta(days=2)
        self.assertEqual(self.read(BASE+'status')['status'],401)

    def test_expiry_after_approval_persist_rolls_back_publication_and_audit(self):
        body=self.approval()
        original=self.policy._persist
        def expired(*args,**kwargs):
            result=original(*args,**kwargs);self.now+=timedelta(days=2);return result
        with patch.object(self.policy,'_persist',side_effect=expired):
            self.assertEqual(self.write(BASE+'approve',body=body)['status'],401)
        with sqlite3.connect(Path(self.tmp.name)/'policy.sqlite') as db:
            self.assertEqual(db.execute("SELECT published_revision,revision FROM artifact_indexes_v1 WHERE workspace='alpha'").fetchone(),(1,2))
            self.assertEqual(db.execute('SELECT count(*) FROM artifact_index_events_v1').fetchone()[0],0)


if __name__=='__main__':unittest.main()
