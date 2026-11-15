# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Synthetic identity/SQLite only. No agent, launcher, endpoint or transport."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier, Thread
import unittest
from unittest.mock import patch

from packages.assistant_history import create_conversation, initialize as history_initialize
from packages.assistant_protocol import ExecutionPreflight, initialize
from packages.assistant_protocol.preflight import _audit
from packages.identity import AuthenticationError, Provider, VerifiedIdentity
from packages.workspace_api import WorkspaceApplication
from packages.workspaces import Principal, Workspace, Membership, ModuleInstance, SQLiteWorkspaceService, PolicyError, StorageError

NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
ISSUER = 'https://identity.example.invalid'

class Verifier:
    def verify(self, credential):
        if credential.value not in {'test-alice', 'test-bob', 'test-admin', 'test-operator'}:
            raise AuthenticationError()
        return VerifiedIdentity(ISSUER, credential.value[5:], 'preflight-tests', NOW, NOW+timedelta(minutes=1))

class PreflightTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)/'policy.sqlite'
        self.store = SQLiteWorkspaceService.initialize(self.path,
            principals=tuple(Principal(p, ISSUER, p) for p in ('alice', 'bob', 'admin', 'operator')),
            workspaces=tuple(Workspace(w, w, 'shared' if w == 'commons' else 'product') for w in ('alpha', 'beta', 'commons')),
            memberships=tuple(Membership(w, p, 'admin' if p == 'admin' else 'developer') for w in ('alpha', 'beta') for p in ('alice', 'bob', 'admin')),
            modules=tuple(ModuleInstance(w, m, ('read', 'write')) for w in ('alpha', 'beta', 'commons') for m in ('assistant', 'pipelines')),
            operators=frozenset({'operator'}))
        history_initialize(self.store); initialize(self.store); self.now = NOW
        self.boundary = WorkspaceApplication(store=self.store, providers=(Provider('test', ISSUER, 'preflight-tests', Verifier()),), provider_key='test', clock=lambda:self.now)
        self.preflight = ExecutionPreflight(boundary=self.boundary)
        for w in ('alpha', 'beta'):
            create_conversation(self.store, owner='alice', workspace=w, conversation='one', title='Private title never in audit', required_modules=('assistant', 'pipelines'), clock=lambda:NOW)
    def assess(self, token='test-alice', **kw):
        values = dict(workspace='alpha', conversation='one', operation='op-1', expected_revision=1); values.update(kw)
        return self.preflight.assess({'REQUEST_METHOD':'POST', 'HTTP_AUTHORIZATION':'Bearer '+token}, **values)
    def sql(self, query, values=()):
        with sqlite3.connect(self.path) as c:return c.execute(query, values).fetchall()
    def audit_count(self):return self.sql("SELECT COUNT(*) FROM audit WHERE object_id LIKE 'assistant-preflight:%'")[0][0]
    def test_authorized_request_is_terminal_blocked_not_launch_authority(self):
        result=self.assess()
        self.assertEqual(set(result), {'receipt','state','reason','execution_enabled','launch_permitted'})
        self.assertEqual((result['state'], result['reason']), ('blocked','isolation_not_accepted'))
        self.assertIs(result['execution_enabled'],False);self.assertIs(result['launch_permitted'],False)
        self.assertRegex(result['receipt'],r'\A[0-9a-f]{64}\Z')
        self.assertFalse(callable(self.preflight));self.assertFalse(hasattr(self.preflight,'run'))
    def test_idempotency_does_not_duplicate_audit_or_queue_work(self):
        first=self.assess();self.assertEqual(first,self.assess());self.assertEqual(self.audit_count(),1)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM assistant_execution_preflights'),[(1,)])
        self.assertEqual(self.sql('SELECT event_count,revision FROM assistant_conversations WHERE workspace_id=\'alpha\''),[(0,1)])
    def test_no_owner_override_even_admin_operator_or_shared_space(self):
        for actor in ('test-bob','test-admin','test-operator'):
            with self.assertRaises(PolicyError):self.assess(actor)
        with self.assertRaises(PolicyError):self.assess(workspace='commons')
        self.assertEqual(self.audit_count(),0)
        with self.assertRaises(TypeError):self.assess(owner='bob')
    def test_same_operation_is_exact_scope_and_payload_bound(self):
        a=self.assess();b=self.assess(workspace='beta');self.assertNotEqual(a['receipt'],b['receipt'])
        create_conversation(self.store,owner='alice',workspace='alpha',conversation='two',title='second',clock=lambda:NOW)
        with self.assertRaisesRegex(PolicyError,'operation_conflict'):self.assess(conversation='two')
        self.assertEqual(self.audit_count(),2)
    def test_current_authorization_is_checked_on_idempotent_retry(self):
        self.assess();self.store.set_membership('admin','alpha','alice',role='developer',state='revoked',expected_revision=1)
        with self.assertRaises(PolicyError):self.assess()
        self.assertEqual(self.audit_count(),1)
    def test_dependency_epoch_change_does_not_resurrect_receipt(self):
        self.assess()
        self.sql("UPDATE modules SET revision=revision+1 WHERE workspace_id='alpha' AND key='pipelines'")
        with self.assertRaisesRegex(PolicyError,'not_found'):self.assess()
        self.assertEqual(self.audit_count(),1)
    def test_archived_stale_or_readonly_conversation_never_admitted(self):
        with self.assertRaisesRegex(PolicyError,'stale_revision'):self.assess(expected_revision=2)
        self.sql("UPDATE assistant_conversations SET state='archived' WHERE workspace_id='alpha'")
        with self.assertRaisesRegex(PolicyError,'conversation_archived'):self.assess()
        self.sql("UPDATE assistant_conversations SET state='active' WHERE workspace_id='alpha'")
        self.sql("UPDATE memberships SET role='viewer' WHERE workspace_id='alpha' AND principal_id='alice'")
        with self.assertRaises(PolicyError):self.assess()
        self.assertEqual(self.audit_count(),0)
    def test_atomic_audit_failure_rolls_back_receipt(self):
        self.sql("CREATE TRIGGER reject_preflight_audit BEFORE INSERT ON audit WHEN NEW.object_id LIKE 'assistant-preflight:%' BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
        with self.assertRaises(StorageError):self.assess()
        self.assertEqual(self.sql('SELECT COUNT(*) FROM assistant_execution_preflights'),[(0,)])
        self.assertEqual(self.audit_count(),0)
    def test_expiry_after_receipt_and_audit_rolls_back_both(self):
        def expire(*args):_audit(*args);self.now+=timedelta(minutes=2)
        with patch('packages.assistant_protocol.preflight._audit',side_effect=expire):
            with self.assertRaises(AuthenticationError):self.assess()
        self.assertEqual(self.sql('SELECT COUNT(*) FROM assistant_execution_preflights'),[(0,)])
        self.assertEqual(self.audit_count(),0)
    def test_racing_same_operation_is_one_atomic_receipt(self):
        barrier=Barrier(3);results=[];errors=[]
        def run():
            barrier.wait()
            try:results.append(self.assess())
            except Exception as e:errors.append(type(e).__name__)
        threads=[Thread(target=run) for _ in range(2)]
        for t in threads:t.start()
        barrier.wait()
        for t in threads:t.join(timeout=10)
        self.assertFalse(any(t.is_alive() for t in threads));self.assertEqual(errors,[])
        self.assertEqual(len(results),2);self.assertEqual(results[0],results[1]);self.assertEqual(self.audit_count(),1)
    def test_schema_and_record_corruption_fail_closed(self):
        self.assess();self.sql("UPDATE assistant_execution_preflights SET binding='not-a-digest'")
        with self.assertRaises(StorageError):self.assess()
        self.sql('ALTER TABLE assistant_execution_preflights ADD COLUMN unexpected TEXT')
        with self.assertRaises(StorageError):self.assess()
    def test_no_sensitive_inputs_or_environment_are_part_of_contract(self):
        for extra in ({'prompt':'secret'}, {'env':{'HOME':'/private'}}, {'command':['sh']}, {'isolation_accepted':True}):
            with self.assertRaises(TypeError):self.assess(**extra)
        for operation in ('bad/path','x'*65,'alpha\n'):
            with self.assertRaises(ValueError):self.assess(operation=operation)
        with self.assertRaisesRegex(PolicyError,'post_required'):
            self.preflight.assess({'REQUEST_METHOD':'GET'},workspace='alpha',conversation='one',operation='op',expected_revision=1)
        self.assess();dump=str(self.sql('SELECT * FROM assistant_execution_preflights'))+str(self.sql("SELECT * FROM audit WHERE object_id LIKE 'assistant-preflight:%'"))
        for forbidden in ('Private title','test-alice','https://','HOME','secret'):self.assertNotIn(forbidden,dump)
    def test_bound_receipt_budget_does_not_evict_or_replay(self):
        for i in range(100):self.assess(operation=f'op-{i}')
        with self.assertRaisesRegex(PolicyError,'preflight_limit'):self.assess(operation='overflow')
        self.assess(operation='op-1');self.assertEqual(self.audit_count(),100)

if __name__=='__main__':unittest.main()
