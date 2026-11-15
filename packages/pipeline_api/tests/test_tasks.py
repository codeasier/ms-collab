"""Synthetic revision-pinned task reads; no raw log text or upstream network."""
from copy import deepcopy
from datetime import timedelta
import sqlite3
import unittest
from unittest.mock import patch

from packages.pipeline_api import encode_snapshots, resource_id
from packages.pipeline_api.tests import test_application as fixtures
from packages.pipeline_api.tasks import STATES
from packages.workspaces import ModuleInstance


class TaskTests(unittest.TestCase):
    setUp = fixtures.ApplicationTests.setUp
    request = fixtures.ApplicationTests.request
    update = fixtures.ApplicationTests.update
    error = fixtures.ApplicationTests.error

    def read(self, query='revision=1', **kwargs):
        return self.request('/runs/run-1/tasks', query=query, **kwargs)

    def test_positions_duplicate_ids_and_status_counts_are_observations(self):
        row=fixtures.run();job=row['stages'][0]['jobs'][0]
        row['stages']=[{'name':'<img src=x>', 'jobs':[dict(job,status='success'),dict(job,status='failure')]},
                       {'name':'<img src=x>', 'jobs':[dict(job,id='different',status='unknown')]},
                       {'name':'Empty stage', 'jobs':[]}]
        self.update(encode_snapshots(fixtures.SCOPE,[row]))
        data=self.read()['body']['data']
        self.assertEqual(data['coverage'],{'stage_count':3,'task_count':3,'status_counts':dict.fromkeys(STATES,0)|{'success':1,'failure':1,'unknown':1},'duplicate_id_tasks':2,'stage_status_supplied':False})
        self.assertEqual([(j['stage_index'],j['job_index'],j['id_occurrences']) for j in data['items']],[(0,0,2),(0,1,2),(1,0,1)])
        self.assertNotIn('status',data['stages'][0]);self.assertEqual(data['stages'][2]['task_count'],0)
        self.assertEqual(data['run']['status'],'failure');self.assertEqual(data['stages'][0]['name'],'<img src=x>')
        self.assertFalse(data['source']['full_history_complete']);self.assertFalse(data['source']['live_provider_verified'])

    def test_filter_pagination_and_global_coverage(self):
        row=fixtures.run();job=row['stages'][0]['jobs'][0]
        row['stages'][0]['jobs']=[dict(job,id=f'job-{i}',status='running' if i%2 else 'success') for i in range(25)]
        self.update(encode_snapshots(fixtures.SCOPE,[row]))
        first=self.read('revision=1&limit=10&status=running')['body']['data']
        second=self.read('revision=1&offset=10&limit=10&status=running')['body']['data']
        self.assertEqual((first['total'],len(first['items']),len(second['items'])),(12,10,2))
        self.assertEqual(second['coverage'],first['coverage']);self.assertEqual(first['coverage']['task_count'],25)
        self.assertEqual([j['job_index'] for j in second['items']],[21,23])
        self.assertEqual(self.read('revision=1&offset=100000')['body']['data']['items'],[])

    def test_unknown_missing_and_empty_are_distinct_not_success(self):
        row=fixtures.run();row['stages']=None;self.update(encode_snapshots(fixtures.SCOPE,[row]))
        missing=self.read()['body']['data'];self.assertFalse(missing['run']['details_available']);self.assertEqual(missing['stages'],[])
        row['stages']=[];self.update(encode_snapshots(fixtures.SCOPE,[row]))
        empty=self.read()['body']['data'];self.assertTrue(empty['run']['details_available']);self.assertEqual(empty['coverage']['task_count'],0)
        self.assertEqual(empty['run']['status'],'failure')

    def test_requires_canonical_revision_and_known_bounded_query(self):
        self.assertEqual(self.read('')['status'],428)
        for query in ['revision=0','revision=01','revision=-1','revision=1.0','revision=1000000000','revision=1&revision=1','revision=1&limit=0','revision=1&limit=101','revision=1&offset=-1','revision=1&offset=100001','revision=1&status=COMPLETED','revision=1&status=','revision=1&cursor=secret','revision=1&query=secret','revision=1&owner=admin','revision=1&x=%FF']:
            with self.subTest(query=query):self.assertEqual(self.read(query)['status'],400)

    def test_revision_change_never_returns_a_mixed_page(self):
        self.assertEqual(self.read()['status'],200)
        with sqlite3.connect(self.path) as connection:connection.execute('UPDATE resources SET revision=revision+1 WHERE workspace_id=? AND id=?',('alpha',resource_id(fixtures.SCOPE)))
        self.assertEqual(self.read()['body'],{'error':'revision_mismatch'});self.assertEqual(self.read()['status'],412)
        self.assertEqual(self.read('revision=2')['status'],200)

    def test_current_membership_before_lookup_or_decode(self):
        with patch('packages.pipeline_api.application.decode_snapshots',side_effect=AssertionError('must not decode')):
            self.assertEqual(self.read(token='test-outsider')['status'],404)
            self.assertEqual(self.read(token='test-disabled')['status'],401)
            self.store.set_membership('admin','alpha','reader',role='viewer',state='revoked',expected_revision=1)
            self.assertEqual(self.read()['status'],404)

    def test_other_scope_is_not_selected_by_headers_or_query(self):
        for scope in ('beta','commons'):
            self.assertEqual(self.request(path=fixtures.BASE.replace('/alpha/',f'/{scope}/')+'/runs/run-1/tasks',query='revision=1',token='test-admin')['status'],404)
        self.assertEqual(self.read(token=None,extra={'HTTP_COOKIE':'user=admin','HTTP_X_USER_ID':'admin'})['status'],401)
        self.assertEqual(self.read(extra={'HTTP_X_WORKSPACE_ID':'beta'})['body']['data']['scope'],fixtures.SCOPE.as_dict())
        self.assertEqual(self.read('revision=1&workspace_id=beta')['status'],400)

    def test_assertion_expiry_before_transaction_response_is_denied(self):
        from packages.pipeline_api.application import task_page
        def expire(*args,**kwargs):
            result=task_page(*args,**kwargs);self.now+=timedelta(minutes=2);return result
        with patch('packages.pipeline_api.application.task_page',side_effect=expire):
            self.assertEqual(self.read()['status'],401)

    def test_corrupted_or_private_extra_fields_fail_closed(self):
        row=fixtures.run();row['stages'][0]['jobs'][0]['raw_log']='PRIVATE_MARKER'
        import json
        self.update(json.dumps({'version':1,'scope':fixtures.SCOPE.as_dict(),'runs':[row]}))
        result=self.read();self.assertEqual(result['status'],503);self.assertNotIn('PRIVATE_MARKER',str(result))

    def test_no_log_retry_or_arbitrary_task_path(self):
        for suffix in ('/runs/run-1/logs','/runs/run-1/tasks/latest','/runs/run-1/tasks/0','/durations/run-1/tasks','/runs/run-1/tasks/../logs'):
            self.assertEqual(self.request(suffix,query='revision=1')['status'],404)
        for method in ('POST','PUT','DELETE','PATCH','HEAD'):self.assertEqual(self.read(method=method)['status'],404)
        self.assertEqual(self.request('/runs/no-run/tasks',query='revision=1')['status'],404)
        self.assertEqual(self.read()['headers']['Cache-Control'],'no-store')

if __name__=='__main__':unittest.main()
