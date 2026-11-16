"""Membership re-enrollment and invitation counter budgets use synthetic stores."""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import sqlite3
from packages.workspaces.tests.identity_fixture import bind_core_grants_for_test
from threading import Barrier
import unittest
from unittest.mock import patch

from packages.workspace_invitations.tests import test_application as invitation_fixtures
from packages.workspaces import SQLiteWorkspaceService
from packages.workspaces.model import MAX_REVISION as MAX
from packages.workspaces.tests.test_authority import snapshot
from packages.workspaces.tests.test_counter_bounds import CountedAudit


class InvitationCounterTests(unittest.TestCase):
    setUp=invitation_fixtures.InvitationTests.setUp
    request=invitation_fixtures.InvitationTests.request
    invite=invitation_fixtures.InvitationTests.invite
    respond=invitation_fixtures.InvitationTests.respond
    revoke=invitation_fixtures.InvitationTests.revoke
    item=invitation_fixtures.InvitationTests.item

    def revoked_member(self, value):
        with closing(sqlite3.connect(self.path,isolation_level=None)) as db:
            db.execute("INSERT INTO memberships VALUES('alpha','outsider','viewer','revoked',?)",(value,))
            bind_core_grants_for_test(db, ('outsider',))

    def test_reenrollment_uses_last_increment_once_and_restart_preserves_it(self):
        self.revoked_member(MAX-1);item=self.item()
        self.assertEqual(self.respond(item)['status'],200)
        reopened=SQLiteWorkspaceService(self.path)
        member=next(x for x in reopened.list_memberships('admin','alpha') if x.principal_id=='outsider')
        self.assertEqual((member.state,member.role,member.revision),('active','developer',MAX))
        before=snapshot(self.path)
        self.assertEqual(self.respond(item)['status'],412)
        result=self.request('/api/workspaces/alpha/members/outsider',method='PATCH',body={'role':'viewer','state':'revoked'},revision=f'"{MAX}"')
        self.assertEqual(result['status'],409,result)
        self.assertEqual(snapshot(self.path),before)

    def test_membership_max_prevents_accept_but_not_explicit_decline(self):
        self.revoked_member(MAX);item=self.item();before=snapshot(self.path)
        result=self.respond(item)
        self.assertEqual((result['status'],result['body']), (409,{'error':'counter_exhausted'}))
        self.assertEqual(snapshot(self.path),before)
        self.assertEqual(self.respond(item,'declined')['status'],200)
        self.assertEqual(next(x for x in self.store.list_memberships('admin','alpha') if x.principal_id=='outsider').state,'revoked')

    def test_invitation_revision_budget_precedes_membership_and_audits(self):
        for value in (MAX-1,MAX):
            with self.subTest(value=value):
                item=self.item()
                with closing(sqlite3.connect(self.path,isolation_level=None)) as db:
                    db.execute('UPDATE workspace_invitations_v1 SET revision=? WHERE id=?',(value,item['id']))
                item['revision']=value;before=snapshot(self.path)
                result=self.respond(item)
                if value==MAX-1:
                    self.assertEqual(result['status'],200,result)
                    self.assertEqual(result['body']['data']['revision'],MAX)
                    self.store.set_membership('admin','alpha','outsider',role='viewer',state='revoked',expected_revision=1)
                else:
                    self.assertEqual((result['status'],result['body']),(409,{'error':'counter_exhausted'}))
                    self.assertEqual(snapshot(self.path),before)
                    self.assertEqual(self.revoke(item)['status'],409)
                    self.assertEqual(snapshot(self.path),before)

    def test_epoch_and_two_audit_budget_are_preflighted_before_acceptance(self):
        item=self.item();before=snapshot(self.path)
        original=SQLiteWorkspaceService._load
        def load(store,db):
            policy=original(store,db);policy._audit=CountedAudit(MAX-1);return policy
        with patch.object(SQLiteWorkspaceService,'_load',load):
            result=self.respond(item)
        self.assertEqual((result['status'],result['body']),(409,{'error':'counter_exhausted'}))
        self.assertEqual(snapshot(self.path),before)
        with closing(sqlite3.connect(self.path,isolation_level=None)) as db:
            db.execute('UPDATE metadata SET epoch=?',(MAX,))
        before=snapshot(self.path)
        self.assertEqual(self.respond(item)['status'],409)
        self.assertEqual(snapshot(self.path),before)
        self.assertEqual(self.respond(item,'declined')['status'],200)

    def test_invalid_persisted_revision_coordinates_never_echo_or_repair(self):
        item=self.item()
        for column in ('revision','workspace_revision','inviter_revision','member_revision'):
            with closing(sqlite3.connect(self.path,isolation_level=None)) as db:
                old=db.execute(f'SELECT {column} FROM workspace_invitations_v1 WHERE id=?',(item['id'],)).fetchone()[0]
                db.execute(f'UPDATE workspace_invitations_v1 SET {column}=? WHERE id=?',(MAX+1,item['id']))
            before=snapshot(self.path)
            for operation in (self.request,lambda:self.request('/api/invitations',token='test-outsider'),lambda:self.respond(item)):
                result=operation();self.assertEqual((result['status'],result['body']),(503,{'error':'storage_unavailable'}))
                self.assertEqual(snapshot(self.path),before)
            with closing(sqlite3.connect(self.path,isolation_level=None)) as db:
                db.execute(f'UPDATE workspace_invitations_v1 SET {column}=? WHERE id=?',(old,item['id']))

    def test_audit_failure_and_final_expiry_restore_invitation_membership_epoch(self):
        self.revoked_member(MAX-1);item=self.item()
        with closing(sqlite3.connect(self.path,isolation_level=None)) as db:
            db.execute('UPDATE metadata SET epoch=?',(MAX-1,))
        before=snapshot(self.path)
        original=SQLiteWorkspaceService._persist
        def expire(db,policy,*,audit_start):
            original(db,policy,audit_start=audit_start)
            self.invitation_now+=timedelta(days=2)
        with patch.object(SQLiteWorkspaceService,'_persist',staticmethod(expire)):
            self.assertEqual(self.respond(item)['status'],409)
        self.assertEqual(snapshot(self.path),before)
        self.invitation_now-=timedelta(days=2)
        with closing(sqlite3.connect(self.path,isolation_level=None)) as db:
            db.execute("CREATE TRIGGER fail_join_audit BEFORE INSERT ON audit WHEN NEW.action='invitation.accepted' BEGIN SELECT RAISE(ABORT,'synthetic private marker'); END")
        before=snapshot(self.path)
        result=self.respond(item)
        self.assertEqual((result['status'],result['body']),(503,{'error':'storage_unavailable'}))
        self.assertEqual(snapshot(self.path),before)

    def test_concurrent_acceptance_at_last_member_increment_has_one_winner(self):
        self.revoked_member(MAX-1);item=self.item();barrier=Barrier(2)
        def accept():
            barrier.wait(timeout=5)
            return self.respond(item)['status']
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:accept(),range(2)))
        self.assertCountEqual(results,[200,412])
        self.assertEqual([x.action for x in self.store.audit_events('admin','alpha')],
                         ['invitation.create','membership.join','invitation.accepted'])

    def test_if_match_exact_safe_integer_limit(self):
        item=self.item();before=snapshot(self.path)
        for value in (f'"{MAX+1}"','"9999999999999999"','"true"','"1.0"'):
            result=self.respond(item,revision=value)
            self.assertEqual((result['status'],result['body']),(400,{'error':'invalid_revision'}))
            self.assertEqual(snapshot(self.path),before)
        self.assertEqual(self.respond(item,revision=f'"{MAX}"')['status'],412)


if __name__=='__main__':unittest.main()
