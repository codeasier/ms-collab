"""Real Cookie, current-principal, SQLite and three-directory receipt integration.

No provider, fake projection, production state or browser is used. Clock/fault
hooks wrap real directory evaluation, rather than replacing the directory data.
"""
import base64
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from packages.application_host.tests import fixtures
from packages.application_ui.tests.directory_receipt_fixtures import (
    HIDDEN, NAMESPACES, SPACES, UNPUBLISHED, application, config, publish_many,
)
from packages.application_ui.tests.test_application import request
from packages.artifacts import Artifact, Scope, Snapshot
from packages.artifact_sync import records
from packages.artifact_sync.tests.authority_fixtures import configure_index_fixture, seed_publication
from packages.directory_cursors.store import PARTITIONS, RECEIPTS
from packages.pipeline_api import encode_snapshots, resource_id
from packages.pipelines import PipelineScope
from packages.session_api.application import _CSRF, _SESSION
from packages.session_api.tests.test_application import cookie_header
from packages.workspaces import Membership
from packages.workspaces.tests.identity_fixture import simulate_identity_with_core_regrant

CHANGED = {'error': 'scope_directory_changed'}


class DirectoryReceiptIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = fixtures.NOW
        self.app, self.policy, self.login, self.exchange = application(
            self.temp.name, clock=lambda: self.now)
        self.path = Path(self.temp.name) / 'policy.sqlite'
        self.sign_in()

    def sign_in(self, subject='admin-sub'):
        self.session = fixtures.session(self.login, self.exchange, subject)
        self.cookies = cookie_header({_SESSION: self.session.token.value, _CSRF: self.session.csrf.value})
        return self.cookies

    def sql(self, statement, args=()):
        with sqlite3.connect(self.path) as db:
            return db.execute(statement, args).fetchall()

    def call(self, namespace='pipelines', workspace='alpha', *, token=None, suffix='', **kwargs):
        if token is not None:
            self.assertEqual(suffix, '')
            suffix = '?after=' + token
        return request(self.app, f'/api/workspaces/{workspace}/{namespace}/scopes' + suffix,
                       cookies=kwargs.pop('cookies', self.cookies), **kwargs)

    def page(self, namespace='pipelines', workspace='alpha', **kwargs):
        result = self.call(namespace, workspace, **kwargs)
        self.assertEqual(result['status'], 200, result.get('body'))
        return result['body']['data']

    def tokens(self):
        return {namespace: self.page(namespace)['next_after'] for namespace in NAMESPACES}

    def assert_changed(self, result):
        self.assertEqual(result['status'], 409)
        self.assertEqual(result['body'], CHANGED)
        self.assertEqual(dict(result['headers'])['Cache-Control'], 'no-store')

    def assert_tokens_changed(self, tokens):
        for namespace, token in tokens.items():
            with self.subTest(namespace=namespace):
                self.assert_changed(self.call(namespace, token=token))

    def configure(self, scope, **changes):
        old = self.policy._transaction(lambda p, db: records.load(db, scope).policy,
                                       write=False, with_connection=True)
        configure_index_fixture(self.policy, replace(old, **changes), actor='second')

    def business_rows(self):
        tables = ('metadata', 'principals', 'memberships', 'membership_authority',
                  'modules', 'resources', 'audit', 'artifact_indexes_v1', 'artifact_index_events_v1')
        return {table: self.sql(f'SELECT * FROM {table} ORDER BY 1,2') for table in tables}

    def identity_aba(self, actor):
        with sqlite3.connect(self.path) as db:
            simulate_identity_with_core_regrant(db,
                'UPDATE principals SET subject=? WHERE id=?', (actor + '-temporary', actor))
            simulate_identity_with_core_regrant(db,
                'UPDATE principals SET subject=? WHERE id=?', (actor + '-sub', actor))

    def test_three_actions_three_spaces_two_pages_and_canonical_receipts(self):
        anchors = []
        for namespace in NAMESPACES:
            for workspace in SPACES:
                with self.subTest(namespace=namespace, workspace=workspace):
                    first = self.page(namespace, workspace)
                    self.assertEqual(len(first['items']), 25)
                    token = first['next_after']
                    self.assertRegex(token, r'^dcr1_[A-Za-z0-9_-]{43}$')
                    decoded = base64.urlsafe_b64decode(token[5:] + '=')
                    self.assertEqual(len(decoded), 32)
                    self.assertEqual(base64.urlsafe_b64encode(decoded).decode().rstrip('='), token[5:])
                    key = 'pipeline_id' if namespace == 'pipelines' else 'bucket_id'
                    last = first['items'][-1]['scope']
                    anchors.append((last['connection_id'], last[key]))
                    second = self.page(namespace, workspace, token=token)
                    self.assertEqual(len(second['items']), 3 if namespace == 'artifact-admin' and workspace == 'alpha' else 2)
                    self.assertIsNone(second['next_after'])
                    all_items = first['items'] + second['items']
                    tuples = [(item['scope']['connection_id'], item['scope'][key]) for item in all_items]
                    self.assertEqual(tuples, sorted(set(tuples)))
                    self.assertTrue(all(item['scope']['workspace_id'] == workspace for item in all_items))
        self.assertEqual(set(anchors), {('source', 'p023')})

    def test_same_tuple_receipt_cannot_cross_any_workspace(self):
        for namespace in NAMESPACES:
            for source in SPACES:
                token = self.page(namespace, source)['next_after']
                for target in SPACES:
                    if target != source:
                        with self.subTest(namespace=namespace, source=source, target=target):
                            self.assert_changed(self.call(namespace, target, token=token))

    def test_same_tuple_receipt_cannot_cross_any_action(self):
        tokens = self.tokens()
        for source, token in tokens.items():
            for target in NAMESPACES:
                if target != source:
                    with self.subTest(source=source, target=target):
                        self.assert_changed(self.call(target, token=token))

    def test_both_authorized_principals_have_isolated_receipts(self):
        admin_cookies, admin_tokens = self.cookies, self.tokens()
        self.sign_in('second-sub')
        second_tokens = self.tokens()
        self.assert_tokens_changed(admin_tokens)
        for namespace in NAMESPACES:
            self.assert_changed(self.call(namespace, token=second_tokens[namespace], cookies=admin_cookies))
            self.page(namespace, token=second_tokens[namespace])
            self.page(namespace, token=admin_tokens[namespace], cookies=admin_cookies)

    def test_new_session_same_principal_can_resume_but_revoked_cookie_cannot(self):
        old_cookies, tokens = self.cookies, self.tokens()
        self.login.revoke_subject('admin-sub')
        self.sign_in()
        for namespace, token in tokens.items():
            self.assertEqual(self.call(namespace, token=token, cookies=old_cookies)['status'], 401)
            self.page(namespace, token=token)

    def test_all_malformed_after_values_have_one_value_free_error(self):
        malformed = ('?after=', '?after=source~p023', '?after=a&after=b', '?after=%',
                     '?after=%GG', '?after=%FF', '?after=%00', '?after=%E2%98%83',
                     '?after=' + 'x' * 601, '?after=dcr1_' + 'A' * 43,
                     '?after=dcr1_' + 'A' * 42 + 'B')
        for namespace in NAMESPACES:
            for suffix in malformed:
                with self.subTest(namespace=namespace, suffix=suffix):
                    self.assert_changed(self.call(namespace, suffix=suffix))
            for suffix in ('?limit=25', '?actor=admin', '?after=x&filter=y'):
                result = self.call(namespace, suffix=suffix)
                self.assertEqual(result['status'], 400)
                self.assertEqual(result['body'], {'error': 'invalid_request'})

    def test_current_cookie_and_transport_precede_malicious_after(self):
        for namespace in NAMESPACES:
            self.assertEqual(self.call(namespace, suffix='?after=%', cookies='')['status'], 401)
            self.assertEqual(self.call(namespace, suffix='?after=%', extra={'HTTP_AUTHORIZATION': 'Bearer synthetic'})['status'], 401)
        self.now = self.session.expires_at
        for namespace in NAMESPACES:
            result = self.call(namespace, suffix='?after=%')
            self.assertEqual(result['body'], {'error': 'authentication_required'})
            self.assertEqual(result['status'], 401)

    def test_missing_workspace_and_nonmember_precede_malicious_after(self):
        for namespace in NAMESPACES:
            self.assertEqual(self.call(namespace, 'missing', suffix='?after=%')['status'], 404)
        self.sign_in('outsider-sub')
        for namespace in NAMESPACES:
            self.assertEqual(self.call(namespace, suffix='?after=%')['status'], 404)
        self.assertEqual(self.sql(f'SELECT token FROM {RECEIPTS}'), [])
        self.assertEqual(self.sql(f'SELECT actor_id FROM {PARTITIONS}'), [])

    def test_module_capability_and_lifecycle_precede_malicious_after(self):
        for module in ('pipelines', 'artifacts'):
            self.sql('UPDATE modules SET can_read=0,can_write=0 WHERE workspace_id=? AND key=?', ('alpha', module))
        for namespace in NAMESPACES:
            self.assertEqual(self.call(namespace, suffix='?after=%')['status'], 403)
        self.sql("UPDATE modules SET can_read=1,can_write=1,state='disabled' WHERE workspace_id='alpha'")
        for namespace in NAMESPACES:
            result = self.call(namespace, suffix='?after=%')
            self.assertEqual(result['status'], 409)
            self.assertEqual(result['body'], {'error': 'module_unavailable'})

    def test_receipt_reuse_does_not_slide_expiry_and_boundary_is_exact(self):
        tokens = self.tokens()
        rows = self.sql(f'SELECT token,issued_us,expires_us FROM {RECEIPTS} ORDER BY token')
        self.assertTrue(all(expires - issued == 300_000_000 for _, issued, expires in rows))
        self.now += timedelta(seconds=299)
        for namespace, token in tokens.items():
            self.assertEqual(self.page(namespace)['next_after'], token)
            self.page(namespace, token=token)
            self.page(namespace, token=token)
        self.assertEqual(self.sql(f'SELECT token,issued_us,expires_us FROM {RECEIPTS} ORDER BY token'), rows)
        self.now += timedelta(seconds=1)
        self.assert_tokens_changed(tokens)

    def test_membership_revoke_then_explicit_rejoin_does_not_revive_receipt(self):
        tokens = self.tokens()
        self.policy.set_membership('second', 'alpha', 'admin', role='admin', state='revoked', expected_revision=1)
        for namespace, token in tokens.items():
            self.assertEqual(self.call(namespace, token=token)['status'], 404)
        self.policy._transaction(lambda p: p._grant_membership(Membership('alpha', 'admin', 'admin', revision=3)), write=True)
        self.assert_tokens_changed(tokens)

    def test_actor_role_true_aba_keeps_current_access_but_invalidates_old_receipt(self):
        tokens = self.tokens()
        self.policy.set_membership('second', 'alpha', 'admin', role='developer', state='active', expected_revision=1)
        self.policy.set_membership('second', 'alpha', 'admin', role='admin', state='active', expected_revision=2)
        self.assert_tokens_changed(tokens)
        for namespace in NAMESPACES:
            self.assertTrue(self.page(namespace)['items'])

    def test_module_disable_then_enable_true_aba_invalidates_receipts(self):
        tokens = self.tokens()
        for module in ('pipelines', 'artifacts'):
            for revision, state in enumerate(('disabling', 'disabled', 'enabled'), 1):
                self.policy.set_module_state('admin', 'alpha', module, state=state, expected_revision=revision)
        self.assert_tokens_changed(tokens)

    def test_workspace_suspend_then_activate_true_aba_invalidates_receipts(self):
        tokens = self.tokens()
        self.policy.set_workspace_state('admin', 'alpha', state='suspended', expected_revision=1)
        self.policy.set_workspace_state('admin', 'alpha', state='active', expected_revision=2)
        self.assert_tokens_changed(tokens)

    def test_identity_true_aba_requires_new_cookie_and_preserves_actor_partition(self):
        tokens = self.tokens()
        partitions = self.sql(f'SELECT actor_id,workspace_id,action,clock_floor_us FROM {PARTITIONS} ORDER BY action')
        self.identity_aba('admin')
        for namespace, token in tokens.items():
            self.assertEqual(self.call(namespace, token=token)['status'], 401)
        self.sign_in()
        self.assert_tokens_changed(tokens)
        self.assertEqual(self.sql(f'SELECT actor_id,workspace_id,action,clock_floor_us FROM {PARTITIONS} ORDER BY action'), partitions)
        self.assertEqual(len(self.sql(f'SELECT token FROM {RECEIPTS}')), 3)
        self.assertNotEqual(self.page('pipelines')['next_after'], tokens['pipelines'])
        self.assertEqual(len(self.sql(f'SELECT actor_id FROM {PARTITIONS}')), 3)

    def test_pipeline_snapshot_and_keyset_changes_are_data_not_authority(self):
        token = self.page()['next_after']
        scope = PipelineScope('alpha', 'source', 'p025')
        self.sql('UPDATE resources SET text=?,revision=2 WHERE workspace_id=? AND module_key=? AND id=?',
                 (encode_snapshots(scope, []), 'alpha', 'pipelines', resource_id(scope)))
        for name in ('a-new', 'z-new'):
            added = PipelineScope('alpha', 'source', name)
            self.sql('INSERT INTO resources VALUES (?,?,?,?,1)',
                     ('alpha', 'pipelines', resource_id(added), encode_snapshots(added, [])))
        second = self.page(token=token)
        self.assertEqual([item['scope']['pipeline_id'] for item in second['items']], ['p024', 'p025', 'z-new'])
        self.assertEqual(second['items'][1]['snapshot_revision'], 2)

    def test_pipeline_anchor_removal_is_not_silently_restarted(self):
        first = self.page()
        anchor = PipelineScope(**first['items'][-1]['scope'])
        self.sql("DELETE FROM resources WHERE workspace_id='alpha' AND module_key='pipelines' AND id=?", (resource_id(anchor),))
        self.assert_changed(self.call(token=first['next_after']))

    def test_artifact_candidate_and_same_provenance_republication_do_not_invalidate(self):
        tokens = self.tokens()
        scope = Scope('alpha', 'source', 'p025')
        snapshot = Snapshot(scope, '2026-01-01T00:00:00Z', (Artifact('current.txt', '2026-01-01T00:00:00Z', 7),))
        self.sql('UPDATE artifact_indexes_v1 SET candidate=?,revision=2 WHERE workspace=? AND connection=? AND bucket=?',
                 (records.snapshot_text(snapshot), *records.scope_key(scope)))
        for namespace in NAMESPACES:
            self.page(namespace, token=tokens[namespace])
        seed_publication(self.policy, scope, snapshot, actor='second')
        reader = self.page('artifacts', token=tokens['artifacts'])
        self.assertEqual(next(item['catalog_revision'] for item in reader['items'] if item['scope']['bucket_id'] == 'p025'), 2)
        self.page('artifact-admin', token=tokens['artifact-admin'])

    def test_first_publication_changes_reader_visibility_not_admin_authority(self):
        tokens = self.tokens()
        publish_many(self.policy, [UNPUBLISHED])
        self.assert_changed(self.call('artifacts', token=tokens['artifacts']))
        self.page('artifact-admin', token=tokens['artifact-admin'])
        self.page('pipelines', token=tokens['pipelines'])

    def test_visible_nonanchor_scope_removal_invalidates_both_artifact_directories(self):
        tokens = self.tokens()
        self.sql("DELETE FROM artifact_indexes_v1 WHERE workspace='alpha' AND bucket='p025'")
        for namespace in ('artifacts', 'artifact-admin'):
            self.assert_changed(self.call(namespace, token=tokens[namespace]))
        self.page('pipelines', token=tokens['pipelines'])

    def test_explicit_same_policy_regrant_changes_fence_without_global_epoch_change(self):
        tokens = self.tokens()
        epoch = self.sql('SELECT epoch FROM metadata')
        self.configure(Scope('alpha', 'source', 'p025'))
        self.assertEqual(self.sql('SELECT epoch FROM metadata'), epoch)
        for namespace in ('artifacts', 'artifact-admin'):
            self.assert_changed(self.call(namespace, token=tokens[namespace]))
        self.page('pipelines', token=tokens['pipelines'])

    def test_artifact_grant_true_aba_does_not_revive_old_receipt(self):
        tokens = self.tokens()
        scope = Scope('alpha', 'source', 'p025')
        self.configure(scope, readers=('second', 'reader'))
        self.configure(scope, readers=('admin', 'second', 'reader'))
        for namespace in ('artifacts', 'artifact-admin'):
            self.assert_changed(self.call(namespace, token=tokens[namespace]))
            self.assertTrue(self.page(namespace)['items'])

    def test_hidden_candidate_and_normal_hidden_regrant_do_not_create_conflicts(self):
        tokens = self.tokens()
        hidden_snapshot = Snapshot(HIDDEN, '2026-01-01T00:00:00Z', ())
        self.sql('UPDATE artifact_indexes_v1 SET candidate=?,revision=2 WHERE workspace=? AND connection=? AND bucket=?',
                 (records.snapshot_text(hidden_snapshot), *records.scope_key(HIDDEN)))
        configure_index_fixture(self.policy, replace(config(HIDDEN, hidden=True), credential_ref='other-synthetic-reference'),
                                republish=False)
        for namespace, token in tokens.items():
            self.page(namespace, token=token)

    def test_unrelated_workspace_module_and_principal_changes_are_not_global_fences(self):
        tokens = self.tokens()
        self.policy.set_membership('second', 'beta', 'admin', role='developer', state='active', expected_revision=1)
        self.policy.set_module_state('admin', 'alpha', 'community', state='disabling', expected_revision=1)
        self.identity_aba('outsider')
        for namespace, token in tokens.items():
            self.page(namespace, token=token)

    def test_approver_role_true_aba_and_fresh_proof_invalidate_reader_not_admin(self):
        tokens = self.tokens()
        self.policy.set_membership('admin', 'alpha', 'second', role='developer', state='active', expected_revision=1)
        self.policy.set_membership('admin', 'alpha', 'second', role='admin', state='active', expected_revision=2)
        scopes = [Scope('alpha', 'source', 'bucket'), *(Scope('alpha', 'source', f'p{i:03d}') for i in range(26))]
        publish_many(self.policy, scopes)
        self.assert_changed(self.call('artifacts', token=tokens['artifacts']))
        self.page('artifact-admin', token=tokens['artifact-admin'])
        self.page('pipelines', token=tokens['pipelines'])

    def test_approver_identity_true_aba_hides_stale_proof_without_admin_fence(self):
        tokens = self.tokens()
        self.identity_aba('second')
        self.assert_changed(self.call('artifacts', token=tokens['artifacts']))
        self.assertEqual(self.page('artifacts')['items'], [])
        self.page('artifact-admin', token=tokens['artifact-admin'])
        self.page('pipelines', token=tokens['pipelines'])

    def test_reader_sql_projects_null_candidate_and_denied_payload_is_late_decoded(self):
        tokens = self.tokens()
        self.sql("UPDATE artifact_indexes_v1 SET candidate=CAST(X'ff00' AS TEXT) WHERE workspace='alpha'")
        self.sql("UPDATE artifact_indexes_v1 SET published=CAST(X'ff00' AS TEXT),published_revision=1 WHERE workspace='alpha' AND bucket='zz-hidden'")
        trace = []
        connect = self.policy._connect
        def traced():
            db = connect()
            db.set_trace_callback(trace.append)
            return db
        with patch.object(self.policy, '_connect', side_effect=traced):
            self.page('artifacts', token=tokens['artifacts'])
        projections = [statement for statement in trace if 'SELECT revision,' in statement]
        self.assertTrue(projections)
        self.assertTrue(all(',NULL,' in statement and 'CAST(candidate' not in statement for statement in projections))
        self.assertEqual(self.call('artifact-admin', token=tokens['artifact-admin'])['status'], 503)

    def test_admin_uses_same_exact_capabilities_and_not_reader_membership(self):
        scope = Scope('alpha', 'source', 'p000')
        self.configure(scope, readers=('second', 'reader'))
        self.assertNotIn('p000', [item['scope']['bucket_id'] for item in self.page('artifacts')['items']])
        admin = self.page('artifact-admin')
        item = next(item for item in admin['items'] if item['scope']['bucket_id'] == 'p000')
        status = request(self.app, '/api/workspaces/alpha/artifact-admin/source/p000/status', cookies=self.cookies)
        self.assertEqual(status['status'], 200)
        self.assertEqual(item['capabilities'], status['body']['data']['capabilities'])
        self.assertEqual(item['capabilities'], {'refresh': False, 'review': True, 'approve': True})
        self.assertEqual(set(status['body']['data']), {'scope', 'revision', 'published_revision', 'pending', 'capabilities'})

    def test_allow_refresh_is_current_context_not_a_persistent_configuration_epoch(self):
        tokens = self.tokens()
        admin = self.app._host._artifact_admin
        # Trusted synthetic configuration only. No provider request is performed.
        with patch.object(admin, '_allow_refresh', True):
            self.assert_changed(self.call('artifact-admin', token=tokens['artifact-admin']))
            self.page('artifacts', token=tokens['artifacts'])
        self.page('artifact-admin', token=tokens['artifact-admin'])

    def test_cookie_get_without_csrf_never_persists_business_state(self):
        cookies = cookie_header({_SESSION: self.session.token.value})
        before = self.business_rows()
        with patch.object(self.policy, '_persist', side_effect=AssertionError('directory must not persist business policy')):
            for namespace in NAMESPACES:
                self.page(namespace, cookies=cookies)
                self.assert_changed(self.call(namespace, suffix='?after=bad', cookies=cookies))
        self.assertEqual(self.business_rows(), before)
        self.assertEqual(len(self.sql(f'SELECT token FROM {RECEIPTS}')), 3)

    def test_conflicting_workspace_hint_does_not_issue_or_arm_receipts(self):
        before = self.business_rows()
        for namespace in NAMESPACES:
            result = self.call(namespace, suffix='?after=%', extra={'HTTP_X_WORKSPACE_ID': 'beta'})
            self.assertEqual(result['status'], 400)
            self.assertEqual(result['body'], {'error': 'invalid_request'})
        self.assertEqual(self.sql(f'SELECT token FROM {RECEIPTS}'), [])
        self.assertEqual(self.sql(f'SELECT actor_id FROM {PARTITIONS}'), [])
        self.assertEqual(self.business_rows(), before)

    def test_each_success_uses_two_real_projections_not_one_per_receipt(self):
        modules = ('packages.pipeline_api.application', 'packages.artifact_sync.application', 'packages.artifact_admin.application')
        for namespace, name in zip(NAMESPACES, modules):
            with self.subTest(namespace=namespace):
                module = __import__(name, fromlist=['directory'])
                with patch.object(module, 'directory', wraps=module.directory) as observed:
                    token = self.page(namespace)['next_after']
                    self.assertEqual(observed.call_count, 2)
                    self.page(namespace, token=token)
                    self.assertEqual(observed.call_count, 4)
                    self.assert_changed(self.call(namespace, suffix='?after=%'))
                    self.assertEqual(observed.call_count, 4)

    def test_final_session_expiry_rolls_back_new_receipt_for_every_real_directory(self):
        modules = ('packages.pipeline_api.application', 'packages.artifact_sync.application', 'packages.artifact_admin.application')
        for namespace, name in zip(NAMESPACES, modules):
            with self.subTest(namespace=namespace):
                self.sign_in()
                before = self.business_rows()
                module = __import__(name, fromlist=['directory'])
                original = module.directory
                def expire(*args, **kwargs):
                    projection = original(*args, **kwargs)
                    self.assertTrue(projection.value)
                    self.now = self.session.expires_at
                    return projection
                with patch.object(module, 'directory', side_effect=expire):
                    result = self.call(namespace)
                self.assertEqual(result['status'], 401)
                self.assertEqual(result['body'], {'error': 'authentication_required'})
                self.assertEqual(self.sql(f'SELECT token FROM {RECEIPTS}'), [])
                self.assertEqual(self.business_rows(), before)

    def test_final_receipt_expiry_rolls_back_issuance_without_returning_a_token(self):
        modules = ('packages.pipeline_api.application', 'packages.artifact_sync.application', 'packages.artifact_admin.application')
        for namespace, name in zip(NAMESPACES, modules):
            with self.subTest(namespace=namespace):
                module = __import__(name, fromlist=['directory'])
                original = module.directory
                first = True
                def expire(*args, **kwargs):
                    nonlocal first
                    projection = original(*args, **kwargs)
                    if first:
                        first = False
                        self.now += timedelta(seconds=300)
                    return projection
                before = self.business_rows()
                with patch.object(module, 'directory', side_effect=expire):
                    self.assert_changed(self.call(namespace))
                self.assertEqual(self.sql(f'SELECT token FROM {RECEIPTS}'), [])
                self.assertEqual(self.business_rows(), before)
