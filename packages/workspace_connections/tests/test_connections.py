"""Synthetic authority and policy-change regressions, no network provider."""
from dataclasses import replace
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import json
import sqlite3
import unittest
from unittest.mock import patch

from packages.community_api.tests.test_application import CommunityTests, config, call, SCOPE, BASE, ISSUER
from packages.community_api.records import read
from packages.community_api import replace_policy
from packages.workspace_api import WorkspaceApplication
from packages.identity import Provider
from packages.workspaces import StorageError
from packages.workspace_connections import CommunityConnectionTemplate, WorkspaceConnectionsApplication, initialize_connections
from packages.workspace_connections.records import GRANTS, MAX_REVISION

PATH = '/api/workspaces/alpha/connections/community'


class ConnectionTests(CommunityTests):
    # Reuse fixture only, not the parent tests (removed from suite below).
    def setUp(self):
        super().setUp()
        self.maximum = replace(config(), reader_ids=('admin', 'reader', 'dev', 'second'), refresher_ids=('admin','second'), approver_ids=('admin','second'))
        self.template = CommunityConnectionTemplate('source-policy', self.maximum, ('admin',))
        self.templates = (self.template, replace(self.template, maximum=replace(self.maximum, connection=replace(self.maximum.connection, scope=replace(SCOPE, workspace_id='commons')))))
        initialize_connections(self.store, self.templates)
        api = WorkspaceApplication(store=self.store, providers=(Provider('test', ISSUER, 'community-test', self.verifier),), provider_key='test', clock=lambda: self.now)
        self.management = WorkspaceConnectionsApplication(api=api, connections=self.templates)

    def request(self, suffix='', **kwargs):
        kwargs.setdefault('base', PATH); kwargs.setdefault('token','test-admin')
        return call(self.management, suffix, **kwargs)

    def change(self, *, grants=None, revision=None, **kwargs):
        grants = grants if grants is not None else dict(reader_ids=['admin'], refresher_ids=['admin'], approver_ids=['admin'])
        return self.request('/manage/source-policy', method='PATCH', body=grants,
            revision=self.state().revision if revision is None else revision, **kwargs)

    def test_discovery_filters_exact_workspace_and_explicit_readers(self):
        d=self.request(token='test-reader')['body']['data']
        self.assertEqual(len(d['items']), 1); self.assertEqual(d['items'][0]['scope'], SCOPE.as_dict())
        self.assertEqual(self.request(token='test-second')['body']['data']['items'], [])
        self.assertEqual(self.request(token='test-outsider')['status'],404)
        self.assertEqual(self.request(base=PATH.replace('alpha','beta'),token='test-reader')['status'],404)
        d=self.request(base=PATH.replace('alpha','commons'),token='test-reader')['body']['data']
        self.assertEqual(d['items'][0]['scope']['workspace_id'],'commons')
        self.assertNotIn('credential',json.dumps(d));self.assertNotIn('manager',json.dumps(d))

    def test_manager_requires_admin_module_and_explicit_platform_grant(self):
        for token in ('test-reader','test-dev','test-second'):
            self.assertNotEqual(self.change(token=token)['status'],200)
        self.assertEqual(self.request('/manage',token='test-second')['body']['data']['items'],[])
        self.assertEqual(self.request('/manage')['status'],200)
        self.store.set_membership('second','alpha','admin',role='developer',state='active',expected_revision=1)
        self.assertEqual(self.change()['status'],403)

    def test_new_grants_clear_observations_approvals_and_audit_atomically(self):
        self.refresh();self.approve(); self.assertTrue(self.state().approvals)
        result=self.change(); self.assertEqual(result['status'],200)
        self.assertEqual(result['body']['data']['revision'],4)
        self.assertIsNone(self.state().snapshot);self.assertFalse(self.state().approvals)
        self.assertEqual(self.state().policy.credential_ref,self.maximum.credential_ref)
        self.assertEqual(self.state().policy.connection,self.maximum.connection)
        self.assertEqual(call(self.app)['status'],404)
        audit=self.store._transaction(lambda p: p._audit[-1],write=False)
        self.assertEqual(audit.revision,4);self.assertTrue(audit.object_id.startswith('community-config:'))
        self.assertNotIn('reader',str(audit));self.assertNotIn('approved-secret',str(audit))

    def test_noop_retains_snapshot_approval_revision_and_no_extra_audit(self):
        self.refresh();self.approve(); before=self.state()
        grants={k:list(reversed(getattr(before.policy,k))) for k in GRANTS}
        r=self.change(grants=grants); self.assertEqual(r['status'],200);self.assertEqual(self.state(),before)

    def test_current_member_and_role_required_for_every_selected_principal(self):
        values={k:list(getattr(config(),k)) for k in GRANTS}
        values['reader_ids']=['outsider'];self.assertEqual(self.change(grants=values)['status'],400)
        values['reader_ids']=['reader'];values['refresher_ids']=['reader'];self.assertEqual(self.change(grants=values)['status'],400)
        self.store.set_membership('admin','alpha','reader',role='viewer',state='revoked',expected_revision=1)
        values['refresher_ids']=['admin'];self.assertEqual(self.change(grants=values)['status'],400)
        options=self.request('/manage')['body']['data']['items'][0]['options']
        self.assertFalse(next(o for o in options if o['principal_id']=='reader')['eligible']['reader_ids'])

    def test_cas_then_current_grant_check(self):
        self.assertEqual(self.change()['status'],200)
        self.assertEqual(self.change(revision=1)['status'],412)
        self.store.set_membership('second','alpha','admin',role='viewer',state='revoked',expected_revision=1)
        self.assertEqual(self.change(revision=1)['status'],404)

    def test_inflight_observation_cannot_restore_snapshot_after_policy_change(self):
        self.provider.hook=lambda:self.assertEqual(self.change()['status'],200)
        self.assertEqual(self.refresh(revision=1)['status'],412)
        self.assertIsNone(self.state().snapshot);self.assertEqual(self.state().revision,2)

    def test_old_approval_cannot_restore_after_grant_change(self):
        self.refresh();fingerprints=[__import__('packages.community_api.records',fromlist=['fingerprint']).fingerprint(SCOPE,item) for item in self.state().snapshot.items]
        self.change()
        self.assertEqual(self.approve(fingerprints,revision=2)['status'],412)
        self.assertIsNone(self.state().snapshot)

    def test_binding_template_or_credential_drift_fails_closed(self):
        replacement=replace(self.template,manager_ids=('admin','second'))
        app=WorkspaceConnectionsApplication(api=self.management._api,connections=(replacement,))
        self.assertEqual(call(app,'/manage',base=PATH,token='test-admin')['status'],409)
        replace_policy(self.store,replace(config(),credential_ref='changed-ref'),expected_revision=1)
        self.assertEqual(self.request('/manage')['status'],409)
        self.assertEqual(self.request()['body']['data']['items'],[])
        self.assertNotIn('changed-ref',str(self.request('/manage')))

    def test_audit_failure_rolls_back_config_snapshot_revision(self):
        self.refresh();self.approve();before=self.state()
        with sqlite3.connect(self.path) as c:
            c.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(FAIL,'test'); END")
        self.assertNotEqual(self.change()['status'],200);self.assertEqual(self.state(),before)

    def test_exact_schema_and_no_repin(self):
        with self.assertRaises(StorageError):initialize_connections(self.store,(self.template,))
        with sqlite3.connect(self.path) as c:
            c.execute('ALTER TABLE workspace_connection_templates_v1 RENAME TO old_template')
            c.execute('CREATE TABLE workspace_connection_templates_v1(workspace_id,template_id,connection_id,repository,fingerprint,identities)')
        self.assertEqual(self.request('/manage')['status'],503)
        with self.assertRaises(StorageError):initialize_connections(self.store,())

    def test_malformed_inputs_unknown_keys_no_internal_values(self):
        for body in ({}, {'reader_ids':[], 'refresher_ids':[], 'approver_ids':[], 'credential_ref':'x'},
            {'reader_ids':['admin','admin'],'refresher_ids':[],'approver_ids':[]},
            {'reader_ids':[{}],'refresher_ids':[],'approver_ids':[]},
            {'reader_ids':'admin','refresher_ids':[],'approver_ids':[]}):
            self.assertEqual(self.request('/manage/source-policy',method='PATCH',body=body,revision=1)['status'],400)
        self.assertEqual(self.request('/manage/source-policy',method='PATCH',body=dict(reader_ids=[],refresher_ids=[],approver_ids=[]))['status'],428)
        self.assertEqual(self.request(query='all=true')['status'],400)
        for token in (None,'bad','test-disabled'):
            self.assertEqual(self.request('/manage',token=token)['status'],401)

    def test_missing_initialization_does_not_create_tables(self):
        with sqlite3.connect(self.path) as c:c.execute('DROP TABLE workspace_connection_templates_v1')
        self.assertEqual(self.request('/manage')['status'],503)
        with sqlite3.connect(self.path) as c:self.assertIsNone(c.execute("SELECT name FROM sqlite_schema WHERE name='workspace_connection_templates_v1'").fetchone())

    def test_two_concurrent_replacements_only_one_cas_wins(self):
        barrier=Barrier(2)
        def worker(reader):
            barrier.wait()
            return self.change(grants=dict(reader_ids=[reader],refresher_ids=['admin'],approver_ids=['admin']),revision=1)['status']
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(worker,'admin');b=pool.submit(worker,'reader')
            self.assertEqual(sorted([a.result(),b.result()]),[200,412])
        self.assertEqual(self.state().revision,2)
        audits=self.store._transaction(lambda p:[e for e in p._audit if e.object_id.startswith('community-config:')],write=False)
        self.assertEqual(len(audits),1)

    def test_identity_expiry_at_commit_rolls_back_replacement(self):
        from packages.workspaces.service import WorkspaceService
        original=WorkspaceService._changed
        before=self.state()
        def changed(policy,*args,**kwargs):
            result=original(policy,*args,**kwargs)
            self.now+=timedelta(seconds=61)
            return result
        with patch.object(WorkspaceService,'_changed',changed):
            self.assertEqual(self.change()['status'],401)
        self.assertEqual(self.state(),before)

    def test_readonly_module_denies_edit_without_losing_read_discovery(self):
        with sqlite3.connect(self.path) as c:c.execute("UPDATE modules SET can_write=0 WHERE workspace_id='alpha' AND key='community'")
        self.assertEqual(self.request()['status'],200)
        self.assertEqual(self.request('/manage')['status'],200)
        self.assertEqual(self.change()['status'],403)

    def test_revision_limit_does_not_advance_outside_safe_json_integer(self):
        with sqlite3.connect(self.path) as c:c.execute('UPDATE community_connections SET revision=?',(MAX_REVISION,))
        self.assertEqual(self.change()['status'],409)


def load_tests(loader, tests, pattern):
    suite=unittest.TestSuite()
    for name in ConnectionTests.__dict__:
        if name.startswith('test_'):suite.addTest(ConnectionTests(name))
    return suite
