"""Real cookie/session/SQLite directory policy and codec; no provider discovery."""
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from packages.application_host.tests import fixtures
from packages.application_ui import ApplicationUI
from packages.application_ui.tests.pipeline_discovery_fixtures import seed_directory, seed_pages
from packages.application_ui.tests.test_application import request
from packages.pipeline_api import encode_snapshots, resource_id
from packages.pipeline_api import application as module
from packages.pipeline_api.discovery import MAX_SCOPES, MAX_DIRECTORY_BYTES
from packages.pipeline_api.records import MAX_BYTES
from packages.pipeline_api.tests.test_application import run
from packages.pipelines import PipelineScope
from packages.session_api.application import _SESSION, _CSRF
from packages.session_api.tests.test_application import cookie_header
from packages.workspaces.model import MAX_REVISION

SCOPE = PipelineScope('alpha', 'example-ci', 'build')
BASE = '/api/workspaces/alpha/pipelines/example-ci/build'


class PipelineDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.now = fixtures.NOW
        host, self.policy, self.login, self.exchange = fixtures.setup(self.temp.name, clock=lambda: self.now)
        self.app = ApplicationUI(host=host); self.path = Path(self.temp.name) / 'policy.sqlite'
        self.sign_in()

    def sign_in(self, subject='admin-sub'):
        self.session = fixtures.session(self.login, self.exchange, subject)
        self.cookies = cookie_header({_SESSION: self.session.token.value, _CSRF: self.session.csrf.value})

    def sql(self, statement, args=()):
        with sqlite3.connect(self.path) as db:
            return db.execute(statement, args).fetchall()

    def call(self, workspace='alpha', suffix='', **kwargs):
        return request(self.app, f'/api/workspaces/{workspace}/pipelines/scopes' + suffix,
                       cookies=kwargs.pop('cookies', self.cookies), **kwargs)

    def write(self, text, scope=SCOPE, revision=1):
        self.sql('INSERT INTO resources VALUES (?,?,?,?,?) ON CONFLICT(workspace_id,module_key,id) '
                 'DO UPDATE SET text=excluded.text,revision=excluded.revision',
                 (scope.workspace_id, 'pipelines', resource_id(scope), text, revision))

    def test_minimal_projection_valid_empty_and_no_provider_configuration(self):
        seed_directory(self.policy)
        self.sign_in('reader-sub')
        result = self.call(); self.assertEqual(result['status'], 200)
        self.assertEqual(result['body']['data'], {'workspace_id': 'alpha', 'items': [
            {'scope': SCOPE.as_dict(), 'snapshot_revision': 1},
            {'scope': PipelineScope('alpha', 'example-ci', 'empty').as_dict(), 'snapshot_revision': 1}], 'next_after': None})
        for key in ('total', 'runs', 'origin', 'credential', 'observed_at', 'stages', 'job_count', 'provider'):
            self.assertNotIn(key, result['raw'].decode())
        self.assertEqual(request(self.app, BASE.replace('build', 'empty') + '/runs', cookies=self.cookies)['body']['data']['items'], [])
        self.sql("INSERT INTO modules VALUES ('alpha','pipeline_connections',1,0,'enabled',1)")
        self.sql("INSERT INTO resources VALUES ('alpha','pipeline_connections',?,'sensitive configuration never parsed',1)", (resource_id(SCOPE),))
        self.assertEqual(self.call()['body'], result['body'])

    def test_three_workspaces_identical_pipeline_run_and_task_ids_never_mix(self):
        seed_directory(self.policy)
        for workspace in ('alpha', 'beta', 'commons'):
            data = self.call(workspace)['body']['data']
            self.assertTrue(all(i['scope']['workspace_id'] == workspace for i in data['items']))
            base = BASE.replace('/alpha/', '/' + workspace + '/')
            detail = request(self.app, base + '/runs/run-1', cookies=self.cookies)['body']['data']
            tasks = request(self.app, base + '/runs/run-1/tasks?revision=1', cookies=self.cookies)['body']['data']
            durations = request(self.app, base + '/durations', cookies=self.cookies)['body']['data']
            self.assertEqual(detail['run']['id'], 'run-1')
            self.assertEqual(tasks['items'][0]['id'], 'unit')
            self.assertEqual(detail['run']['stages'][0]['name'], workspace + ' checks')
            self.assertTrue(tasks['items'][0]['name'].startswith(workspace))
            self.assertEqual(durations['groups'][0]['stage_name'], workspace + ' checks')
        self.sign_in('reader-sub')
        self.assertEqual(self.call('beta')['status'], 404); self.assertEqual(self.call('commons')['status'], 404)

    def test_roles_nonmember_operator_write_only_disabled_and_workspace_lifecycle(self):
        for role in ('viewer', 'developer', 'admin'):
            self.sql("UPDATE memberships SET role=? WHERE workspace_id='alpha' AND principal_id='reader'", (role,))
            self.sign_in('reader-sub'); self.assertEqual(self.call()['status'], 200)
        self.sign_in('outsider-sub'); self.assertEqual(self.call()['status'], 404)
        self.sign_in()  # platform operator, but not beta/commons member
        for workspace in ('beta', 'commons'):
            self.assertEqual(self.call(workspace)['status'], 404)
        self.sql("UPDATE modules SET can_read=0 WHERE workspace_id='alpha' AND key='pipelines'")
        self.assertEqual(self.call()['status'], 403)
        self.sql("UPDATE modules SET can_read=1,state='disabled' WHERE workspace_id='alpha' AND key='pipelines'")
        self.assertEqual(self.call()['status'], 409)
        self.sql("UPDATE modules SET state='enabled' WHERE workspace_id='alpha' AND key='pipelines'")
        self.sql("UPDATE workspaces SET state='suspended' WHERE id='alpha'")
        self.assertEqual(self.call()['status'], 409)
        self.sql("UPDATE workspaces SET state='archived' WHERE id='alpha'")
        self.assertEqual(self.call()['status'], 200)
        self.assertEqual(request(self.app, BASE + '/runs', cookies=self.cookies)['status'], 200)

    def test_current_read_before_directory_projection_and_no_other_workspace_decode(self):
        # Raw text was already loaded by SQLite's existing loader. Only the
        # additional directory SELECT and codec are behind the module decision.
        self.write('bad but bounded', PipelineScope('beta', 'example-ci', 'build'))
        self.assertEqual(self.call()['status'], 200)
        projections = []; connect = self.policy._connect
        def traced():
            db = connect(); db.set_trace_callback(projections.append); return db
        self.sql("UPDATE modules SET can_read=0 WHERE workspace_id='alpha' AND key='pipelines'")
        with patch.object(self.policy, '_connect', side_effect=traced), patch('packages.pipeline_api.discovery.snapshot_scope', side_effect=AssertionError('no directory decode')):
            self.assertEqual(self.call()['status'], 403)
        self.assertTrue(any('FROM resources' in s and 'substr' not in s for s in projections))
        self.assertFalse(any('substr(CAST(id AS BLOB),1,74)' in s for s in projections))

    def test_namespace_generic_resources_allowed_but_malformed_pipeline_keys_fail(self):
        original = self.call()['body']
        for i in range(300):
            self.sql("INSERT INTO resources VALUES ('alpha','pipelines',?,'not a snapshot',1)", (f'notes-{i:03d}',))
        plan = self.sql("EXPLAIN QUERY PLAN SELECT id FROM resources WHERE workspace_id='alpha' "
                        "AND module_key='pipelines' AND id >= 'pipeline-' AND id < 'pipeline.' ORDER BY id LIMIT 257")
        self.assertTrue(any('id>?' in row[3] and 'id<?' in row[3] for row in plan))
        self.assertEqual(self.call()['body'], original)
        for key in ('pipeline-bad', 'pipeline-' + 'A' * 64, 'pipeline-' + 'a' * 65, 'pipeline-'):
            with self.subTest(key=key):
                self.sql("INSERT INTO resources VALUES ('alpha','pipelines',?,'not a snapshot',1)", (key,))
                self.assertEqual(self.call()['body'], {'error': 'pipeline_data_unavailable'})
                self.sql("DELETE FROM resources WHERE workspace_id='alpha' AND id=?", (key,))

    def test_nul_key_or_json_suffix_cannot_be_truncated_into_a_valid_snapshot(self):
        good = encode_snapshots(SCOPE, [run()])
        key = resource_id(SCOPE)
        self.sql("UPDATE resources SET id=? WHERE workspace_id='alpha' AND module_key='pipelines' AND id=?", (key + '\x00junk', key))
        self.assertEqual(self.call()['body'], {'error': 'pipeline_data_unavailable'})
        self.assertEqual(request(self.app, BASE + '/runs', cookies=self.cookies)['status'], 404)
        self.sql("UPDATE resources SET id=? WHERE workspace_id='alpha' AND module_key='pipelines' AND id=?", (key, key + '\x00junk'))
        self.write(good + '\x00junk')
        self.assertEqual(self.call()['body'], {'error': 'pipeline_data_unavailable'})
        self.assertEqual(request(self.app, BASE + '/runs', cookies=self.cookies)['status'], 503)

    def test_invalid_utf8_fails_value_free_in_existing_loader(self):
        self.sql("UPDATE resources SET text=CAST(? AS TEXT) WHERE workspace_id='alpha' AND module_key='pipelines' AND id=?", (b'\xff', resource_id(SCOPE)))
        # sqlite3's TEXT decoding failure occurs in policy._load, before the
        # module callback. The existing cookie transaction maps this database
        # exception to AuthenticationUnavailable, not loader validation errors.
        for result in (self.call(), request(self.app, BASE + '/runs', cookies=self.cookies)):
            self.assertEqual((result['status'], result['body']),
                             (503, {'error': 'authentication_unavailable'}))

    def test_malformed_envelope_scope_hash_revision_future_and_duplicate_json_fail_all(self):
        good = encode_snapshots(SCOPE, [run()]); parsed = json.loads(good)
        mutations = ['bad', good.replace('"version":1', '"version":1,"version":1'),
                     good.replace('"version":1', '"version":true'),
                     json.dumps({**parsed, 'scope': {**SCOPE.as_dict(), 'workspace_id': 'beta'}}),
                     json.dumps({**parsed, 'scope': {**SCOPE.as_dict(), 'pipeline_id': 'different'}}),
                     json.dumps({**parsed, 'scope': {**SCOPE.as_dict(), 'extra': 'discard me'}}),
                     encode_snapshots(SCOPE, [run(observed='2027-01-01T00:00:00Z')]),
                     good.replace('"status":"failure"', '"status":"invented"')]
        for raw in mutations:
            with self.subTest(raw=raw[:40]):
                self.write(raw); self.assertEqual(self.call()['body'], {'error': 'pipeline_data_unavailable'})
        self.write(good, revision=MAX_REVISION)
        self.assertEqual(self.call()['body']['data']['items'][0]['snapshot_revision'], MAX_REVISION)
        self.write(good, revision=MAX_REVISION + 1)
        self.assertEqual(self.call()['body'], {'error': 'storage_unavailable'})  # existing loader's revision validation

    def test_true_identity_subject_aba_and_old_membership_never_revive(self):
        from packages.workspaces.tests.identity_fixture import simulate_identity
        self.assertEqual(self.call()['status'], 200)
        with sqlite3.connect(self.path) as db:
            simulate_identity(db, "UPDATE principals SET subject='temporary-sub' WHERE id='admin'")
            simulate_identity(db, "UPDATE principals SET subject='admin-sub' WHERE id='admin'")
        self.assertEqual(self.sql("SELECT generation FROM principals WHERE id='admin'")[0][0], 3)
        self.assertEqual(self.call()['body'], {'error': 'authentication_required'})
        self.sign_in(); self.assertEqual(self.call()['status'], 404)  # fresh cookie is not a new membership

    def test_disabled_identity_cannot_keep_discovering(self):
        from packages.workspaces.tests.identity_fixture import simulate_identity
        with sqlite3.connect(self.path) as db:
            simulate_identity(db, "UPDATE principals SET active=0 WHERE id='admin'")
        self.assertEqual(self.call()['body'], {'error': 'authentication_required'})

    def test_expiry_at_final_guard_rejects_complete_directory_and_invalid_auth(self):
        for kwargs in ({'cookies': ''}, {'extra': {'HTTP_AUTHORIZATION': 'Bearer synthetic'}}, {'cookies': '', 'extra': {'HTTP_X_USER': 'admin'}}):
            self.assertEqual(self.call(**kwargs)['status'], 401)
        original = module.directory
        def expire(*args, **kwargs):
            result = original(*args, **kwargs); self.assertTrue(result.value)
            self.now = self.session.expires_at; return result
        with patch.object(module, 'directory', side_effect=expire):
            self.assertEqual(self.call()['body'], {'error': 'authentication_required'})
        self.sign_in(); self.login.revoke_subject('admin-sub'); self.assertEqual(self.call()['status'], 401)

    def test_selection_is_not_authority_and_cursor_absence_is_not_empty_success(self):
        seed_directory(self.policy); seed_pages(self.policy)
        data = self.call()['body']['data']; self.assertEqual(len(data['items']), 25)
        after = data['next_after']; self.assertRegex(after, r'^dcr1_[A-Za-z0-9_-]{43}$')
        self.assertEqual(data['items'][-1]['scope']['pipeline_id'], 'p022')
        last = self.call(suffix='?after=' + after)['body']['data']
        self.assertEqual(len(last['items']), 3); self.assertIsNone(last['next_after'])
        for invalid in ('example-ci~absent', 'hidden~scope'):
            self.assertEqual(self.call(suffix='?after=' + invalid)['body'], {'error': 'scope_directory_changed'})
        scope = PipelineScope(**data['items'][-1]['scope'])
        self.sql("DELETE FROM resources WHERE workspace_id='alpha' AND id=?", (resource_id(scope),))
        self.assertEqual(self.call(suffix='?after=' + after)['status'], 409)
        self.policy.set_membership('second', 'alpha', 'admin', role='admin', state='revoked', expected_revision=1)
        self.assertEqual(self.call()['status'], 404)
        self.assertEqual(request(self.app, BASE + '/runs', cookies=self.cookies)['status'], 404)

    def test_actual_scope_capacity_256_then_257_no_partial_decode(self):
        self.sql("DELETE FROM resources WHERE workspace_id='alpha' AND module_key='pipelines'")
        for n in range(MAX_SCOPES):
            scope = PipelineScope('alpha', 'ci', f'p{n:03d}'); self.write(encode_snapshots(scope, []), scope)
        self.assertEqual(len(self.call()['body']['data']['items']), 25)
        scope = PipelineScope('alpha', 'ci', 'extra'); self.write(encode_snapshots(scope, []), scope)
        with patch('packages.pipeline_api.discovery.snapshot_scope', side_effect=AssertionError('no partial parsing')):
            self.assertEqual(self.call()['body'], {'error': 'scope_directory_unavailable'})

    @staticmethod
    def utf8_record(scope, size):
        row = run(scope); row['stages'][0]['name'] = '检查阶段'
        raw = json.dumps({'version': 1, 'scope': scope.as_dict(), 'runs': [row]}, ensure_ascii=False, separators=(',', ':'))
        return raw + ' ' * (size - len(raw.encode('utf-8')))

    def test_actual_per_record_utf8_10000_bytes_not_characters(self):
        raw = self.utf8_record(SCOPE, MAX_BYTES)
        self.assertLess(len(raw), MAX_BYTES); self.assertEqual(len(raw.encode()), MAX_BYTES)
        self.write(raw); self.assertEqual(self.call()['status'], 200)
        self.write(raw + ' '); self.assertLess(len(raw + ' '), MAX_BYTES)
        self.assertEqual(self.call()['body'], {'error': 'pipeline_data_unavailable'})

    def test_actual_aggregate_utf8_budget_exact_then_one_byte_over(self):
        self.sql("DELETE FROM resources WHERE workspace_id='alpha' AND module_key='pipelines'")
        used = 0
        for n in range((MAX_DIRECTORY_BYTES + MAX_BYTES - 1) // MAX_BYTES):
            scope = PipelineScope('alpha', 'ci', f'p{n:03d}')
            raw = self.utf8_record(scope, min(MAX_BYTES, MAX_DIRECTORY_BYTES - used))
            self.write(raw, scope); used += len(raw.encode())
        self.assertEqual(used, MAX_DIRECTORY_BYTES); self.assertEqual(self.call()['status'], 200)
        self.write(raw + ' ', scope)
        self.assertEqual(self.call()['body'], {'error': 'scope_directory_unavailable'})

    def test_empty_directory_not_empty_snapshot_and_strict_query(self):
        self.sql("DELETE FROM resources WHERE workspace_id='alpha' AND module_key='pipelines'")
        self.assertEqual(self.call()['body']['data'], {'workspace_id': 'alpha', 'items': [], 'next_after': None})
        for suffix in ('?after=', '?after=a', '?after=a~b~c', '?after=a~b&after=c~d', '?limit=25', '?after=a%0A~b'):
            self.assertEqual(self.call(suffix=suffix)['status'], 400 if suffix.startswith('?limit=') else 409)
        self.assertEqual(self.call(method='POST', body={}, origin=fixtures.ORIGIN, csrf=self.session.csrf.value)['status'], 404)
        self.assertEqual(self.call(workspace='x' * 1001)['status'], 400)
