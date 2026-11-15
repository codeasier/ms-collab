# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Synthetic identities/content; real same-database policy and WSGI operations."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import copy
import http.client
import json
import sqlite3
from threading import Thread
from unittest.mock import patch
import unittest
from wsgiref.simple_server import make_server,WSGIRequestHandler

from packages.community_api.tests import test_application as fixtures
from packages.community_api import replace_policy as replace_community
from packages.community_api.records import fingerprint
from packages.community_board.model import Snapshot,BoardItem,Scope
from packages.identity import Credential,Provider,AuthenticationError
from packages.responsibility import normalize_paths
from packages.responsibility_api import ResponsibilityApplication,ResponsibilityPolicy,initialize,replace_policy
from packages.responsibility_api.records import read,_TABLE,_SCHEMA
from packages.workspaces import PolicyError,StorageError,SQLiteWorkspaceService

BASE='/api/workspaces/alpha/responsibility/source/sample/project'
SCOPE=fixtures.SCOPE
DIFF={'head_sha':'a'*40,'base_sha':'b'*40,'base':'main'}
FIELDS=[dict(id='ui',name='UI',kind='developer',paths=['ui'],owner_ids=['dev','admin'],backup={'principal_id':'reader','field_id':'core'}),
        dict(id='core',name='Core',kind='developer',paths=['src'],owner_ids=['reader'],backup=None),
        dict(id='review',name='Review',kind='committer',paths=['ui','src'],owner_ids=['second'],backup=None)]

def policy(scope=SCOPE):return ResponsibilityPolicy(scope,('reader','admin','dev'),('admin',),('admin',))


