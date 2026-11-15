"""Synthetic-only identity; real SQLite policy, enrollment and audit transactions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import sqlite3
from packages.workspaces.tests.identity_fixture import simulate_identity
import unittest
from unittest.mock import patch
from uuid import uuid4

from packages.workspace_api.tests import test_application as fixtures
from packages.workspace_admin_api import WorkspaceAdministrationApplication
from packages.workspace_invitations import WorkspaceInvitationApplication, initialize_invitations
from packages.workspace_invitations.store import _CREATE
from packages.workspaces import StorageError, SQLiteWorkspaceService

LIST = '/api/workspaces/alpha/invitations'


class InvitationTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApplicationTests.setUp(self)
        self.base = self.app
        self.invitation_now = fixtures.NOW
        initialize_invitations(self.store)
        self.app = WorkspaceInvitationApplication(api=self.base,clock=lambda:self.invitation_now)

    def request(self,path=LIST,**kwargs):
        return fixtures.call(self.app,path,**kwargs)

    def invite(self,target='outsider',role='developer',key=None,**kwargs):
        return self.request(method='POST',body={'principal_id':target,'role':role},
                            extra={'HTTP_IDEMPOTENCY_KEY':key or str(uuid4())},**kwargs)

    def respond(self,item,state='accepted',token='test-outsider',revision=None):
        return self.request('/api/invitations/'+item['id'],method='PATCH',token=token,
                            body={'state':state},revision=revision or f'"{item["revision"]}"')

    def revoke(self,item,**kwargs):
        return self.request(LIST+'/'+item['id'],method='PATCH',body={'state':'revoked'},
                            revision=f'"{item["revision"]}"',**kwargs)

    def item(self,**kwargs):
        result=self.invite(**kwargs)
        self.assertEqual(result['status'],200,result)
        return result['body']['data']

    def counts(self):
        with sqlite3.connect(self.path) as db:
            return tuple(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                         for table in ('workspace_invitations_v1','memberships','audit'))

    def test_invitee_aba_and_reactivation_do_not_restore_invitation(self):
        for first, second in [("subject='replacement'", "subject='outsider-sub'"), ("active=0", "active=1")]:
            item = self.item()
            with sqlite3.connect(self.path) as db:
                simulate_identity(db, "UPDATE principals SET " + first + " WHERE id='outsider'")
                simulate_identity(db, "UPDATE principals SET " + second + " WHERE id='outsider'")
            self.assertEqual(self.respond(item)['status'],404)
            self.assertEqual(self.request('/api/invitations',token='test-outsider')['body']['data'],[])
            self.assertEqual(self.request()['body']['data'][-1]['state'],'invalidated')

    def test_inviter_aba_keeps_old_grant_invalidated(self):
        item = self.item()
        with sqlite3.connect(self.path) as db:
            simulate_identity(db, "UPDATE principals SET subject='replacement' WHERE id='admin'")
            simulate_identity(db, "UPDATE principals SET subject='admin-sub' WHERE id='admin'")
        self.assertEqual(self.respond(item)['status'],409)

    def test_legacy_identity_digest_is_invalidated_not_backfilled(self):
        from hashlib import sha256
        import json
        item = self.item()
        digest = sha256(json.dumps([fixtures.ISSUER, 'outsider-sub'], ensure_ascii=True, separators=(',', ':')).encode('ascii')).hexdigest()
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE workspace_invitations_v1 SET binding=? WHERE id=?',(digest,item['id']))
        self.assertEqual(self.respond(item)['status'],404)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT binding,state FROM workspace_invitations_v1 WHERE id=?',(item['id'],)).fetchone(),(digest,'pending'))

    def test_factory_and_explicit_initialization(self):
        for value in (None,object(),self.app):
            with self.assertRaises(ValueError):WorkspaceInvitationApplication(api=value)
        app=WorkspaceInvitationApplication(api=WorkspaceAdministrationApplication(api=self.base))
        self.assertEqual(fixtures.call(app,'/api/control/capabilities')['status'],200)
        initialize_invitations(self.store)
        with sqlite3.connect(self.path) as db:db.execute('DROP TABLE workspace_invitations_v1')
        self.assertEqual(self.invite()['status'],503)
        initialize_invitations(self.store)
        self.assertEqual(self.invite()['status'],200)

    def test_wrong_schema_rejected_without_weak_compatibility(self):
        for definition in [_CREATE.replace(' STRICT',''),_CREATE.replace(' NOT NULL',''),
                           _CREATE.replace("'pending'","'PENDING'"),_CREATE.replace(' REFERENCES principals(id)',''),
                           'CREATE TABLE workspace_invitations_v1(id)']:
            with sqlite3.connect(self.path) as db:
                db.execute('DROP TABLE workspace_invitations_v1');db.execute(definition)
            with self.assertRaises(StorageError):initialize_invitations(self.store)
            self.assertEqual(self.invite()['status'],503)

    def test_authentication_and_workspace_admin_required(self):
        for token,status in [(None,401),('bad',401),('test-disabled',401),('test-viewer',403),
                             ('test-other',404),('test-outsider',404)]:
            self.assertEqual(self.invite(token=token)['status'],status)
            self.assertEqual(self.request(token=token)['status'],status)
        self.assertEqual(self.request('/api/workspaces/beta/invitations')['status'],404) # operator is not admin
        self.assertEqual(self.request('/api/invitations',token='test-outsider')['body']['data'],[])

    def test_exact_existing_binding_and_no_admin_invites(self):
        for target in ['missing','disabled','person@example.invalid']:
            self.assertEqual(self.invite(target=target)['status'],400)
        for role in ['admin','owner','', 'Admin']:
            self.assertEqual(self.invite(role=role)['status'],400)
        self.assertEqual(self.invite(target='viewer')['status'],409)
        self.assertEqual(self.invite(target='admin')['status'],409)

    def test_no_membership_before_explicit_target_acceptance(self):
        item=self.item()
        self.assertEqual(self.request(fixtures.BASE+'/same',token='test-outsider')['status'],404)
        self.assertEqual(self.request('/api/workspaces',token='test-outsider')['body']['data'],[])
        inbox=self.request('/api/invitations',token='test-outsider')['body']['data']
        self.assertEqual(inbox,[item]);self.assertEqual(item['workspace_name'],'Alpha')
        self.assertEqual(set(item),{'id','workspace_id','principal_id','role','created','expires','revision','workspace_name','state'})
        self.assertEqual(item['expires']-item['created'],86400)
        for token in ['test-admin','test-viewer','test-other']:
            self.assertEqual(self.respond(item,token=token)['status'],404)
        self.assertEqual(self.respond(item)['status'],200)
        self.assertEqual(self.request(fixtures.BASE+'/same',token='test-outsider')['status'],200)
        self.assertEqual(self.request('/api/workspaces/beta/members',token='test-outsider')['status'],404)
        self.assertEqual(self.respond(item)['status'],412)
        self.assertEqual(self.respond(item,revision='"2"')['status'],409)
        self.assertEqual(self.request('/api/invitations',token='test-outsider')['body']['data'],[])
        self.assertEqual([e.action for e in self.store.audit_events('admin','alpha')],
                         ['invitation.create','membership.join','invitation.accepted'])

    def test_viewer_membership_cannot_write_and_shared_is_explicit(self):
        item=self.item(role='viewer');self.respond(item)
        result=self.request(fixtures.BASE+'/same',token='test-outsider',method='PUT',body={'text':'no'},revision='"1"')
        self.assertEqual(result['status'],403)
        self.assertEqual(self.request('/api/workspaces/commons/modules/board/resources/shared',token='test-outsider')['status'],404)
        item=self.request('/api/workspaces/commons/invitations',token='test-other',method='POST',
             body={'principal_id':'outsider','role':'viewer'},extra={'HTTP_IDEMPOTENCY_KEY':str(uuid4())})['body']['data']
        self.assertEqual(self.respond(item)['status'],200)
        self.assertEqual(self.request('/api/workspaces/commons/modules/board/resources/shared',token='test-outsider')['status'],200)

    def test_duplicate_creation_replay_does_not_extend_expiry_or_audit(self):
        key=str(uuid4());item=self.item(key=key);count=self.counts()
        self.invitation_now+=timedelta(hours=1)
        self.assertEqual(self.item(key=key),item);self.assertEqual(self.counts(),count)
        self.assertEqual(self.invite(key=key,role='viewer')['status'],409)
        self.assertEqual(self.invite()['status'],409)
        self.respond(item);self.assertEqual(self.item(key=key)['state'],'accepted')
        self.assertEqual(len(self.store.audit_events('admin','alpha')),3)

    def test_expiry_strict_at_boundary_and_new_invite_allowed(self):
        item=self.item();self.invitation_now+=timedelta(days=1)
        self.assertEqual(self.respond(item)['status'],409)
        self.assertEqual(self.request()['body']['data'][0]['state'],'expired')
        self.assertEqual(self.request('/api/invitations',token='test-outsider')['body']['data'],[])
        self.assertNotEqual(self.item()['id'],item['id'])

    def test_revoke_and_decline_are_terminal(self):
        item=self.item();self.assertEqual(self.revoke(item)['status'],200)
        self.assertEqual(self.respond(item)['status'],412)
        self.assertEqual(self.respond(item,revision='"2"')['status'],409)
        other=self.item();self.assertEqual(self.respond(other,'declined')['status'],200)
        self.assertEqual(self.respond(other,revision='"2"')['status'],409)
        self.assertNotIn('outsider',{m.principal_id for m in self.store.list_memberships('admin','alpha')})

    def test_membership_revocation_requires_new_invite_and_never_replays_old_accept(self):
        item=self.item();self.respond(item)
        self.store.set_membership('admin','alpha','outsider',role='viewer',state='revoked',expected_revision=1)
        self.assertEqual(self.respond(item,revision='"2"')['status'],409)
        next_item=self.item(role='viewer');self.assertEqual(self.respond(next_item)['status'],200)
        member=next(m for m in self.store.list_memberships('admin','alpha') if m.principal_id=='outsider')
        self.assertEqual((member.role,member.state,member.revision),('viewer','active',3))

    def test_inviter_demotion_and_restoration_invalidates_old_grant(self):
        item=self.item()
        self.store.set_membership('admin','alpha','viewer',role='admin',state='active',expected_revision=1)
        self.store.set_membership('viewer','alpha','admin',role='developer',state='active',expected_revision=1)
        self.assertEqual(self.respond(item)['status'],409)
        self.store.set_membership('viewer','alpha','admin',role='admin',state='active',expected_revision=2)
        self.assertEqual(self.respond(item)['status'],409)
        self.assertEqual(self.request()['body']['data'][0]['state'],'invalidated')
        self.assertEqual(self.respond(self.item())['status'],200)

    def test_lifecycle_change_invalidates_even_after_reactivation(self):
        item=self.item();self.store.set_workspace_state('admin','alpha',state='suspended',expected_revision=1)
        self.assertEqual(self.respond(item)['status'],409)
        self.assertEqual(self.invite()['status'],409)
        self.store.set_workspace_state('admin','alpha',state='active',expected_revision=2)
        self.assertEqual(self.respond(item)['status'],409)
        item2=self.item();self.store.set_workspace_state('admin','alpha',state='archived',expected_revision=3)
        self.assertEqual(self.respond(item2)['status'],409)
        self.assertEqual(self.revoke(item2)['status'],200)

    def test_identity_rebind_cannot_inherit_invitation(self):
        item=self.item()
        with sqlite3.connect(self.path) as db:simulate_identity(db, "UPDATE principals SET subject='replacement' WHERE id='outsider'")
        self.assertEqual(self.respond(item)['status'],401)
        # A fully verified replacement subject still cannot inherit old grant.
        from dataclasses import replace
        old=self.verifier.verify
        self.verifier.verify=lambda credential:replace(old(credential),subject='replacement')
        self.assertEqual(self.respond(item)['status'],404)
        self.assertEqual(self.request('/api/invitations',token='test-outsider')['body']['data'],[])

    def test_inactive_target_is_denied(self):
        item=self.item()
        with sqlite3.connect(self.path) as db:simulate_identity(db, "UPDATE principals SET active=0 WHERE id='outsider'")
        self.assertEqual(self.respond(item)['status'],401)
        self.assertEqual(self.request()['body']['data'][0]['state'],'invalidated')

    def test_replayed_create_rechecks_admin(self):
        key=str(uuid4());self.item(key=key)
        self.store.set_membership('admin','alpha','viewer',role='admin',state='active',expected_revision=1)
        self.store.set_membership('viewer','alpha','admin',role='viewer',state='active',expected_revision=1)
        self.assertEqual(self.invite(key=key)['status'],403)

    def test_concurrent_creation_and_acceptance_are_single_mutations(self):
        key=str(uuid4())
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(lambda _:self.invite(key=key),range(2)))
        self.assertEqual([r['status'] for r in responses],[200,200])
        self.assertEqual(responses[0]['body'],responses[1]['body'])
        item=responses[0]['body']['data']
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=sorted(pool.map(lambda _:self.respond(item)['status'],range(2)))
        self.assertEqual(statuses,[200,412]);self.assertEqual(self.counts(),(1,6,3))

    def test_concurrent_accept_and_revoke_has_one_winner(self):
        item=self.item()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(self.respond,item);b=pool.submit(self.revoke,item)
            self.assertEqual(sorted([a.result()['status'],b.result()['status']]),[200,412])
        state=self.request()['body']['data'][0]['state']
        self.assertEqual(self.request(fixtures.BASE+'/same',token='test-outsider')['status'],200 if state=='accepted' else 404)

    def test_durable_restart_and_no_plaintext_secrets(self):
        key=str(uuid4());item=self.item(key=key)
        self.store=SQLiteWorkspaceService(self.path)
        base=fixtures.WorkspaceApplication(store=self.store,providers=(self.provider,),provider_key='test',clock=lambda:self.now)
        self.app=WorkspaceInvitationApplication(api=base,clock=lambda:self.invitation_now)
        self.assertEqual(self.item(key=key),item);self.assertEqual(self.respond(item)['status'],200)
        raw=self.path.read_bytes()
        for value in (b'test-admin',b'test-outsider',b'Bearer '):self.assertNotIn(value,raw)

    def test_invitation_expiry_during_persistence_rolls_everything_back(self):
        item=self.item();counts=self.counts();persist=self.store._persist
        def slow(*args,**kwargs):
            persist(*args,**kwargs);self.invitation_now+=timedelta(days=1)
        with patch.object(self.store,'_persist',side_effect=slow):
            self.assertEqual(self.respond(item)['status'],409)
        self.assertEqual(self.counts(),counts)
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT state FROM workspace_invitations_v1').fetchone()[0],'pending')

    def test_assertion_expiry_during_persistence_rolls_everything_back(self):
        item=self.item();counts=self.counts();persist=self.store._persist
        def slow(*args,**kwargs):persist(*args,**kwargs);self.now+=timedelta(minutes=1)
        with patch.object(self.store,'_persist',side_effect=slow):self.assertEqual(self.respond(item)['status'],401)
        self.assertEqual(self.counts(),counts)

    def test_audit_failure_rolls_back_invitation_and_membership(self):
        item=self.item();counts=self.counts()
        with sqlite3.connect(self.path) as db:db.execute("CREATE TRIGGER reject_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        self.assertEqual(self.respond(item)['status'],503);self.assertEqual(self.counts(),counts)
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT state FROM workspace_invitations_v1').fetchone()[0],'pending')

    def test_create_audit_failure_rolls_back_pending_record(self):
        counts=self.counts()
        with sqlite3.connect(self.path) as db:db.execute("CREATE TRIGGER reject_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        self.assertEqual(self.invite()['status'],503);self.assertEqual(self.counts(),counts)

    def test_input_routes_preconditions_and_duplicate_fields_fail_closed(self):
        item=self.item()
        for path,kwargs,status in [
            (LIST,{'method':'POST','body':{'principal_id':'other','role':'viewer'}},400),
            (LIST,{'method':'POST','body':{'principal_id':'other','role':'viewer','state':'accepted'},'extra':{'HTTP_IDEMPOTENCY_KEY':str(uuid4())}},400),
            (LIST,{'query':'actor=admin'},400),
            ('/api/invitations/'+item['id'],{'method':'PATCH','body':{'state':'accepted'},'token':'test-outsider'},428),
            ('/api/invitations/'+item['id'],{'method':'PATCH','body':{'state':'active'},'token':'test-outsider','revision':'"1"'},400),
            (LIST+'/'+item['id'],{'method':'PATCH','body':{'state':'accepted'},'revision':'"1"'},400),
            ('/api/workspaces/beta/invitations/'+item['id'],{'method':'PATCH','body':{'state':'revoked'},'revision':'"1"','token':'test-other'},404),
            ('/api/invitations/'+item['id']+'/accept',{'method':'POST'},404),
        ]:
            self.assertEqual(self.request(path,**kwargs)['status'],status,(path,kwargs))
        result=self.request(LIST,method='POST',raw=b'{"principal_id":"other","role":"viewer","role":"admin"}',extra={'HTTP_IDEMPOTENCY_KEY':str(uuid4())})
        self.assertEqual(result['status'],400)

    def test_added_commit_guard_cannot_extend_credential_validity(self):
        item=self.item();counts=self.counts();calls=0
        def advancing_clock():
            nonlocal calls
            calls+=1
            if calls==2:self.now+=timedelta(minutes=1)
            return self.invitation_now
        self.app._clock=advancing_clock
        self.assertEqual(self.respond(item)['status'],401)
        self.assertEqual(self.counts(),counts)
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT state FROM workspace_invitations_v1').fetchone()[0],'pending')

    def test_inviter_binding_replacement_invalidates_old_grant(self):
        item=self.item()
        with sqlite3.connect(self.path) as db:simulate_identity(db, "UPDATE principals SET subject='replacement' WHERE id='admin'")
        self.assertEqual(self.respond(item)['status'],409)
        self.assertEqual(self.request('/api/invitations',token='test-outsider')['body']['data'],[])
