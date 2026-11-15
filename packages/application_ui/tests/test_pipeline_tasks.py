"""Task pages through the same cookie/policy transaction, no fake bearer."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from packages.application_host.tests import fixtures
from packages.application_ui import ApplicationUI
from packages.application_ui.tests.test_application import request
from packages.application_ui.tests.pipeline_task_fixtures import snapshot
from packages.pipeline_api import resource_id
from packages.pipelines import PipelineScope
from packages.session_api.application import _SESSION, _CSRF
from packages.session_api.tests.test_application import cookie_header

BASE='/api/workspaces/alpha/pipelines/example-ci/build/runs/run-tasks/tasks'

class PipelineTaskSessionTests(unittest.TestCase):
    def setUp(self):
        d=tempfile.TemporaryDirectory();self.addCleanup(d.cleanup);self.directory=d.name
        self.host,self.policy,self.login,self.exchange=fixtures.setup(d.name);self.app=ApplicationUI(host=self.host)
        self.session=fixtures.session(self.login,self.exchange,'admin-sub');self.cookies=cookie_header({_SESSION:self.session.token.value,_CSRF:self.session.csrf.value})
        self.text=snapshot()
        with sqlite3.connect(Path(d.name)/'policy.sqlite') as db:db.execute("UPDATE resources SET text=? WHERE workspace_id='alpha' AND id=?",(self.text,resource_id(PipelineScope('alpha','example-ci','build'))))
    def read(self,query='revision=1&limit=10',**kw):return request(self.app,BASE+'?'+query,cookies=self.cookies,**kw)
    def test_provider_extra_data_not_published_and_cookie_pagination_exact(self):
        self.assertNotIn('DISCARDED_SYNTHETIC_FIELD',self.text)
        first=self.read();self.assertEqual(first['status'],200);data=first['body']['data']
        self.assertEqual((data['total'],len(data['items']),data['coverage']['duplicate_id_tasks']),(23,10,3))
        page=self.read('revision=1&offset=20&limit=10')['body']['data'];self.assertEqual(len(page['items']),3);self.assertEqual(page['items'][0]['job_index'],8)
        self.assertEqual(page['stages'],data['stages']);self.assertNotIn('DISCARDED_SYNTHETIC_FIELD',str(page))
    def test_scope_current_membership_and_mixed_auth_fail_closed(self):
        self.assertEqual(self.read(extra={'HTTP_AUTHORIZATION':'Bearer synthetic'})['status'],401)
        self.assertEqual(request(self.app,BASE.replace('/alpha/','/commons/')+'?revision=1',cookies=self.cookies)['status'],404)
        self.policy.set_membership('second','alpha','admin',role='admin',state='revoked',expected_revision=1)
        self.assertEqual(self.read()['status'],404)
        self.login.revoke_subject('admin-sub');self.assertEqual(self.read()['status'],401)
    def test_revision_race_is_not_silent_retry(self):
        with sqlite3.connect(Path(self.directory)/'policy.sqlite') as db:db.execute("UPDATE resources SET revision=revision+1 WHERE workspace_id='alpha' AND module_key='pipelines'")
        self.assertEqual(self.read()['status'],412);self.assertEqual(self.read('revision=2')['status'],200)
        self.assertEqual(request(self.app,BASE.replace('/tasks','/logs')+'?revision=2',cookies=self.cookies)['status'],404)

if __name__=='__main__':unittest.main()
