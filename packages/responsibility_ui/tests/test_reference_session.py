"""Real shared-session reconfirmation boundary with disposable synthetic SQLite."""
from pathlib import Path
import tempfile
import unittest
from packages.application_host.tests import fixtures
from packages.application_ui import ApplicationUI
from packages.application_ui.tests.test_application import request
from packages.session_api.application import _SESSION, _CSRF
from packages.session_api.tests.test_application import cookie_header
from packages.responsibility_ui.tests.reference_fixture import identity_aba, membership_change

BASE='/api/workspaces/alpha/responsibility/source/sample/project'
FIELDS=[dict(id='ui',name='Synthetic UI',kind='developer',paths=['ui'],owner_ids=['second'],backup=None)]

class ReferenceSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.host,self.policy,self.login,self.exchange=fixtures.setup(self.temp.name)
        self.app=ApplicationUI(host=self.host);self.session=fixtures.session(self.login,self.exchange)
        self.cookies=cookie_header({_SESSION:self.session.token.value,_CSRF:self.session.csrf.value})
        self.assertEqual(self.call('/config',method='PUT',body={'fields':FIELDS},revision='"1"')['status'],200)
    def call(self,suffix='/config',**kwargs):
        options=dict(cookies=self.cookies,origin=fixtures.ORIGIN,csrf=self.session.csrf.value);options.update(kwargs)
        return request(self.app,BASE+suffix,**options)
    def stale(self):
        identity_aba(Path(self.temp.name)/'policy.sqlite','second')
        return self.call()['body']['data']
    def confirm(self,data,**kwargs):
        options=dict(method='POST',body={'fields':data['fields'],'confirmation_token':data['references']['confirmation_token']},revision='"'+str(data['revision'])+'"');options.update(kwargs)
        return self.call('/reconfirm',**options)
    def test_cookie_confirmation_uses_current_csrf_full_body_and_cas(self):
        data=self.stale();self.assertEqual(data['references']['state'],'reconfirmation_required')
        for view in ('all','mine','field&field_id=ui','unassigned'):
            denied=self.call('/candidates?view='+view);self.assertEqual(denied['status'],409);self.assertNotIn('data',denied['body'])
        for patch in ({'csrf':None},{'csrf':'x'*43},{'extra':{'HTTP_AUTHORIZATION':'Bearer forbidden'}}):
            self.assertEqual(self.confirm(data,**patch)['status'],401)
        result=self.confirm(data);self.assertEqual(result['status'],200)
        self.assertEqual(result['body']['data']['references']['state'],'current')
        self.assertEqual(result['body']['data']['revision'],data['revision']+1)
        self.assertEqual(self.confirm(data)['status'],412)
    def test_reader_cannot_obtain_or_execute_confirmation_proposal(self):
        data=self.stale();reader=fixtures.session(self.login,self.exchange,'reader-sub')
        cookies=cookie_header({_SESSION:reader.token.value,_CSRF:reader.csrf.value})
        viewed=self.call(cookies=cookies)['body']['data']
        self.assertEqual(viewed['references']['stale_ids'],['second']);self.assertIsNone(viewed['references']['confirmation_token'])
        self.assertEqual(self.confirm(data,cookies=cookies,csrf=reader.csrf.value)['status'],404)
    def test_proposal_aba_and_expired_session_never_rebind(self):
        data=self.stale();self.stale();self.assertIn(self.confirm(data)['status'],(409,412))
        self.assertEqual(self.call()['body']['data']['revision'],data['revision'])
        self.login.revoke_subject('admin-sub');self.assertEqual(self.confirm(data)['status'],401)
    def test_fixture_membership_restore_preserves_stale_pin(self):
        path=Path(self.temp.name)/'policy.sqlite';membership_change(self.policy,path,'second','revoked')
        data=self.call()['body']['data'];self.assertIsNone(data['references']['confirmation_token'])
        membership_change(self.policy,path,'second','active');data=self.call()['body']['data']
        self.assertEqual(data['references']['state'],'reconfirmation_required');self.assertIsNotNone(data['references']['confirmation_token'])
        self.assertEqual(self.confirm(data)['status'],200)
