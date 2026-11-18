"""Finite I1/I2 storage compatibility. No HTTP transfer or conversion coverage."""
from contextlib import closing, redirect_stdout
from hashlib import sha256
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from packages.service_runtime import (
    RuntimeFailure, backup, check, create_application, initialize, load_config,
    restore, verify_backup)
from packages.service_runtime import _layout_v1 as frozen
from packages.service_runtime.__main__ import main
from packages.service_runtime.runtime import (
    Lease, canonical, connection, inspect, manifest, now_micros, write_new)
from packages.service_runtime import schema
from packages.workspaces import SQLiteWorkspaceService
from packages.workspaces.sqlite import _PRINCIPAL_GUARDS
from packages.service_runtime.tests.layout_profile_fixture import (
    append_transfer, create_policy, load_policy)
from packages.workspace_admin_api import transfer_records


class LayoutProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / 'state'
        self.config = self.root / 'config.json'
        self.value = {'version': 1, 'origin': 'https://app.example.invalid',
            'data_directory': str(self.directory), 'modules': ['community'],
            'artifact_administration': False,
            'login': {'mode': 'disabled', 'issuer': 'https://identity.example.invalid',
                      'audience': 'synthetic-client'}}
        self.save()

    def save(self):
        self.config.write_text(json.dumps(self.value))
        self.config.chmod(0o600)

    def snapshot(self, path=None):
        target = self.directory if path is None else path
        return {p.name: p.read_bytes() for p in target.iterdir()}

    def login_state(self):
        with closing(sqlite3.connect((self.directory / 'login.sqlite').as_uri() + '?mode=ro',
                                     uri=True)) as db:
            metadata = db.execute('SELECT id,binding,clock FROM metadata').fetchall()
            self.assertEqual(len(metadata), 1)
            self.assertEqual(len(metadata[0]), 3)
            self.assertIs(type(metadata[0][2]), int)
            state = (db.execute('SELECT * FROM sqlite_schema ORDER BY type,name').fetchall(),
                     db.execute('PRAGMA application_id').fetchone(),
                     db.execute('PRAGMA user_version').fetchone(),
                     db.execute('PRAGMA journal_mode').fetchone(), metadata[0][:2],
                     db.execute('SELECT * FROM attempts ORDER BY state').fetchall(),
                     db.execute('SELECT * FROM sessions ORDER BY token').fetchall())
            return state, metadata[0][2]

    def synthetic_receipt(self, *, workspace_ids=('alpha',)):
        # Explicit test-only replacement of the empty policy by a trusted fresh
        # synthetic bootstrap with the SAME exact I2 schema. Never a migration.
        path = self.root / 'synthetic-policy.sqlite'
        create_policy(path, workspace_ids=workspace_ids)
        (self.directory / 'policy.sqlite').write_bytes(path.read_bytes())
        path.unlink()
        store = SQLiteWorkspaceService(self.directory / 'policy.sqlite')
        return store, append_transfer(store)

    def reject_installed(self, expected_code='unsupported_layout'):
        for name, operation in (
                ('check', lambda: check(self.config)),
                ('factory', lambda: create_application(self.config)),
                ('backup', lambda: backup(self.config, self.root / 'rejected-backup'))):
            with self.subTest(entrypoint=name):
                before = self.snapshot()
                config_before = self.config.read_bytes()
                entries = set(self.root.iterdir())
                with patch('packages.service_runtime.backup.allocate_directory') as allocator:
                    with self.assertRaises(RuntimeFailure) as error:
                        operation()
                    code = 'runtime_unavailable' if name == 'factory' else expected_code
                    self.assertEqual(error.exception.code, code)
                    self.assertEqual(str(error.exception), code)
                    allocator.assert_not_called()
                self.assertEqual(before, self.snapshot())
                self.assertEqual(config_before, self.config.read_bytes())
                self.assertEqual(entries, set(self.root.iterdir()))
                with Lease(self.directory, exclusive=True) as lease:
                    self.assertTrue(lease.valid())
                self.assertEqual(before, self.snapshot())

    def reject_checkpoint(self, source):
        before = self.snapshot(source)
        self.value['data_directory'] = str(self.root / 'rejected-restore')
        self.save()
        entries = set(self.root.iterdir())
        with patch('packages.service_runtime.backup.allocate_directory') as allocator:
            for operation in (lambda: verify_backup(self.config, source),
                              lambda: restore(self.config, source)):
                with self.assertRaises(RuntimeFailure) as error:
                    operation()
                self.assertEqual(error.exception.code, 'backup_invalid')
                self.assertEqual(str(error.exception), 'backup_invalid')
            allocator.assert_not_called()
        self.assertEqual(before, self.snapshot(source))
        self.assertEqual(entries, set(self.root.iterdir()))
        self.assertFalse((self.root / 'rejected-restore').exists())

    def test_frozen_i1_matches_every_live_definition_and_numeric_id(self):
        # Full independent baseline raw-definition golden, not merely counts.
        from packages.workspaces import sqlite as workspace
        from packages.login_state import core as login
        from packages.workspace_admin_api.requests import _CREATE as admin
        from packages.workspace_invitations.store import _CREATE as invites
        from packages.community_api.records import _SCHEMA as community
        from packages.artifact_sync.records import _CREATE as artifacts, _EVENT_CREATE as events
        from packages.assistant_history.store import _TABLES as history
        from packages.responsibility_api.records import _SCHEMA as responsibility
        from packages.gui_smoke.store import _SCHEMA as gui
        from packages.gui_smoke.grants import _SCHEMA as grants
        from packages.research_assets.store import _SCHEMA as assets
        groups = [('WORKSPACE', workspace._SCHEMA), ('LOGIN', login._SCHEMA),
            ('ADMIN', (admin,)), ('INVITES', (invites,)), ('COMMUNITY', (community,)),
            ('ARTIFACTS', (artifacts,)), ('ARTIFACT_EVENTS', (events,)),
            ('HISTORY', tuple(history.values())), ('RESPONSIBILITY', (responsibility,)),
            ('GUI', (gui,)), ('GUI_GRANTS', (grants,)), ('ASSETS', assets)]
        constants = [('policy', 'application_id', '_APPLICATION_ID', workspace._APPLICATION_ID),
                     ('policy', 'user_version', '_SCHEMA_VERSION', workspace._SCHEMA_VERSION),
                     ('login', 'application_id', '_APP', login._APP),
                     ('login', 'user_version', '_VERSION', login._VERSION)]
        payload = {'format': 'ms-collab-i1-raw-literals-v1',
            'constants': [dict(database=d, meaning=m, runtime_import_name=n, value=v)
                          for d, m, n, v in constants],
            'groups': [dict(group=n, statements=list(values)) for n, values in groups]}
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
        self.assertEqual(sha256(raw).hexdigest(),
                         '3449950d7674518306c2ec8e9ef4e02b2d02c6567d55d9f7bcff7b92e7cd1616')
        self.assertEqual(frozen.WORKSPACE, tuple(workspace._SCHEMA))
        self.assertEqual(frozen.LOGIN, tuple(login._SCHEMA))
        self.assertEqual(frozen.MODULE_SCHEMA, tuple(s for _, group in groups[2:] for s in group))
        for _, _, name, value in constants:
            self.assertEqual(getattr(frozen, name), value)
        self.assertEqual(tuple(v for _, _, _, v in constants), (1297302359, 3, 1297304659, 2))
        self.assertEqual(schema.WORKSPACE, frozen.WORKSPACE)
        self.assertEqual(schema.MODULE_SCHEMA, frozen.MODULE_SCHEMA)
        self.assertEqual(schema.LOGIN, frozen.LOGIN)
        self.assertEqual(schema.POLICY_DIGEST, schema.expected((*frozen.WORKSPACE, *frozen.MODULE_SCHEMA)))
        self.assertEqual(schema.LOGIN_DIGEST, schema.expected(frozen.LOGIN))

    def test_finite_immutable_profiles_and_exact_int_selectors(self):
        one, two = schema.layout_profile(1), schema.layout_profile(2)
        self.assertEqual((one.format, two.format), (1, 2))
        self.assertEqual(one.module_schema, frozen.MODULE_SCHEMA)
        self.assertEqual(two.module_schema, (*frozen.MODULE_SCHEMA, *transfer_records.SCHEMA))
        # Published I2 must not drift when its format-v1 record module changes.
        self.assertEqual(len(transfer_records.SCHEMA), 1)
        self.assertEqual(sha256(transfer_records.SCHEMA[0].encode('utf-8')).hexdigest(),
                         'a61abc7521fed5cfa83e3dbda04f35c5b937030b6663d63f032bd0bbb163fa52')
        self.assertEqual(two.policy_schema, (*frozen.WORKSPACE, *two.module_schema))
        self.assertEqual(one.policy_digest, schema.POLICY_DIGEST)
        self.assertNotEqual(one.policy_digest, two.policy_digest)
        self.assertEqual(one.login_digest, two.login_digest)
        for profile in (one, two):
            with self.assertRaises((AttributeError, TypeError)):
                profile.format = 99
        for invalid in (True, False, 1.0, 2.0, '2', None, 0, 3):
            with self.subTest(selector=invalid):
                with self.assertRaises((ValueError, RuntimeFailure)):
                    schema.layout_profile(invalid)
                with patch('packages.service_runtime.runtime.allocate_directory') as allocator:
                    with self.assertRaises((ValueError, RuntimeFailure)):
                        initialize(self.config, layout=invalid)
                    allocator.assert_not_called()
                self.assertFalse(self.directory.exists())

    def test_default_i1_and_explicit_i2_preserve_status_and_factory_clock_contract(self):
        for layout in (1, 2):
            with self.subTest(layout=layout):
                self.directory = self.root / ('state-' + str(layout))
                self.value['data_directory'] = str(self.directory)
                self.save()
                result = initialize(self.config) if layout == 1 else initialize(self.config, layout=2)
                self.assertEqual(result, {'installation_valid': True, 'login_enabled': False,
                    'live_provider_verified': False, 'production_ready': False, 'modules': ['community']})
                expected = manifest(load_config(self.config), layout=layout)
                self.assertEqual((self.directory / 'installation.json').read_bytes(), canonical(expected))
                self.assertEqual(set(expected), {'format', 'binding', 'policy_schema', 'login_schema'})
                self.assertEqual(expected['format'], layout)
                before = self.snapshot()
                with closing(connection(self.directory)) as db:
                    inspection = inspect(load_config(self.config), db)
                    self.assertEqual(inspection.profile, schema.layout_profile(layout))
                    self.assertIs(type(inspection.clock_floor), int)
                    self.assertEqual(schema.schema_digest(db), inspection.profile.policy_digest)
                self.assertTrue(check(self.config)['installation_valid'])
                self.assertEqual(before, self.snapshot())
                state, floor = self.login_state()
                app = create_application(self.config)
                try:
                    self.assertTrue(app.lease.valid())
                finally:
                    app.close()
                finished = now_micros()
                after_state, after_floor = self.login_state()
                self.assertEqual(state, after_state)
                self.assertIs(type(floor), int)
                self.assertIs(type(after_floor), int)
                self.assertLessEqual(floor, after_floor)
                self.assertLessEqual(after_floor, finished)
                after = self.snapshot()
                self.assertEqual(set(before), set(after))
                self.assertEqual({k: v for k, v in before.items() if k != 'login.sqlite'},
                                 {k: v for k, v in after.items() if k != 'login.sqlite'})
                with Lease(self.directory, exclusive=True) as lease:
                    self.assertTrue(lease.valid())
                self.assertEqual(after, self.snapshot())

    def test_cli_only_explicit_init_layout_and_no_converter(self):
        for args, layout in (([], 1), (['--layout', '2'], 2)):
            self.directory = self.root / ('cli-' + str(layout))
            self.value['data_directory'] = str(self.directory)
            self.save()
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--config', str(self.config), 'init', *args]), 0)
            self.assertEqual(json.loads((self.directory / 'installation.json').read_text())['format'], layout)
        for command in (['init', '--layout', '3'], ['init', '--layout', '2.0'],
                        ['check', '--layout', '2'], ['convert-backup']):
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['--config', str(self.config), *command]), 2)
            self.assertEqual(json.loads(output.getvalue())['error'], 'invalid_arguments')

    def test_i2_manifest_exact_keys_types_values_reject_before_backup_allocation(self):
        initialize(self.config, layout=2)
        expected = manifest(load_config(self.config), layout=2)
        cases = [dict(expected, format=x) for x in (True, 2.0, '2', None, 0, 3)]
        cases += [{k: v for k, v in expected.items() if k != missing} for missing in expected]
        cases += [dict(expected, extra=1), dict(expected, format=1),
                  dict(expected, policy_schema=schema.POLICY_DIGEST),
                  dict(expected, login_schema='0' * 64), dict(expected, binding='wrong')]
        for index, value in enumerate(cases):
            with self.subTest(case=index):
                (self.directory / 'installation.json').write_bytes(canonical(value))
                self.reject_installed()

    def test_i1_i2_schema_cannot_be_admitted_by_relabeling_manifest(self):
        initialize(self.config)
        (self.directory / 'installation.json').write_bytes(canonical(manifest(load_config(self.config), layout=2)))
        self.reject_installed('invalid_storage')

    def test_i2_unknown_table_trigger_index_and_database_version_reject_without_repair(self):
        initialize(self.config, layout=2)
        original = (self.directory / 'policy.sqlite').read_bytes()
        statements = ('CREATE TABLE unexpected (x TEXT)',
            'CREATE TRIGGER unexpected AFTER INSERT ON audit BEGIN SELECT 1; END',
            'CREATE INDEX unexpected ON workspace_admin_transfers_v1(replacement_id)',
            'PRAGMA user_version=99')
        for sql in statements:
            with self.subTest(sql=sql):
                (self.directory / 'policy.sqlite').write_bytes(original)
                with sqlite3.connect(self.directory / 'policy.sqlite') as db:
                    db.execute(sql)
                self.reject_installed('invalid_storage')

    def test_b1_and_b2_round_trip_exact_envelopes_and_fresh_login(self):
        for layout in (1, 2):
            with self.subTest(layout=layout):
                self.directory = self.root / ('round-' + str(layout))
                self.value['data_directory'] = str(self.directory)
                self.save()
                initialize(self.config, layout=layout)
                if layout == 2:
                    store, _ = self.synthetic_receipt(workspace_ids=('alpha', 'beta'))
                    append_transfer(store, workspace='beta', number=1001)
                    # Two partitions crossing the 64-row page boundary. Each
                    # inverse core transfer is explicit, with no pin recapture.
                    for workspace, offset in (('alpha', 0), ('beta', 1000)):
                        for index in range(1, 65):
                            store.transfer_admin('member', workspace, 'admin',
                                expected_revision=2 * index, replacement_revision=2 * index)
                            append_transfer(store, workspace=workspace, number=offset + index + 1)
                with sqlite3.connect(self.directory / 'login.sqlite') as db:
                    db.execute("INSERT INTO attempts VALUES('state','browser','SYNTHETIC_VERIFIER',1,2,NULL,NULL)")
                    db.execute("INSERT INTO sessions VALUES('session','csrf','synthetic-subject',1,2,NULL,NULL,NULL)")
                before = self.snapshot()
                source = self.root / ('backup-' + str(layout))
                self.assertTrue(backup(self.config, source)['backup_valid'])
                self.assertEqual(before, self.snapshot())
                saved = self.snapshot(source)
                meta = json.loads(saved['backup.json'])
                keys = {'format', 'binding', 'policy_schema', 'login_schema', 'policy_sha256',
                        'policy_bytes', 'clock_floor', 'created_at', 'sessions_excluded'}
                self.assertEqual(set(meta), keys if layout == 1 else keys | {'installation_format'})
                self.assertEqual(meta['format'], layout)
                self.assertIs(type(meta['format']), int)
                if layout == 2:
                    self.assertEqual(meta['installation_format'], 2)
                self.assertEqual(set(saved), {'policy.sqlite', 'backup.json'})
                self.assertTrue(verify_backup(self.config, source)['backup_valid'])
                self.value['data_directory'] = str(self.root / ('restored-' + str(layout)))
                self.save()
                self.assertFalse(restore(self.config, source)['sessions_restored'])
                self.directory = Path(self.value['data_directory'])
                self.assertEqual((self.directory / 'policy.sqlite').read_bytes(), saved['policy.sqlite'])
                self.assertEqual(json.loads((self.directory / 'installation.json').read_text()),
                                 manifest(load_config(self.config), layout=layout))
                self.assertTrue(check(self.config)['installation_valid'])
                if layout == 2:
                    with closing(sqlite3.connect(self.directory / 'policy.sqlite')) as db:
                        self.assertEqual(transfer_records.validate_all(db, load_policy(db)), 130)
                with sqlite3.connect(self.directory / 'login.sqlite') as db:
                    for table in ('attempts', 'sessions'):
                        self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)
                    self.assertGreaterEqual(db.execute('SELECT clock FROM metadata').fetchone()[0], meta['clock_floor'])
                self.assertEqual(saved, self.snapshot(source))

    def test_b2_cross_profile_or_malformed_envelope_rejects_before_restore_allocation(self):
        initialize(self.config, layout=2)
        source = self.root / 'backup'
        backup(self.config, source)
        expected = json.loads((source / 'backup.json').read_text())
        cases = [dict(expected, format=x) for x in (True, 2.0, '2', 1, 3)]
        cases += [dict(expected, installation_format=x) for x in (True, 2.0, '2', 1, 3)]
        cases += [{k: v for k, v in expected.items() if k != missing} for missing in expected]
        cases += [dict(expected, extra=1), dict(expected, policy_schema=schema.POLICY_DIGEST)]
        for index, value in enumerate(cases):
            with self.subTest(case=index):
                (source / 'backup.json').write_bytes(canonical(value))
                self.reject_checkpoint(source)

    def test_b1_extra_installation_selector_is_not_an_upgrade(self):
        initialize(self.config)
        source = self.root / 'backup'
        backup(self.config, source)
        value = json.loads((source / 'backup.json').read_text())
        value['installation_format'] = 2
        (source / 'backup.json').write_bytes(canonical(value))
        self.reject_checkpoint(source)

    def test_stale_generation_realm_revoked_and_null_pins_remain_valid_history(self):
        initialize(self.config, layout=2)
        _, retained = self.synthetic_receipt()
        # Explicit synthetic future directory/lifecycle changes, NOT supported
        # production identity mutation or automatic ownership recapture.
        with sqlite3.connect(self.directory / 'policy.sqlite') as db:
            db.execute('DROP TRIGGER principal_authority_closed')
            db.execute("UPDATE principals SET generation=2,authority_epoch=2,active=0 WHERE id='admin'")
            db.execute(_PRINCIPAL_GUARDS[0])
            db.execute('DROP TRIGGER policy_authority_monotonic')
            db.execute('DROP TRIGGER authority_marker_update_closed')
            db.execute("UPDATE metadata SET epoch=epoch+1,realm=?", ('f' * 32,))
            db.execute("UPDATE authority_migrations SET realm=?", ('f' * 32,))
            for definition in frozen.WORKSPACE:
                if definition.startswith(('CREATE TRIGGER policy_authority_monotonic ',
                                          'CREATE TRIGGER authority_marker_update_closed ')):
                    db.execute(definition)
            db.execute("UPDATE memberships SET state='revoked',revision=revision+1 WHERE principal_id='admin'")
            db.execute("UPDATE membership_authority SET realm=NULL,issuer=NULL,subject=NULL,generation=NULL WHERE principal_id='admin'")
        before = self.snapshot()
        with patch('packages.workspaces.authority.authority_current', side_effect=AssertionError('history is not authority')):
            self.assertTrue(check(self.config)['installation_valid'])
            source = self.root / 'stale-backup'
            backup(self.config, source)
            self.assertTrue(verify_backup(self.config, source)['backup_valid'])
            self.value['data_directory'] = str(self.root / 'stale-restored')
            self.save()
            restore(self.config, source)
        self.assertEqual(before, self.snapshot())
        with sqlite3.connect(Path(self.value['data_directory']) / 'policy.sqlite') as db:
            self.assertEqual(transfer_records.validate_all(db, load_policy(db)), 1)
            row = db.execute('SELECT intent,provenance FROM workspace_admin_transfers_v1').fetchone()
            self.assertEqual(row, (retained['intent'], retained['provenance']))
            self.assertEqual(db.execute("SELECT generation,active FROM principals WHERE id='admin'").fetchone(), (2, 0))
            self.assertEqual(db.execute("SELECT realm,issuer,subject,generation FROM membership_authority WHERE principal_id='admin'").fetchone(), (None,) * 4)

    def test_corrupt_i2_receipt_rejects_installed_and_rehashed_backup_without_allocation(self):
        initialize(self.config, layout=2)
        self.synthetic_receipt()
        with sqlite3.connect(self.directory / 'policy.sqlite') as db:
            db.execute("UPDATE workspace_admin_transfers_v1 SET request_digest=?", ('0' * 64,))
        self.reject_installed('invalid_storage')
        # A matching outer checksum does not substitute for receipt validation.
        source = self.root / 'synthetic-corrupt-backup'
        source.mkdir(mode=0o700)
        content = (self.directory / 'policy.sqlite').read_bytes()
        write_new(source / 'policy.sqlite', content)
        floor = self.login_state()[1]
        meta = {'format': 2, 'installation_format': 2,
            'binding': load_config(self.config).binding.fingerprint(),
            'policy_schema': schema.layout_profile(2).policy_digest,
            'login_schema': schema.LOGIN_DIGEST, 'policy_sha256': sha256(content).hexdigest(),
            'policy_bytes': len(content), 'clock_floor': floor,
            'created_at': max(floor, now_micros()), 'sessions_excluded': True}
        write_new(source / 'backup.json', canonical(meta))
        self.reject_checkpoint(source)

    def test_i2_backup_lease_existing_and_overlapping_targets_preserve_source(self):
        initialize(self.config, layout=2)
        app = create_application(self.config)
        try:
            before = self.snapshot()
            with self.assertRaises(RuntimeFailure):
                backup(self.config, self.root / 'busy')
            self.assertFalse((self.root / 'busy').exists())
            self.assertEqual(before, self.snapshot())
        finally:
            app.close()
        for destination in (self.directory, self.directory / 'nested', self.root):
            before = self.snapshot()
            with self.assertRaises(RuntimeFailure):
                backup(self.config, destination)
            self.assertEqual(before, self.snapshot())
        source = self.root / 'backup'
        backup(self.config, source)
        saved = self.snapshot(source)
        for target in (self.directory, source, source / 'nested'):
            self.value['data_directory'] = str(target)
            self.save()
            with self.assertRaises(RuntimeFailure):
                restore(self.config, source)
            self.assertEqual(saved, self.snapshot(source))

    def test_i2_restore_before_and_after_manifest_publication_have_distinct_outcomes(self):
        initialize(self.config, layout=2)
        self.synthetic_receipt()
        source = self.root / 'backup'
        backup(self.config, source)
        saved = self.snapshot(source)
        for after_publication in (False, True):
            target = self.root / ('after-publication' if after_publication else 'before-publication')
            self.value['data_directory'] = str(target)
            self.save()
            def fail_publication(path, content):
                if path.name == 'installation.json':
                    if after_publication:
                        write_new(path, content)
                    raise OSError('synthetic publication interruption')
                write_new(path, content)
            with patch('packages.service_runtime.backup.write_new', side_effect=fail_publication):
                with self.assertRaises(RuntimeFailure):
                    restore(self.config, source)
            self.assertTrue((target / 'policy.sqlite').exists())
            self.assertEqual((target / 'policy.sqlite').read_bytes(), saved['policy.sqlite'])
            self.assertEqual((target / 'installation.json').exists(), after_publication)
            if after_publication:
                # The call failed AFTER a real publication. Retained committed
                # state is not evidence of rollback or confirmed durability.
                self.assertTrue(check(self.config)['installation_valid'])
            else:
                with self.assertRaises(RuntimeFailure):
                    check(self.config)
            with self.assertRaises(RuntimeFailure):
                restore(self.config, source)
            self.assertEqual(saved, self.snapshot(source))


if __name__ == '__main__':
    unittest.main()
