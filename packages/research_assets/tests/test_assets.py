"""Synthetic catalog documents and identities only; no source checkout/network."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

from packages.identity import AuthenticationError, Credential, Provider, VerifiedIdentity
from packages.research_assets import (AssetError, AssetScope, CatalogBinding, Entity, SourceBatch, SourceSpec,
    AssetApplication, AssetSynchronizer, prepare_batch, provision_catalog)
from packages.workspaces import PolicyError, SQLiteWorkspaceService, Principal, Workspace, Membership, ModuleInstance
from packages.research_assets.core import canonical, decode, project

NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
SCOPE = AssetScope('alpha', 'engineering')
ISSUER = 'https://identity.example.invalid'


def binding(scope=SCOPE):
    return CatalogBinding(scope, 'example-team', (Entity('editor', 'Editor'), Entity('tools', 'Tools')),
        (SourceSpec('primary', ('editor','tools'), ('api','records','runbook','fault')),
         SourceSpec('secondary', ('editor',), ('api','records','runbook'))))


def document(kind='records', key='overview', title='Overview', entity='editor'):
    common = {'type_id':kind,'entity_id':entity,'summary':'Synthetic documentation'}
    if kind == 'api':
        return {**common,'module':'unit','command':key,'request':'{"path":"logical-field"}',
                'response':'OK','errors':'None','notes':''}
    if kind == 'runbook':
        return {**common,'id':key,'title':title,'module':'editor','status':'verified',
                'preconditions':'Synthetic only','steps':'Describe; do not execute','expectations':'Declared only'}
    if kind == 'fault':
        return {**common,'id':key,'title':title,'library':'pipeline','category':'build','status':'active',
                'confidence':'declared','symptoms':'Synthetic symptom','guidance':'Read-only advice'}
    return {**common,'id':key,'title':title,'text':'Synthetic content; not production evidence'}


def batch(source='primary', docs=None, scope=SCOPE, revision='r1', observed='2026-01-01T12:00:00Z'):
    return SourceBatch(scope, source, revision, observed, tuple([document()] if docs is None else docs), True)


class CoreTests(unittest.TestCase):
    def prepare(self, *batches):
        return prepare_batch(binding(), tuple(batches), NOW)

    def test_stable_identity_across_sources_title_and_revision(self):
        first,_ = self.prepare(batch(),batch('secondary',[]))
        moved,_ = self.prepare(batch(docs=[],revision='r2'),batch('secondary',[document(title='Renamed')],revision='r2'))
        self.assertEqual(first[0]['id'],moved[0]['id'])
        self.assertNotEqual(first[0]['content_revision'],moved[0]['content_revision'])

    def test_scope_namespace_entity_and_business_keys_separate_identity(self):
        b=binding(); value=project(b,b.sources[0],document())
        variants=[replace(b,scope=AssetScope('beta','engineering')),replace(b,scope=AssetScope('alpha','other')),replace(b,namespace='other')]
        self.assertTrue(all(project(x,x.sources[0],document())['id']!=value['id'] for x in variants))
        self.assertNotEqual(value['id'],project(b,b.sources[0],document(entity='tools'))['id'])
        self.assertNotEqual(value['id'],project(b,b.sources[0],document(key='different'))['id'])

    def test_explicit_record_identity_no_title_fallback(self):
        for key in ('id','title'):
            d=document();del d[key]
            with self.assertRaises(AssetError):self.prepare(batch(docs=[d]),batch('secondary',[]))

    def test_duplicate_identity_across_sources_is_not_last_writer_wins(self):
        for a,b in (([document(),document()],[]),([document()],[document()])):
            with self.assertRaisesRegex(AssetError,'identity_conflict'):self.prepare(batch(docs=a),batch('secondary',b))

    def test_four_type_projection_and_declared_verification_only(self):
        assets,_=self.prepare(batch(docs=[document(k) for k in ('api','records','runbook','fault')]),batch('secondary',[]))
        self.assertEqual(len(assets),4)
        self.assertTrue(all(a['detail']['execution_verified'] is False for a in assets))
        self.assertEqual(next(a for a in assets if a['type_id']=='runbook')['fields']['status'],'verified')
        self.assertNotIn('source_id',canonical(assets));self.assertNotIn('revision',canonical(assets).replace('content_revision',''))

    def test_unknown_operational_fields_rejected_but_contract_path_is_text(self):
        for kind in ('api','records','runbook','fault'):
            for field in ('path','source_path','repository','token','evidence','regex','cases','raw'):
                d=document(kind);d[field]='private-value'
                with self.assertRaises(AssetError) as raised:self.prepare(batch(docs=[d]),batch('secondary',[]))
                self.assertNotIn('private-value',str(raised.exception))
        self.prepare(batch(docs=[document('api')]),batch('secondary',[]))

    def test_incomplete_source_must_not_be_treated_as_empty(self):
        with self.assertRaisesRegex(AssetError,'incomplete_source'):
            self.prepare(replace(batch(docs=[]),complete=False),batch('secondary',[]))

    def test_binding_encoded_limit_matches_read_limit(self):
        entities=tuple(Entity('entity-'+str(n)+'x'*110,'Title') for n in range(32))
        sources=tuple(SourceSpec('source-'+str(n),tuple(e.id for e in entities),('records',)) for n in range(16))
        with self.assertRaisesRegex(AssetError,'binding_too_large'):
            CatalogBinding(SCOPE,'example',entities,sources)

    def test_source_type_and_entity_are_bound(self):
        for d in (document('fault'),document(entity='tools')):
            with self.assertRaises(AssetError):self.prepare(batch(docs=[]),batch('secondary',[d]))

    def test_complete_sources_scope_duplicate_and_future_checks(self):
        for rows in ((batch(),), (batch(),batch()), (batch(),batch('missing',[])),
                     (batch(),batch('secondary',[],scope=AssetScope('beta','engineering'))),
                     (batch(observed='2027-01-01T00:00:00Z'),batch('secondary',[]))):
            with self.subTest(rows=rows),self.assertRaises(AssetError):self.prepare(*rows)

    def test_bad_inputs_are_bounded_and_closed(self):
        for value in ('bad\x00text','bad\ud800','x'*257):
            with self.assertRaises(AssetError):self.prepare(batch(docs=[document(title=value)]),batch('secondary',[]))
        for field,value in (('text','x'*8193),('id',''),('type_id','unknown'),('entity_id','unknown')):
            d=document();d[field]=value
            with self.assertRaises(AssetError):self.prepare(batch(docs=[d]),batch('secondary',[]))
        with self.assertRaises(AssetError):self.prepare(batch(docs=[document(key=str(i)) for i in range(1001)]),batch('secondary',[]))
        for raw in ('{"a":1,"a":2}', '{"a":NaN}', '[[['):
            with self.assertRaises(AssetError):decode(raw)

    def test_detached_projection_and_binding_roundtrip(self):
        d=document();assets,_=self.prepare(batch(docs=[d]),batch('secondary',[]));d['text']='changed'
        self.assertNotEqual(assets[0]['detail']['sections'][0]['text'],'changed')
        b=binding();self.assertEqual(CatalogBinding.decode(b.encode()),b)
        raw=json.loads(b.encode());raw['sources'][0]['url']='https://other.example.invalid'
        with self.assertRaises(AssetError):CatalogBinding.decode(json.dumps(raw))


class Verifier:
    def verify(self, credential):
        if credential.value not in {'synthetic-admin','synthetic-reader','synthetic-developer','synthetic-other','synthetic-disabled'}:
            raise AuthenticationError()
        return VerifiedIdentity(ISSUER,credential.value.removeprefix('synthetic-'),'assets',NOW,NOW+timedelta(seconds=60))


class TestProvider:
    def __init__(self):
        self.batches={s:batch(s,[] if s=='secondary' else None) for s in ('primary','secondary')}
        self.calls=[];self.hook=lambda scope,source:None
    def prepare(self,scope,source):
        self.calls.append((scope,source.id));self.hook(scope,source)
        return replace(deepcopy(self.batches[source.id]),scope=scope)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        self.path=Path(directory.name)/'synthetic.sqlite3';self.now=NOW
        spaces=('alpha','beta','commons')
        self.store=SQLiteWorkspaceService.initialize(self.path,
            principals=tuple(Principal(p,ISSUER,p,p!='disabled') for p in ('admin','reader','developer','other','disabled')),
            workspaces=tuple(Workspace(s,s,'shared' if s=='commons' else 'product') for s in spaces),
            memberships=tuple([Membership(s,'admin','admin') for s in spaces]+[Membership('alpha',p,r) for p,r in (('reader','viewer'),('developer','developer'),('disabled','viewer'))]+[Membership('commons','reader','viewer'),Membership('beta','other','viewer')]),
            modules=tuple(m for s in spaces for m in (ModuleInstance(s,'research_assets',('read',)),ModuleInstance(s,'research_assets_sync',('read','write')))))
        for s in spaces:provision_catalog(self.store,binding(AssetScope(s,'engineering')))
        providers=(Provider('test',ISSUER,'assets',Verifier()),)
        self.provider=TestProvider()
        self.sync=AssetSynchronizer(store=self.store,providers=providers,provider_key='test',provider=self.provider,clock=lambda:self.now)
        self.app=AssetApplication(store=self.store,providers=providers,provider_key='test',clock=lambda:self.now)

    def refresh(self,revision=1,scope=SCOPE,token='synthetic-admin'):
        return self.sync.refresh(user_credential=Credential(token),scope=scope,expected_revision=revision)

    def get(self,endpoint='assets',query='',scope=SCOPE,token='synthetic-reader',method='GET',path=None):
        env={'PATH_INFO':path or f'/api/workspaces/{scope.workspace_id}/research-assets/{scope.catalog_id}/{endpoint}',
             'REQUEST_METHOD':method,'QUERY_STRING':query,'wsgi.input':io.BytesIO(b'{}'),
             'CONTENT_LENGTH':'2','CONTENT_TYPE':'application/json','HTTP_COOKIE':'admin=true','HTTP_X_USER':'admin'}
        if token is not None:env['HTTP_AUTHORIZATION']='Bearer '+token
        result={}
        def start(status,headers):result.update(status=int(status.split()[0]),headers=dict(headers))
        body=b''.join(self.app(env,start));result['body']=json.loads(body)
        self.assertEqual(int(result['headers']['Content-Length']),len(body))
        return result

    def sql(self,query,args=()):
        with sqlite3.connect(self.path) as c:return c.execute(query,args).fetchall()

    def prior(self):
        return self.sql('SELECT * FROM research_catalog'),self.sql('SELECT * FROM research_asset'),self.sql('SELECT * FROM audit')

    def test_unavailable_is_not_successful_empty_and_empty_publication(self):
        data=self.get()['body']['data'];self.assertFalse(data['available']);self.assertEqual(data['revision'],1)
        self.provider.batches['primary']=batch(docs=[])
        self.refresh();data=self.get()['body']['data']
        self.assertTrue(data['available']);self.assertEqual(data['total'],0)
        self.assertFalse(data['live_provider_verified']);self.assertFalse(data['execution_verified'])

    def test_types_entities_discoverable_with_zero_assets(self):
        self.assertEqual(len(self.get('types')['body']['data']['items']),4)
        self.assertTrue(all(i['count']==0 for i in self.get('types')['body']['data']['items']))
        self.assertEqual(len(self.get('entities')['body']['data']['items']),2)

    def test_filters_paging_details_and_no_payload_in_list(self):
        self.provider.batches['primary']=batch(docs=[document(key=f'item-{i}',title=f'Entry {i:03}') for i in range(103)]+[document('api')])
        self.refresh();data=self.get(query='type=records&entity=editor&q=ENTRY&page=2&page_size=100')['body']['data']
        self.assertEqual(data['total'],103);self.assertEqual(len(data['items']),3)
        self.assertNotIn('detail',data['items'][0]);self.assertNotIn('text',data['items'][0])
        asset=self.get('assets/'+data['items'][0]['id'],query='revision=2')['body']['data']['asset']
        self.assertEqual(asset['detail']['sections'][0]['text'],'Synthetic content; not production evidence')
        self.assertEqual(self.get(query='q=%25')['body']['data']['total'],0) # literal, not LIKE wildcard

    def test_scope_separation_including_public_and_same_business_id(self):
        self.refresh();alpha=self.get()['body']['data']['items'][0]['id']
        self.refresh(scope=AssetScope('commons','engineering'))
        shared=self.get(scope=AssetScope('commons','engineering'))['body']['data']['items'][0]['id']
        self.assertNotEqual(alpha,shared)
        self.assertEqual(self.get('assets/'+alpha,scope=AssetScope('commons','engineering'))['status'],404)
        self.assertEqual(self.get(scope=AssetScope('beta','engineering'))['status'],404)
        self.assertEqual(self.get(scope=AssetScope('alpha','missing'))['status'],404)

    def test_no_cookie_header_or_disabled_authority(self):
        for token in (None,'invalid','synthetic-disabled'):
            self.assertEqual(self.get(token=token)['status'],401)
        self.assertEqual(self.get(token='synthetic-other')['status'],404)
        self.assertEqual(self.get()['headers']['Cache-Control'],'no-store')

    def test_closed_routing_queries_and_no_sync_http(self):
        for q in ('repo=primary','path=secret','type=api&type=records','page=0','page_size=101','page=true','revision=-1','q='+('x'*201),'entity=missing','type=unknown'):
            self.assertEqual(self.get(query=q)['status'],400,q)
        for path in ('/api/research-assets/assets','/api/workspaces/alpha/research-assets/engineering/sync','/api/workspaces/alpha/research-assets/engineering/assets/../other'):
            self.assertEqual(self.get(path=path)['status'],404)
        for method in ('POST','PUT','DELETE'):
            self.assertEqual(self.get(method=method)['status'],404)
        self.assertEqual(self.get('types',query='page=1')['status'],400)

    def test_stale_read_revision_fails(self):
        self.refresh()
        for endpoint in ('assets','types','entities'):
            self.assertEqual(self.get(endpoint,query='revision=1')['status'],412)
        self.assertEqual(self.get('assets/a_'+'0'*64,query='revision=1')['status'],412)

    def test_reader_and_foreign_scope_never_call_provider(self):
        for scope,token,status in ((SCOPE,'synthetic-reader',403),(AssetScope('beta','engineering'),'synthetic-developer',404)):
            with self.assertRaises(PolicyError) as raised:self.refresh(scope=scope,token=token)
            self.assertEqual(raised.exception.status,status)
        self.assertEqual(self.provider.calls,[])
        self.refresh(token='synthetic-developer')

    def test_all_sources_atomic_failure_retains_previous_and_errors_are_generic(self):
        self.refresh();old=self.prior()
        def fail(scope,source):
            if source.id=='secondary':raise RuntimeError('private-path-and-token')
        self.provider.hook=fail
        with self.assertRaises(PolicyError) as raised:self.refresh(2)
        self.assertEqual(str(raised.exception),'asset_source_unavailable')
        self.assertEqual(self.prior(),old)

    def test_duplicate_cross_source_and_wrong_source_fail_whole_batch(self):
        self.refresh();old=self.prior();self.provider.batches['secondary']=batch('secondary',[document()])
        with self.assertRaises(PolicyError):self.refresh(2)
        self.assertEqual(self.prior(),old)
        self.provider.batches['secondary']=batch('unexpected',[])
        with self.assertRaises(PolicyError):self.refresh(2)
        self.assertEqual(self.prior(),old)

    def test_source_removal_or_binding_changes_cannot_be_reprovisioned(self):
        b=binding()
        for changed in (replace(b,sources=b.sources[:1]),replace(b,namespace='other')):
            with self.assertRaisesRegex(AssetError,'binding_already'):provision_catalog(self.store,changed)
        provision_catalog(self.store,b)
        self.assertEqual(self.get()['body']['data']['revision'],1)

    def test_source_move_preserves_id_and_is_order_independent(self):
        self.refresh();identity=self.get()['body']['data']['items'][0]['id']
        self.provider.batches={'primary':batch(docs=[],revision='r2',observed='2026-01-01T13:00:00Z'),
                               'secondary':batch('secondary',[document(title='Moved')],revision='r2',observed='2026-01-01T13:00:00Z')}
        self.refresh(2);item=self.get()['body']['data']['items'][0]
        self.assertEqual(item['id'],identity);self.assertEqual(item['title'],'Moved')

    def test_exact_replay_does_not_bump_revision_or_audit(self):
        self.refresh();old=self.prior();result=self.refresh(2)
        self.assertFalse(result['changed']);self.assertEqual(result['revision'],2);self.assertEqual(self.prior(),old)

    def test_stale_and_same_revision_mutation_rejected(self):
        self.refresh();old=self.prior()
        variants=[batch(docs=[document(title='Changed')],observed='2026-01-01T13:00:00Z'),
                  batch(revision='r2'),batch(revision='r2',observed='2025-01-01T00:00:00Z')]
        for value in variants:
            self.provider.batches['primary']=value
            with self.assertRaises(PolicyError):self.refresh(2)
            self.assertEqual(self.prior(),old)

    def test_revocation_after_source_stops_next_and_preserves_snapshot(self):
        def revoke(scope,source):
            self.store.set_membership('admin','alpha','developer',role='developer',state='revoked',expected_revision=1)
        self.provider.hook=revoke
        with self.assertRaises(PolicyError) as raised:self.refresh(token='synthetic-developer')
        self.assertEqual(raised.exception.status,404);self.assertEqual(len(self.provider.calls),1)
        self.assertEqual(self.get()['body']['data']['revision'],1)

    def test_expiry_during_source_prevents_publication(self):
        self.provider.hook=lambda scope,source:setattr(self,'now',NOW+timedelta(seconds=61))
        with self.assertRaises(AuthenticationError):self.refresh()
        self.assertEqual(len(self.provider.calls),1)
        self.assertEqual(self.sql('SELECT revision FROM research_catalog WHERE workspace_id=?',('alpha',)),[(1,)])

    def test_configuration_change_during_prepare_rejected(self):
        def mutate(scope,source):
            altered=replace(binding(),namespace='changed').encode()
            self.sql('UPDATE research_catalog SET binding=? WHERE workspace_id=?',(altered,'alpha'))
        self.provider.hook=mutate
        with self.assertRaises(PolicyError) as raised:self.refresh()
        self.assertEqual(str(raised.exception),'catalog_changed');self.assertEqual(len(self.provider.calls),1)

    def test_concurrent_publication_one_cas_winner(self):
        barrier=Barrier(2)
        self.provider.hook=lambda scope,source:barrier.wait(timeout=5) if source.id=='secondary' else None
        def work():
            try:return self.refresh()['revision']
            except PolicyError as e:return e.status
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:work(),range(2)))
        self.assertEqual(sorted(results),[2,412])
        self.assertEqual(len(self.sql("SELECT * FROM audit WHERE action='resource.update'")),1)

    def test_audit_failure_rolls_back_catalog_and_rows(self):
        self.sql("CREATE TRIGGER fail_asset_audit BEFORE INSERT ON audit WHEN NEW.action='resource.update' BEGIN SELECT RAISE(ABORT,'fail'); END")
        old=self.prior()
        with self.assertRaises(Exception):self.refresh()
        self.assertEqual(self.prior(),old)

    def test_malformed_stored_binding_does_not_become_empty_success(self):
        self.sql("UPDATE research_catalog SET binding='{}' WHERE workspace_id='alpha'")
        self.assertEqual(self.get()['status'],503)
        self.assertEqual(self.get(token='synthetic-other')['status'],404)

    def test_catalog_data_is_not_read_after_authorization_revoked(self):
        self.refresh();self.store.set_membership('admin','alpha','reader',role='viewer',state='revoked',expected_revision=1)
        with patch('packages.research_assets.application.query',side_effect=AssertionError('must not read')):
            self.assertEqual(self.get()['status'],404)

    def test_stored_unknown_fields_foreign_entity_and_digest_fail_closed(self):
        self.refresh()
        original=self.sql("SELECT id,summary,summary_digest FROM research_asset WHERE workspace_id='alpha'")[0]
        for mutate in (lambda s:s.update(source_path='private-marker'),
                       lambda s:s['entity'].update(id='foreign'),lambda s:s.update(title='different')):
            summary=json.loads(original[1]);mutate(summary)
            self.sql("UPDATE research_asset SET summary=? WHERE workspace_id='alpha'",(json.dumps(summary),))
            for endpoint in ('assets','assets/'+original[0]):
                result=self.get(endpoint)
                self.assertEqual(result['status'],503);self.assertNotIn('private-marker',str(result))
            self.sql("UPDATE research_asset SET summary=? WHERE workspace_id='alpha'",(original[1],))

    def test_stored_detail_shape_and_content_revision_fail_closed(self):
        self.refresh();asset_id=self.get()['body']['data']['items'][0]['id']
        old=self.sql("SELECT detail FROM research_asset WHERE workspace_id='alpha'")[0][0]
        for mutate in (lambda d:d.update(raw='private-marker'),lambda d:d['sections'][0].update(text='tampered')):
            detail=json.loads(old);mutate(detail)
            self.sql("UPDATE research_asset SET detail=? WHERE workspace_id='alpha'",(json.dumps(detail),))
            result=self.get('assets/'+asset_id)
            self.assertEqual(result['status'],503);self.assertNotIn('private-marker',str(result))
        # Summary reads do not load body content; a detail defect is not rendered there.
        self.assertEqual(self.get()['status'],200)

    def test_valid_row_accidentally_moved_from_other_scope_fails(self):
        self.refresh();self.refresh(scope=AssetScope('beta','engineering'))
        beta=self.sql("SELECT id,type_id,entity_id,title,search_text,summary,summary_digest,detail FROM research_asset WHERE workspace_id='beta'")[0]
        self.sql("DELETE FROM research_asset WHERE workspace_id='alpha'")
        self.sql("INSERT INTO research_asset VALUES(?,?,?,?,?,?,?,?,?,?)",('alpha','engineering',*beta))
        self.assertEqual(self.get()['status'],503)

    def test_missing_rows_or_incomplete_provider_never_delete_snapshot(self):
        self.refresh();old=self.prior()
        self.provider.batches['secondary']=replace(batch('secondary',[]),complete=False)
        with self.assertRaises(PolicyError):self.refresh(2)
        self.assertEqual(self.prior(),old)
        self.sql("DELETE FROM research_asset WHERE workspace_id='alpha'")
        self.assertEqual(self.get()['status'],503)

    def test_commit_guard_expiry_rolls_back_rows_and_audit(self):
        original=self.store._persist;old=self.prior()
        def expire(conn,service,*,audit_start):
            original(conn,service,audit_start=audit_start);self.now=NOW+timedelta(seconds=61)
        with patch.object(self.store,'_persist',side_effect=expire), self.assertRaises(AuthenticationError):self.refresh()
        self.assertEqual(self.prior(),old)

    def test_last_source_revocation_prevents_atomic_publication(self):
        old=self.sql('SELECT * FROM research_catalog'),self.sql('SELECT * FROM research_asset')
        def revoke(scope,source):
            if source.id=='secondary':self.store.set_membership('admin','alpha','developer',role='developer',state='revoked',expected_revision=1)
        self.provider.hook=revoke
        with self.assertRaises(PolicyError):self.refresh(token='synthetic-developer')
        self.assertEqual((self.sql('SELECT * FROM research_catalog'),self.sql('SELECT * FROM research_asset')),old)

    def test_module_disable_and_archived_sync_boundary(self):
        self.store.set_module_state('admin','alpha','research_assets',state='disabling',expected_revision=1)
        self.assertEqual(self.get()['status'],409)
        with self.assertRaises(PolicyError):self.refresh()
        self.assertEqual(self.provider.calls,[])


if __name__=='__main__':unittest.main()