class ResponsibilityTests(unittest.TestCase):
    def setUp(self):
        fixtures.CommunityTests.setUp(self)
        self.community=self.app
        for w in ('alpha','beta','commons'):
            self.store.install_module('admin',w,'responsibility',capabilities=('read','write'))
            self.store.set_module_state('admin',w,'responsibility',state='enabled',expected_revision=1)
        initialize(self.store,[policy(p.scope) for p in self.policies])
        self.app=ResponsibilityApplication(store=self.store,providers=(Provider('test',fixtures.ISSUER,'community-test',self.verifier),),provider_key='test',clock=lambda:self.now)
        self.provider.observe=lambda connection,credential,observed_at,before_read=None:Snapshot(connection.scope,observed_at,(
            BoardItem('issue',1,'Unassigned synthetic issue','open','2026-01-01T00:00:00Z'),
            BoardItem('pull',2,'Synthetic UI change','open','2026-01-01T00:00:00Z'),
            BoardItem('pull',3,'Unapproved synthetic change','open','2026-01-01T00:00:00Z'),
            BoardItem('pull',4,'Synthetic missing paths','open','2026-01-01T00:00:00Z')))
        self.assertEqual(fixtures.call(self.community,'/refresh',method='POST',token='test-admin',revision=1,body={})['status'],200)
        self.assertEqual(fixtures.call(self.community,'/approve',method='POST',token='test-admin',revision=2,body={'fingerprints':[fingerprint(SCOPE,i) for i in self.cstate().snapshot.items if i.number!=3]})['status'],200)

    def cstate(self):return self.store._transaction(lambda p,c:fixtures.read(c,SCOPE),write=False,with_connection=True)
    def rstate(self,scope=SCOPE):return self.store._transaction(lambda p,c:read(c,scope),write=False,with_connection=True)
    def request(self,suffix='/config',**kwargs):return fixtures.call(self.app,suffix,base=kwargs.pop('base',BASE),**kwargs)
    def save(self,config=None,**kwargs):
        kwargs.setdefault('revision',self.rstate()['revision']);kwargs.setdefault('token','test-admin')
        return self.request(method='PUT',body={'fields':copy.deepcopy(FIELDS if config is None else config)},**kwargs)
    def evidence(self,number=2):
        item=next(i for i in self.cstate().snapshot.items if i.number==number)
        return {'fingerprint':fingerprint(SCOPE,item),'paths':normalize_paths(DIFF,[[{'filename':'ui/a','status':'modified'},{'filename':'unmanaged/file','status':'added'}]],DIFF|{'changed_files':2}).as_dict()}
    def publish(self,records=None,**kwargs):
        kwargs.setdefault('expected_revision',self.rstate()['revision']);kwargs.setdefault('community_revision',self.cstate().revision)
        kwargs.setdefault('observed_at',self.now.isoformat());kwargs.setdefault('scope',SCOPE);kwargs.setdefault('credential',Credential('test-admin'))
        return self.app.publish_prepared(observations=[self.evidence()] if records is None else records,**kwargs)
    def count_audit(self):return len(self.store.audit_events('admin','alpha'))

    def test_empty_config_and_missing_evidence_remain_unknown(self):
        data=self.request()['body']['data'];self.assertEqual(data['fields'],[]);self.assertEqual(data['revision'],1)
        result=self.request('/candidates',query='view=all')['body']['data']
        self.assertEqual([x['number'] for x in result['items']],[1,2,4]);self.assertEqual(result['incomplete_pull_count'],2)
        self.assertFalse(result['workflow_enabled']);self.assertFalse(result['evidence_current'])
        self.assertEqual(self.request('/candidates')['body']['data']['items'],[])

    def test_authenticated_scope_grants_not_role_names(self):
        for token,status in ((None,401),('bad',401),('test-disabled',401),('test-outsider',404),('test-second',404)):
            self.assertEqual(self.request(token=token)['status'],status)
        for base in (BASE.replace('/alpha/','/beta/'),BASE.replace('/source/','/unknown/'),BASE.replace('/project','/other')):
            self.assertEqual(self.request(base=base)['status'],404)
        self.assertEqual(self.request(base=BASE.replace('/alpha/','/commons/'))['status'],200)
        self.assertEqual(self.request(base=BASE.replace('/alpha/','/commons/'))['body']['data']['fields'],[])
        self.assertEqual(self.save(token='test-reader')['status'],404)
        self.assertEqual(self.save(token='test-second')['status'],404)

    def test_capabilities_resolve_current_grants_and_never_grant_write(self):
        self.assertEqual(self.request('/capabilities')['body']['data']['capabilities'],{'read':True,'edit':False})
        self.assertEqual(self.request('/capabilities',token='test-admin')['body']['data']['capabilities'],{'read':True,'edit':True})
        self.store.set_membership('second','alpha','admin',role='developer',state='active',expected_revision=1)
        self.assertEqual(self.request('/capabilities',token='test-admin')['body']['data']['capabilities'],{'read':True,'edit':False})
        self.assertEqual(self.save()['status'],403)
        self.assertEqual(self.request('/capabilities',token='test-outsider')['status'],404)
        self.assertEqual(self.request('/capabilities',query='actor=admin')['status'],404)

    def test_current_membership_and_both_modules_required(self):
        self.store.set_module_state('admin','alpha','responsibility',state='disabling',expected_revision=2)
        self.assertEqual(self.request()['status'],409)
        self.store.set_module_state('admin','alpha','responsibility',state='disabled',expected_revision=3)
        self.store.set_module_state('admin','alpha','responsibility',state='enabled',expected_revision=4)
        self.store.set_module_state('admin','alpha','community',state='disabling',expected_revision=1)
        self.assertEqual(self.request()['status'],409)
        self.store.set_membership('admin','alpha','reader',role='viewer',state='revoked',expected_revision=1)
        self.assertEqual(self.request()['status'],404)

    def test_save_is_cas_durable_and_does_not_grant_review_authority(self):
        result=self.save();self.assertEqual(result['status'],200)
        data=result['body']['data'];self.assertEqual((data['revision'],data['config_version']),(2,2));self.assertEqual(data['fields'],FIELDS)
        self.assertEqual(self.save(revision=1)['status'],412)
        self.assertEqual(self.save(config=[])['status'],409)
        self.assertEqual(self.request()['body']['data'],data)
        self.assertFalse(data['workflow_enabled'])
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute(f'SELECT config_version FROM {_TABLE} WHERE workspace_id="alpha" AND connection_id="source"').fetchone()[0],2)

    def test_owners_require_existing_active_scope_members_not_raw_provider_ids(self):
        for owner in ('missing','outsider','disabled','provider-user-id'):
            config=copy.deepcopy(FIELDS);config[0]['owner_ids']=[owner]
            self.assertEqual(self.save(config)['status'],400)
        self.store.set_membership('admin','alpha','dev',role='developer',state='revoked',expected_revision=1)
        self.assertEqual(self.save()['status'],400)

    def test_published_paths_still_only_readonly_approved_candidates(self):
        self.save();self.publish()
        data=self.request('/candidates',token='test-dev')['body']['data']
        self.assertEqual([i['number'] for i in data['items']],[2]);row=data['items'][0]
        self.assertEqual(row['assignment_status'],'partially_mapped');self.assertEqual(row['unmapped_paths'],['unmanaged/file'])
        self.assertEqual([f['id'] for f in row['fields']],['ui','review'])
        self.assertFalse(row['approval_available']);self.assertFalse(data['transport_executable']);self.assertEqual(data['external_effect'],'not_observed')
        self.assertTrue(data['evidence_current']);self.assertEqual(data['coverage'],'approved_observed_subset')
        self.assertEqual(self.request('/candidates',query='view=field&field_id=review')['body']['data']['total'],1)
        self.assertEqual(self.request('/candidates',query='view=unassigned')['body']['data']['items'][0]['number'],1)
        self.assertEqual(self.request('/candidates',query='view=field&field_id=unknown')['status'],404)

    def test_unapproved_or_cross_scope_content_cannot_be_published(self):
        for records in ([self.evidence(3)],[self.evidence(1)]):
            with self.assertRaises(PolicyError) as caught:self.publish(records)
            self.assertEqual(caught.exception.status,400)
        self.assertEqual(self.rstate()['evidence'],[])
        with self.assertRaises(PolicyError):self.publish(scope=Scope('commons','source','sample/project'))

    def test_publisher_is_separate_from_editor_and_reader(self):
        old=policy();replace_policy(self.store,replace(old,publisher_ids=('reader',)),expected_revision=1)
        with self.assertRaises(PolicyError) as e:self.publish()
        self.assertEqual(e.exception.status,404)
        with self.assertRaises(PolicyError) as e:self.publish(credential=Credential('test-reader'))
        self.assertEqual(e.exception.status,403) # explicit grant is not administrator role
        self.assertEqual(self.save()['status'],200)
        with self.assertRaises(ValueError):self.publish(credential='test-admin')

    def test_editor_and_publisher_still_require_current_reader_grant(self):
        replace_policy(self.store,replace(policy(),reader_ids=('reader',)),expected_revision=1)
        self.assertEqual(self.request(token='test-admin')['status'],404)
        self.assertEqual(self.save()['status'],404)
        with self.assertRaises(PolicyError) as e:self.publish()
        self.assertEqual(e.exception.status,404)
        self.assertEqual(self.rstate()['revision'],2)

    def test_reopen_service_uses_durable_config_evidence_and_current_policy(self):
        self.save();self.publish()
        expected=self.request('/candidates',query='view=all')['body']['data']
        reopened=SQLiteWorkspaceService(self.path)
        self.app=ResponsibilityApplication(store=reopened,providers=(Provider('test',fixtures.ISSUER,'community-test',self.verifier),),provider_key='test',clock=lambda:self.now)
        self.assertEqual(self.request('/candidates',query='view=all')['body']['data'],expected)
        self.store.set_membership('admin','alpha','reader',role='viewer',state='revoked',expected_revision=1)
        self.assertEqual(self.request('/candidates',query='view=all')['status'],404)

    def test_prepared_publish_requires_current_identity_and_revisions(self):
        with self.assertRaises(AuthenticationError):self.publish(credential=Credential('bad'))
        with self.assertRaises(PolicyError) as e:self.publish(community_revision=2)
        self.assertEqual(e.exception.status,412)
        self.publish()
        with self.assertRaises(PolicyError) as e:self.publish(expected_revision=1)
        self.assertEqual(e.exception.status,412)

    def test_refresh_or_approval_revision_invalidates_entire_path_observation(self):
        self.save();self.publish()
        self.assertEqual(fixtures.call(self.community,'/approve',method='POST',token='test-admin',revision=3,body={'fingerprints':list(self.cstate().approvals)})['status'],200)
        result=self.request('/candidates',query='view=all')['body']['data']
        self.assertFalse(result['evidence_current']);self.assertEqual(result['incomplete_pull_count'],2)
        self.assertEqual(self.request('/candidates',token='test-dev')['body']['data']['items'],[])
        self.publish();self.assertTrue(self.request('/candidates',query='view=all')['body']['data']['evidence_current'])

    def test_approval_removal_hides_title_and_path_even_with_existing_evidence(self):
        self.save();self.publish()
        self.assertEqual(fixtures.call(self.community,'/approve',method='POST',token='test-admin',revision=3,body={'fingerprints':[]})['status'],200)
        result=self.request('/candidates',query='view=all')['body']['data']
        self.assertEqual(result['items'],[]);self.assertEqual(result['incomplete_pull_count'],0)
        self.assertNotIn('Synthetic UI change',json.dumps(result))

    def test_policy_change_clears_old_fields_evidence_and_new_audience(self):
        self.save();self.publish();old=self.rstate();replace_policy(self.store,replace(policy(),reader_ids=('admin',)),expected_revision=old['revision'])
        self.assertEqual(self.request()['status'],404)
        self.assertEqual(self.request(token='test-admin')['body']['data']['fields'],[])
        self.assertEqual(self.rstate()['evidence'],[])
        replace_community(self.store,actor_id='admin',config=replace(fixtures.config(),reader_ids=('reader',)),expected_revision=self.cstate().revision)
        self.assertEqual(self.request(token='test-admin')['status'],404)

    def test_admin_demotion_and_assertion_expiry_prevent_writes(self):
        self.store.set_membership('second','alpha','admin',role='developer',state='active',expected_revision=1)
        self.assertEqual(self.save()['status'],403)
        with self.assertRaises(PolicyError) as e:self.publish()
        self.assertEqual(e.exception.status,403)
        self.now+=timedelta(minutes=1)
        self.assertEqual(self.request()['status'],401)

    def test_save_and_publish_roll_back_with_audit(self):
        counts=self.count_audit();old=self.rstate()
        with sqlite3.connect(self.path) as db:db.execute("CREATE TRIGGER reject_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        self.assertEqual(self.save()['status'],503)
        with self.assertRaises(StorageError):self.publish()
        self.assertEqual(self.rstate(),old);self.assertEqual(self.count_audit(),counts)

    def test_expiry_during_persistence_rolls_back_config_and_publication(self):
        old=self.rstate();persist=self.store._persist
        def advance(*args,**kwargs):persist(*args,**kwargs);self.now+=timedelta(minutes=1)
        with patch.object(self.store,'_persist',side_effect=advance):self.assertEqual(self.save()['status'],401)
        self.assertEqual(self.rstate(),old);self.now=fixtures.NOW
        with patch.object(self.store,'_persist',side_effect=advance),self.assertRaises(AuthenticationError):self.publish()
        self.assertEqual(self.rstate(),old)

    def test_concurrent_config_replacements_have_one_cas_winner(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=sorted(pool.map(lambda _:self.save(revision=1)['status'],range(2)))
        self.assertEqual(statuses,[200,412]);self.assertEqual(self.rstate()['config_version'],2)

    def test_bad_schema_and_stored_records_fail_closed(self):
        with sqlite3.connect(self.path) as db:db.execute(f'UPDATE {_TABLE} SET fields=? WHERE workspace_id=?',('{"unexpected":true}','alpha'))
        self.assertEqual(self.request()['status'],503)
        with sqlite3.connect(self.path) as db:
            db.execute(f'DROP TABLE {_TABLE}');db.execute(_SCHEMA.replace(' STRICT',''))
        self.assertEqual(self.request()['status'],503)

    def test_no_provider_calls_no_raw_fields_or_client_actor(self):
        for suffix,kwargs,status in [('/publish',{'method':'POST','body':{}},404),('/candidates',{'query':'actor=admin'},400),
                                     ('/candidates',{'query':'view=all&view=mine'},400),('/candidates',{'query':'limit=0'},400),
                                     ('/config',{'query':'scope=beta'},404),('/config',{'method':'PUT','body':{'fields':[]},'token':'test-admin'},428)]:
            self.assertEqual(self.request(suffix,**kwargs)['status'],status)
        raw=b'{"fields":[],"fields":[]}'
        self.assertEqual(self.request(method='PUT',raw=raw,revision=1,token='test-admin')['status'],400)
        for key in ('actor','credential','assignees'):
            self.assertEqual(self.request(method='PUT',revision=1,token='test-admin',body={'fields':[],key:'untrusted'})['status'],400)
        self.assertEqual(len(self.load_calls),1) # only synthetic setup refresh; responsibility performs no acquisition
        result=json.dumps(self.request('/candidates',query='view=all')['body'])
        for word in ('approved-secret-ref','test-admin','synthetic-provider-token','Unapproved synthetic change'):self.assertNotIn(word,result)

    def test_exact_body_bound_and_pagination(self):
        self.assertEqual(self.request(method='PUT',revision=1,token='test-admin',raw=b' '*65537)['status'],413)
        result=self.request('/candidates',query='view=all&offset=1&limit=1')['body']['data']
        self.assertEqual((result['total'],len(result['items']),result['items'][0]['number']),(3,1,2))
        self.assertEqual(self.request('/candidates',query='view=unassigned&field_id=ui')['status'],400)

    def test_observation_time_and_partial_path_bounds(self):
        for timestamp in ('2025-01-01T00:00:00Z','2027-01-01T00:00:00Z'):
            with self.assertRaises(PolicyError):self.publish(observed_at=timestamp)
        record=self.evidence();record['paths']=normalize_paths(DIFF,[],DIFF).as_dict()
        self.publish([record]);self.save()
        result=self.request('/candidates',query='view=all')['body']['data'];self.assertEqual(result['incomplete_pull_count'],2)
        self.assertEqual(result['items'][1]['fields'],[])

    def test_real_loopback_http_read_and_cas_config(self):
        class Quiet(WSGIRequestHandler):
            def log_message(self,*args):pass
        server=make_server('127.0.0.1',0,self.app,handler_class=Quiet);worker=Thread(target=server.serve_forever,daemon=True);worker.start()
        client=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        try:
            client.request('PUT',BASE+'/config',body=json.dumps({'fields':FIELDS}),headers={'Authorization':'Bearer test-admin','Content-Type':'application/json','If-Match':'"1"'})
            response=client.getresponse();self.assertEqual(response.status,200);self.assertEqual(json.loads(response.read())['data']['revision'],2)
            client.request('GET',BASE+'/candidates?view=all',headers={'Authorization':'Bearer test-reader'});response=client.getresponse();self.assertEqual(response.status,200);self.assertFalse(json.loads(response.read())['data']['workflow_enabled'])
            client.request('GET',BASE+'/config',headers={'X-Actor':'admin','Cookie':'identity=admin'});response=client.getresponse();self.assertEqual(response.status,401);response.read()
        finally:client.close();server.shutdown();worker.join(timeout=5);server.server_close()
