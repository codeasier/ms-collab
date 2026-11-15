# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Disposable identity/reference transitions; production mutation remains closed."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import itertools
import json
import sqlite3
import unittest
from threading import Event,current_thread
from unittest.mock import patch

from packages.community_api import regrant_policy as community_regrant
from packages.community_api.records import encode,fingerprint
from packages.community_api.tests import test_application as community_fixture
from packages.identity import Credential
from packages.responsibility_api import replace_policy
from packages.responsibility_api.records import _TABLE,_MAX,read
from packages.workspaces import PolicyError,StorageError
from packages.workspaces.tests.identity_fixture import simulate_identity
from packages.responsibility_api.tests import test_application as base


class AuthorityTests(unittest.TestCase):
    setUp=base.ResponsibilityTests.setUp
    cstate=base.ResponsibilityTests.cstate
    rstate=base.ResponsibilityTests.rstate
    request=base.ResponsibilityTests.request
    save=base.ResponsibilityTests.save
    evidence=base.ResponsibilityTests.evidence
    publish=base.ResponsibilityTests.publish
    count_audit=base.ResponsibilityTests.count_audit

    def rows(self):
        with sqlite3.connect(self.path) as db:
            return (db.execute(f'SELECT * FROM {_TABLE} ORDER BY workspace_id,connection_id,repository').fetchall(),
                    db.execute('SELECT * FROM audit ORDER BY sequence').fetchall())

    def change_identity(self,actor,*,active=False):
        with sqlite3.connect(self.path) as db:
            if active:
                simulate_identity(db,'UPDATE principals SET active=0 WHERE id=?',(actor,))
                simulate_identity(db,'UPDATE principals SET active=1 WHERE id=?',(actor,))
            else:
                simulate_identity(db,"UPDATE principals SET subject='temporary-subject' WHERE id=?",(actor,))
                simulate_identity(db,'UPDATE principals SET subject=? WHERE id=?',(actor,actor))

    def write_json(self,column,value):
        assert column in {'policy','fields','evidence'}
        with sqlite3.connect(self.path) as db:
            db.execute(f'UPDATE {_TABLE} SET {column}=? WHERE workspace_id=? AND connection_id=? AND repository=?',
                       (encode(value),*base.SCOPE.as_dict().values()))

    def json_columns(self):
        with sqlite3.connect(self.path) as db:
            row=db.execute(f'SELECT policy,fields,evidence FROM {_TABLE} WHERE workspace_id=? AND connection_id=? AND repository=?',
                           tuple(base.SCOPE.as_dict().values())).fetchone()
        return [json.loads(x) for x in row]

    def proposal(self,token='test-admin'):
        response=self.request(token=token)
        self.assertEqual(response['status'],200)
        return response['body']['data']

    def reconfirm(self,config=None,*,token='test-admin',**kwargs):
        current=config or self.proposal(token)
        return self.request('/reconfirm',method='POST',token=token,
            revision=kwargs.get('revision',current['revision']),
            body={'fields':kwargs.get('fields',current['fields']),
                  'confirmation_token':kwargs.get('confirmation_token',current['references']['confirmation_token'])})

    def current_admin_regrant(self,new=None,**kwargs):
        return replace_policy(self.store,new or base.policy(),actor_id=kwargs.get('actor_id','admin'),
                              expected_revision=kwargs.get('expected_revision',self.rstate()['revision']))

    def without_admin_owner(self):
        config=deepcopy(base.FIELDS);config[0]['owner_ids']=['dev'];return config

    def test_current_reference_response_is_strict_and_editor_token_not_reader_authority(self):
        self.assertEqual(self.save()['status'],200)
        editor=self.proposal();reader=self.proposal('test-reader')
        self.assertEqual(editor['references']['state'],'current');self.assertEqual(editor['references']['stale_ids'],[])
        self.assertRegex(editor['references']['confirmation_token'],r'^[0-9a-f]{64}$')
        self.assertIsNone(reader['references']['confirmation_token'])
        self.assertEqual(self.reconfirm(editor,token='test-reader')['status'],404)
        for key in ('issuer','subject','generation','memberships','authority'):
            self.assertNotIn('"'+key+'"',json.dumps(editor))

    def test_owner_committer_and_backup_identity_aba_hide_all_candidate_views(self):
        for actor in ('dev','reader','second'):
            with self.subTest(actor=actor):
                self.current_admin_regrant();self.assertEqual(self.save()['status'],200);self.publish()
                self.change_identity(actor)
                before=self.rows();data=self.proposal()
                self.assertEqual(data['references']['state'],'reconfirmation_required')
                self.assertIn(actor,data['references']['stale_ids'])
                for query in ('view=all','view=mine','view=field&field_id=ui','view=unassigned'):
                    response=self.request('/candidates',query=query,token='test-admin')
                    self.assertEqual(response['status'],409);self.assertNotIn('Synthetic UI',str(response['body']))
                self.assertEqual(self.rows(),before)

    def test_inactive_restored_owner_requires_explicit_confirmation(self):
        self.save();self.change_identity('dev',active=True)
        before=self.rows();self.assertEqual(self.save()['status'],409);self.assertEqual(self.rows(),before)
        proposal=self.proposal();self.assertEqual(self.reconfirm(proposal)['status'],200)
        state=self.rstate();self.assertEqual(state['history']['authority']['principals']['dev']['generation'],3)
        self.assertEqual(self.proposal()['references']['state'],'current')
        self.assertTrue(any(x.object_id.startswith('responsibility.references.reconfirm:') for x in self.store.audit_events('admin','alpha')))

    def test_plain_name_path_reorder_does_not_rebind_stale_owner(self):
        self.save();self.change_identity('dev')
        before=self.rows()
        for change in ('name','paths','order'):
            config=deepcopy(base.FIELDS)
            if change=='name':config[0]['name']='Changed'
            elif change=='paths':config[0]['paths']=['new/path']
            else:config[0]['owner_ids'].reverse()
            self.assertEqual(self.save(config)['status'],409)
            self.assertEqual(self.rows(),before)

    def test_remove_then_readd_across_fields_and_backup_preserves_retired_pin(self):
        self.save();old=deepcopy(self.rstate()['history'])
        removed=deepcopy(base.FIELDS);removed[0]['owner_ids']=['admin']
        self.assertEqual(self.save(removed)['status'],200);self.change_identity('dev')
        before=self.rows()
        variants=[deepcopy(base.FIELDS),deepcopy(removed),deepcopy(removed)]
        variants[1][2]['owner_ids']=['dev']
        variants[2].append(dict(id='extra',name='Extra',kind='developer',paths=['extra'],owner_ids=['dev'],backup=None))
        variants[2][0]['backup']={'principal_id':'dev','field_id':'extra'}
        for value in variants:
            self.assertEqual(self.save(value)['status'],409);self.assertEqual(self.rows(),before)
        self.assertEqual(self.rstate()['history'],old)

    def test_new_nomination_captures_but_never_grants_permissions(self):
        self.save()
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO memberships VALUES ('alpha','outsider','viewer','active',1)")
        config=deepcopy(base.FIELDS);config[2]['owner_ids'].append('outsider')
        self.assertEqual(self.save(config)['status'],200)
        self.assertEqual(self.rstate()['history']['authority']['principals']['outsider']['generation'],1)
        self.assertEqual(self.request(token='test-outsider')['status'],404)
        self.assertEqual(self.reconfirm(self.proposal(),token='test-outsider')['status'],404)

    def test_owner_member_revision_reduction_restore_requires_reconfirmation(self):
        self.save()
        self.store.set_membership('admin','alpha','dev',role='viewer',state='active',expected_revision=1)
        self.store.set_membership('admin','alpha','dev',role='developer',state='active',expected_revision=2)
        self.assertEqual(self.proposal()['references']['stale_ids'],['dev'])
        self.assertEqual(self.reconfirm()['status'],200)
        self.assertEqual(self.rstate()['history']['memberships']['dev'],3)

    def test_revoked_owner_cannot_reconfirm_but_can_be_deliberately_replaced(self):
        self.save();self.store.set_membership('admin','alpha','dev',role='developer',state='revoked',expected_revision=1)
        proposal=self.proposal();self.assertIsNone(proposal['references']['confirmation_token'])
        self.assertEqual(self.reconfirm(proposal,confirmation_token='a'*64)['status'],409)
        config=deepcopy(base.FIELDS);config[0]['owner_ids']=['admin']
        self.assertEqual(self.save(config)['status'],200)
        self.assertIn('dev',self.rstate()['history']['memberships'])

    def test_stale_reader_editor_publisher_grants_never_inherit_current_id(self):
        for actor in ('reader','admin'):
            with self.subTest(actor=actor):
                # Give the community layer a NEW explicit grant after ABA so its
                # independent denial cannot mask responsibility's own stale ACL.
                self.change_identity(actor)
                community_regrant(self.store,actor_id='admin',config=community_fixture.config(),expected_revision=self.cstate().revision)
                self.assertEqual(self.request(token='test-'+actor)['status'],404)
                if actor=='admin':
                    self.assertEqual(self.save()['status'],404)
                    with self.assertRaises(PolicyError) as error:self.publish(records=[])
                    self.assertEqual(error.exception.status,404)
                self.current_admin_regrant()
                self.assertEqual(self.request(token='test-'+actor)['status'],200)

    def test_legacy_stays_inaccessible_unchanged_until_explicit_destructive_reset(self):
        self.save();self.publish();state=self.rstate()
        for column,value in zip(('policy','fields','evidence'),(state['policy'].as_dict(),state['fields'],state['evidence'])):self.write_json(column,value)
        old=self.rows()
        self.assertEqual(self.request()['status'],503);self.assertEqual(self.request(method='PUT',token='test-admin',revision=state['revision'],body={'fields':deepcopy(base.FIELDS)})['status'],503)
        self.assertEqual(self.rows(),old)
        for actor,revision in (('reader',state['revision']),('admin',state['revision']-1)):
            with self.assertRaises(PolicyError):replace_policy(self.store,base.policy(),actor_id=actor,expected_revision=revision)
            self.assertEqual(self.rows(),old)
        replace_policy(self.store,base.policy(),actor_id='admin',expected_revision=state['revision'])
        current=self.rstate();self.assertEqual(current['grant_revision'],1)
        self.assertEqual(current['fields'],[]);self.assertEqual(current['evidence'],[])
        self.assertEqual(current['config_version'],state['config_version']+1)
        self.assertTrue(any(e.object_id.startswith('responsibility.grants.reset:') for e in self.store.audit_events('admin','alpha')))

    def test_all_six_mixed_formats_refuse_reads_and_recovery_without_writes(self):
        self.save();self.publish();state=self.rstate();bound=self.json_columns()
        legacy=[state['policy'].as_dict(),state['fields'],state['evidence']]
        for mask in itertools.product((False,True),repeat=3):
            if len(set(mask))==1:continue
            with self.subTest(mask=mask):
                for i,key in enumerate(('policy','fields','evidence')):self.write_json(key,bound[i] if mask[i] else legacy[i])
                old=self.rows();self.assertEqual(self.request()['status'],503)
                with self.assertRaises(StorageError):replace_policy(self.store,base.policy(),actor_id='admin',expected_revision=state['revision'])
                self.assertEqual(self.rows(),old)
        for key,value in zip(('policy','fields','evidence'),bound):self.write_json(key,value)

    def test_reconfirmation_rejects_changed_fields_or_record_and_never_retries(self):
        self.save();proposal=self.proposal();before=self.rows()
        changed=deepcopy(proposal['fields']);changed[0]['name']='Not the reviewed content'
        self.assertEqual(self.reconfirm(proposal,fields=changed)['status'],412);self.assertEqual(self.rows(),before)
        self.assertEqual(self.reconfirm(proposal,revision=1)['status'],412);self.assertEqual(self.rows(),before)
        self.assertEqual(self.reconfirm(proposal,confirmation_token='f'*64)['status'],412);self.assertEqual(self.rows(),before)

    def test_proposal_pins_editor_nonowner_role_aba(self):
        self.save(self.without_admin_owner());proposal=self.proposal()
        self.store.set_membership('second','alpha','admin',role='developer',state='active',expected_revision=1)
        self.store.set_membership('second','alpha','admin',role='admin',state='active',expected_revision=2)
        before=self.rows();self.assertEqual(self.reconfirm(proposal)['status'],412);self.assertEqual(self.rows(),before)
        self.assertEqual(self.reconfirm()['status'],200)

    def test_proposal_pins_both_module_cycles_workspace_and_community_regrant(self):
        with sqlite3.connect(self.path) as db:db.execute("INSERT INTO operators VALUES ('admin')")
        self.save()
        for key in ('responsibility','community','workspace','community-grant'):
            with self.subTest(key=key):
                proposal=self.proposal()
                if key=='workspace':
                    revision=self.store._transaction(lambda p:p._workspaces['alpha'].revision,write=False)
                    self.store.set_workspace_state('admin','alpha',state='suspended',expected_revision=revision)
                    self.store.set_workspace_state('admin','alpha',state='active',expected_revision=revision+1)
                elif key=='community-grant':
                    community_regrant(self.store,actor_id='admin',config=community_fixture.config(),expected_revision=self.cstate().revision)
                else:
                    revision=self.store.get_module('admin','alpha',key).revision
                    for offset,state in enumerate(('disabling','disabled','enabled')):
                        self.store.set_module_state('admin','alpha',key,state=state,expected_revision=revision+offset)
                before=self.rows();self.assertEqual(self.reconfirm(proposal)['status'],412);self.assertEqual(self.rows(),before)

    def test_target_aba_during_confirmation_invalidates_proposal(self):
        self.save();proposal=self.proposal();self.change_identity('dev')
        before=self.rows();self.assertEqual(self.reconfirm(proposal)['status'],412);self.assertEqual(self.rows(),before)
        self.assertEqual(self.reconfirm()['status'],200)

    def test_empty_configuration_token_is_bound_to_realm_and_actor(self):
        first=self.proposal();self.assertEqual(first['fields'],[])
        other=AuthorityTests();other.setUp();self.addCleanup(other.doCleanups)
        second=other.proposal();self.assertNotEqual(first['references']['confirmation_token'],second['references']['confirmation_token'])
        before=other.rows();self.assertEqual(other.reconfirm(first)['status'],412);self.assertEqual(other.rows(),before)
        self.current_admin_regrant(replace(base.policy(),reader_ids=('admin','second'),editor_ids=('admin','second')))
        proposal=self.proposal();self.assertEqual(self.reconfirm(proposal,token='test-second')['status'],404) # community reader is still separate
        community_regrant(self.store,actor_id='admin',config=replace(community_fixture.config(),reader_ids=('admin','second')),expected_revision=self.cstate().revision)
        proposal=self.proposal();self.assertEqual(self.reconfirm(proposal,token='test-second')['status'],412)

    def test_concurrent_reconfirmation_has_one_winner_and_one_metadata_audit(self):
        self.save();self.change_identity('dev');proposal=self.proposal();before=self.count_audit()
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=sorted(pool.map(lambda _:self.reconfirm(proposal)['status'],range(2)))
        self.assertEqual(statuses,[200,412]);self.assertEqual(self.count_audit(),before+1)

    def test_reconfirmation_audit_failure_and_expiry_rollback_every_value(self):
        self.save();self.change_identity('dev');proposal=self.proposal();before=self.rows()
        with sqlite3.connect(self.path) as db:db.execute("CREATE TRIGGER fail_reference_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT,'private-test-marker'); END")
        response=self.reconfirm(proposal);self.assertEqual(response['status'],503);self.assertNotIn('private-test-marker',str(response));self.assertEqual(self.rows(),before)
        with sqlite3.connect(self.path) as db:db.execute('DROP TRIGGER fail_reference_audit')
        original=self.store._persist
        def expire(*args,**kwargs):original(*args,**kwargs);self.now+=timedelta(minutes=1)
        with patch.object(self.store,'_persist',side_effect=expire):self.assertEqual(self.reconfirm(proposal)['status'],401)
        self.assertEqual(self.rows(),before)

    def test_destructive_reset_audit_failure_preserves_legacy_and_pins(self):
        self.save();self.publish();before=self.rows()
        with sqlite3.connect(self.path) as db:db.execute("CREATE TRIGGER fail_reset_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT,'private-test-marker'); END")
        with self.assertRaises(StorageError):self.current_admin_regrant()
        self.assertEqual(self.rows(),before)

    def test_publisher_provenance_invalidates_paths_without_rebinding_or_erasing(self):
        # Independent community approver isolates responsibility publisher fence.
        community_regrant(self.store,actor_id='admin',config=replace(community_fixture.config(),reader_ids=('admin','reader','dev','second'),approver_ids=('second',)),expected_revision=self.cstate().revision)
        self.assertEqual(community_fixture.call(self.community,'/refresh',method='POST',token='test-admin',revision=self.cstate().revision,body={})['status'],200)
        self.assertEqual(community_fixture.call(self.community,'/approve',method='POST',token='test-second',revision=self.cstate().revision,body={'fingerprints':[fingerprint(base.SCOPE,i) for i in self.cstate().snapshot.items if i.number!=3]})['status'],200)
        self.save(self.without_admin_owner());self.publish();before=self.rstate()['evidence']
        self.change_identity('admin')
        result=self.request('/candidates',query='view=all')['body']['data']
        self.assertFalse(result['evidence_current']);self.assertIsNone(result['evidence_observed_at'])
        self.assertEqual(result['items'][1]['assignment_status'],'incomplete');self.assertEqual(result['items'][1]['unmapped_paths'],[])
        self.assertEqual(self.rstate()['evidence'],before)

    def test_reference_reconfirmation_does_not_rebind_publisher_provenance(self):
        self.save();self.publish();proof=deepcopy(self.rstate()['provenance']);records=deepcopy(self.rstate()['evidence'])
        self.change_identity('dev');self.assertEqual(self.reconfirm()['status'],200)
        self.assertEqual(self.rstate()['provenance'],proof);self.assertEqual(self.rstate()['evidence'],records)

    def test_strict_envelopes_scope_scalars_canonical_form_and_limits(self):
        self.save();self.publish();original=self.json_columns()
        cases=[]
        v=deepcopy(original[0]);v['version']=True;cases.append(('policy',v))
        v=deepcopy(original[0]);v['grant_revision']=self.rstate()['revision']+1;cases.append(('policy',v))
        v=deepcopy(original[1]);v['scope']['workspace_id']='beta';cases.append(('fields',v))
        v=deepcopy(original[1]);v['history']['memberships']['dev']=True;cases.append(('fields',v))
        v=deepcopy(original[1]);v['history']['authority']['principals']['dev']['generation']=True;cases.append(('fields',v))
        v=deepcopy(original[1]);del v['history']['memberships']['dev'];cases.append(('fields',v))
        v=deepcopy(original[2]);v['scope']['connection_id']='other';cases.append(('evidence',v))
        v=deepcopy(original[2]);v['provenance']['authority']['realm']='a'*32;cases.append(('evidence',v))
        for column,value in cases:
            with self.subTest(column=column):
                self.write_json(column,value);self.assertEqual(self.request()['status'],503)
                for key,raw in zip(('policy','fields','evidence'),original):self.write_json(key,raw)
        with sqlite3.connect(self.path) as db:db.execute(f'UPDATE {_TABLE} SET fields=fields||? WHERE workspace_id=?',(' ','alpha'))
        self.assertEqual(self.request()['status'],503)

    def test_revision_exhaustion_refuses_save_reconfirm_publish_reset_without_effects(self):
        self.save();proposal=self.proposal()
        with sqlite3.connect(self.path) as db:db.execute(f'UPDATE {_TABLE} SET revision=?,config_version=? WHERE workspace_id=? AND connection_id=?',(_MAX,_MAX,'alpha','source'))
        before=self.rows();current=self.proposal()
        self.assertEqual(self.save(revision=_MAX)['status'],409)
        self.assertEqual(self.reconfirm(current)['status'],409)
        with self.assertRaises(PolicyError):self.publish(expected_revision=_MAX)
        with self.assertRaises(PolicyError):self.current_admin_regrant(expected_revision=_MAX)
        self.assertEqual(self.rows(),before)

    def test_retired_history_capacity_is_not_silently_pruned(self):
        self.save();raw=self.json_columns()[1];history=raw['history']
        for n in range(3000-len(history['memberships'])):
            ident='retired-'+str(n)
            history['memberships'][ident]=1
            history['authority']['principals'][ident]={'issuer':community_fixture.ISSUER,'subject':ident,'generation':1}
        self.write_json('fields',raw)
        with sqlite3.connect(self.path) as db:db.execute("INSERT INTO memberships VALUES ('alpha','outsider','viewer','active',1)")
        before=self.rows();config=deepcopy(base.FIELDS);config[2]['owner_ids'].append('outsider')
        self.assertEqual(self.save(config)['status'],409);self.assertEqual(self.rows(),before)
        raw['history']['memberships']['one-too-many']=1
        raw['history']['authority']['principals']['one-too-many']={'issuer':community_fixture.ISSUER,'subject':'one-too-many','generation':1}
        self.write_json('fields',raw);self.assertEqual(self.request()['status'],503)

    def test_reconfirmation_serialized_capacity_conflict_preserves_retired_history(self):
        self.save();raw=self.json_columns()[1];history=raw['history'];limit=1024*1024
        # Valid retired pins can approach the byte cap with fewer than 3000 IDs.
        for n in range(1000):
            ident='capacity-retired-'+str(n)
            history['memberships'][ident]=1
            history['authority']['principals'][ident]={'issuer':'x'*200,'subject':'😀'*200,'generation':1}
            try:encode(raw)
            except ValueError:
                del history['memberships'][ident];del history['authority']['principals'][ident]
                break
        else:self.fail('synthetic history did not reach the explicit byte bound')
        history['memberships']['capacity-tail']=1
        history['authority']['principals']['capacity-tail']={'issuer':'x','subject':'x','generation':1}
        available=limit-len(encode(raw))-100
        self.assertGreaterEqual(available,0)
        history['authority']['principals']['capacity-tail']['subject']='😀'*min(available//12,200) or 'x'
        self.write_json('fields',raw)
        with sqlite3.connect(self.path) as db:
            simulate_identity(db,'UPDATE principals SET subject=? WHERE id=?',('😀'*200,'dev'))
        proposal=self.proposal();self.assertIsNotNone(proposal['references']['confirmation_token'])
        before=self.rows();response=self.reconfirm(proposal)
        self.assertEqual(response['status'],409)
        self.assertEqual(response['body'],{'error':'reference_history_exhausted'})
        self.assertEqual(self.rows(),before)

    def test_foreign_realm_all_bound_record_never_grants_current_actor(self):
        self.save();self.publish()
        for key,value in zip(('policy','fields','evidence'),self.json_columns()):
            if key=='policy':value['authority']['realm']='a'*32
            elif key=='fields':value['history']['authority']['realm']='a'*32
            else:value['provenance']['authority']['realm']='a'*32
            self.write_json(key,value)
        before=self.rows();self.assertEqual(self.request(token='test-admin')['status'],404)
        self.assertEqual(self.rows(),before)
        self.current_admin_regrant();self.assertEqual(self.proposal()['fields'],[])

    def test_writer_wait_rechecks_editor_aba_before_reconfirmation(self):
        self.save(self.without_admin_owner());proposal=self.proposal();prior=self.rstate()
        begin=Event();original=self.store._connect
        def connect():
            db=original()
            if current_thread().name.startswith('reference-writer'):
                db.set_trace_callback(lambda sql:begin.set() if sql=='BEGIN IMMEDIATE' else None)
            return db
        future=[]
        with patch.object(self.store,'_connect',side_effect=connect),ThreadPoolExecutor(max_workers=1,thread_name_prefix='reference-writer') as pool:
            def change(policy):
                policy.set_membership('second','alpha','admin',role='developer',state='active',expected_revision=1)
                policy.set_membership('second','alpha','admin',role='admin',state='active',expected_revision=2)
                future.append(pool.submit(self.reconfirm,proposal))
                self.assertTrue(begin.wait(5))
            self.store._transaction(change,write=True)
            self.assertEqual(future[0].result(timeout=5)['status'],412)
        self.assertEqual(self.rstate(),prior)

    def test_size_and_exact_version_reject_before_projection(self):
        self.save();original=self.json_columns()[1]
        for version in (True,False,1,0,2.0,'2',None):
            raw=deepcopy(original);raw['version']=version;self.write_json('fields',raw)
            self.assertEqual(self.request()['status'],503)
        self.write_json('fields',original)
        with sqlite3.connect(self.path) as db:db.execute(f'UPDATE {_TABLE} SET fields=? WHERE workspace_id=?',(' '*(1024*1024+1),'alpha'))
        response=self.request();self.assertEqual(response['status'],503);self.assertLess(len(json.dumps(response['body'])),100)

    def test_runtime_identity_mutation_and_last_admin_controls_unchanged(self):
        with self.assertRaises(PolicyError) as error:self.store.set_principal('admin','dev',active=False)
        self.assertEqual(error.exception.code,'identity_transition_unavailable')
        with sqlite3.connect(self.path) as db:
            with self.assertRaises(sqlite3.IntegrityError):db.execute("UPDATE principals SET active=0 WHERE id='dev'")
        self.store.set_membership('admin','alpha','second',role='developer',state='active',expected_revision=1)
        with self.assertRaises(PolicyError) as error:
            self.store.set_membership('admin','alpha','admin',role='developer',state='active',expected_revision=1)
        self.assertEqual(error.exception.code,'last_admin')
        before=self.store.list_memberships('admin','alpha')
        self.current_admin_regrant();self.assertEqual(self.store.list_memberships('admin','alpha'),before)


if __name__=='__main__':unittest.main()
