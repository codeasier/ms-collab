# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Fresh-only preset storage, separate from identity, HTTP and backup admission."""
from contextlib import closing, redirect_stdout
from hashlib import sha256
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from packages.artifact_sync.product_presets import _CREATE as PRESET_TABLE
from packages.service_runtime import (
    RuntimeFailure, backup, check, create_application, initialize, load_config,
    restore, verify_backup,
)
from packages.service_runtime.__main__ import main
from packages.service_runtime.runtime import Lease, canonical, manifest, now_micros
from packages.service_runtime.schema import layout_profile, schema_digest
from packages.service_runtime.server import ServerError, run_server


class PresetInstallLayoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / 'service.json'
        self.state = self.root / 'state'
        self.write_config(self.state)

    def write_config(self, directory):
        self.config.write_text(json.dumps({
            'version': 1, 'origin': 'https://app.example.invalid',
            'data_directory': str(directory), 'modules': ['artifacts'],
            'artifact_administration': True,
            'login': {'mode': 'disabled', 'issuer': 'https://identity.example.invalid',
                      'audience': 'fixture-client'},
        }))
        self.config.chmod(0o600)

    def snapshot(self, directory=None):
        directory = self.state if directory is None else directory
        return {file.name: file.read_bytes() for file in directory.iterdir()}

    def cli(self, *command):
        stream = io.StringIO()
        with redirect_stdout(stream):
            result = main(['--config', str(self.config), *command])
        return result, json.loads(stream.getvalue())

    def test_i3_adds_exactly_the_reviewed_preset_table_and_freezes_existing_profiles(self):
        self.assertEqual(sha256(PRESET_TABLE.encode()).hexdigest(),
                         '368f8e48c60569eae90d717c843ee10cba9487d40fb9b1abc215bb761690a97f')
        i1, i2, i3 = (layout_profile(n) for n in (1, 2, 3))
        self.assertEqual(i3.module_schema, (*i1.module_schema, PRESET_TABLE))
        self.assertEqual(i3.policy_schema, (*i1.policy_schema, PRESET_TABLE))
        self.assertEqual([p.policy_digest for p in (i1, i2, i3)], [
            '210f7e029e33b6649767fd5d73538a4576bf943cd3f41ee5893d823b10b720ef',
            'd082a3cf5b90d0946fddfe8f2967e6ad0ea44062e8a50f74e2e36f726c7439e1',
            'b231ec015f9c5ab8ec2abac49df23a4b6c43db44763f437c39f6a64af0c2f513',
        ])
        self.assertEqual({p.login_digest for p in (i1, i2, i3)},
                         {'72628a1587459bb08854795e5dd2396fa249362ef488fb8bba87bb6839ff8fdf'})
        with self.assertRaises((AttributeError, TypeError)):
            i3.format = 1

    def test_explicit_cli_init_and_read_only_check_are_not_product_acceptance(self):
        code, result = self.cli('init', '--layout', '3')
        self.assertEqual(code, 0)
        self.assertEqual(result, {'installation_valid': True, 'login_enabled': False,
            'live_provider_verified': False, 'production_ready': False, 'modules': ['artifacts']})
        installed = manifest(load_config(self.config), layout=3)
        self.assertEqual((self.state / 'installation.json').read_bytes(), canonical(installed))
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all((f.stat().st_mode & 0o777) == 0o600 for f in self.state.iterdir()))
        with closing(sqlite3.connect((self.state / 'policy.sqlite').as_uri() + '?mode=ro', uri=True)) as db:
            self.assertEqual(schema_digest(db), installed['policy_schema'])
            self.assertEqual(db.execute('SELECT sql FROM sqlite_schema WHERE name=?',
                                        ('artifact_product_presets_v1',)).fetchall(), [(PRESET_TABLE,)])
            self.assertEqual(db.execute('SELECT * FROM artifact_product_presets_v1').fetchall(), [])
            for table in ('principals', 'operators', 'workspaces', 'memberships', 'artifact_indexes_v1'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone(), (0,))
        before = self.snapshot()
        self.assertEqual(self.cli('check'), (0, result))
        self.assertEqual(self.cli('check', '--require-production'), (3, {
            'error': 'production_acceptance_required', 'installation_valid': True, 'login_enabled': False}))
        self.assertEqual(self.cli('init', '--layout', '3'), (2, {'error': 'destination_exists'}))
        self.assertEqual(self.snapshot(), before)

    def test_legacy_default_never_promotes_and_i3_cannot_be_created_by_relabeling(self):
        initialize(self.config)
        old = self.snapshot()
        self.assertEqual(json.loads(old['installation.json'])['format'], 1)
        self.assertEqual(self.cli('init', '--layout', '3'), (2, {'error': 'destination_exists'}))
        self.assertEqual(self.snapshot(), old)
        (self.state / 'installation.json').write_bytes(canonical(manifest(load_config(self.config), layout=3)))
        with self.assertRaisesRegex(RuntimeFailure, '^invalid_storage$'):
            check(self.config)

    def test_i3_tampered_schema_never_repaired(self):
        initialize(self.config, layout=3)
        with sqlite3.connect(self.state / 'policy.sqlite') as db:
            db.execute('DROP TABLE artifact_product_presets_v1')
            db.execute('CREATE TABLE artifact_product_presets_v1(preset TEXT)')
        before = self.snapshot()
        self.assertEqual(self.cli('check'), (2, {'error': 'invalid_storage'}))
        self.assertEqual(self.snapshot(), before)

    def test_install_cannot_be_served_even_with_successful_tls_setup(self):
        initialize(self.config, layout=3)
        before = self.snapshot()
        with patch('packages.service_runtime.runtime.SQLiteLoginStore') as login:
            with self.assertRaisesRegex(RuntimeFailure, '^runtime_unavailable$'):
                create_application(self.config)
            with patch('packages.service_runtime.server._tls_context', return_value=object()) as tls, \
                    patch('packages.service_runtime.server._build_application') as server:
                with self.assertRaises(ServerError):
                    run_server(self.config, '/synthetic/cert.pem', '/synthetic/key.pem', 30127)
                tls.assert_called_once()
                server.assert_not_called()
            login.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        with Lease(self.state, exclusive=True) as lease:
            self.assertTrue(lease.valid())

    def test_i3_backup_rejected_before_allocation_and_without_mutation(self):
        initialize(self.config, layout=3)
        before = self.snapshot()
        destination = self.root / 'backup'
        with patch('packages.service_runtime.backup.allocate_directory') as allocate:
            self.assertEqual(self.cli('backup', '--directory', str(destination)), (2, {
                'error': 'backup_layout_unsupported'}))
            with self.assertRaisesRegex(RuntimeFailure, '^backup_layout_unsupported$'):
                backup(self.config, destination)
            allocate.assert_not_called()
        self.assertFalse(destination.exists())
        self.assertEqual(self.snapshot(), before)
        with Lease(self.state, exclusive=True) as lease:
            self.assertTrue(lease.valid())

    def test_no_b3_and_i3_bytes_are_never_restored_as_i1_or_i2(self):
        initialize(self.config, layout=3)
        installed = manifest(load_config(self.config), layout=3)
        body = (self.state / 'policy.sqlite').read_bytes()
        source = self.root / 'synthetic-checkpoint'
        source.mkdir(mode=0o700)
        (source / 'policy.sqlite').write_bytes(body)
        (source / 'policy.sqlite').chmod(0o600)
        destination = self.root / 'restore-target'
        self.write_config(destination)
        metadata = {'binding': installed['binding'], 'policy_sha256': sha256(body).hexdigest(),
                    'policy_bytes': len(body), 'clock_floor': 0, 'created_at': now_micros(),
                    'sessions_excluded': True, 'login_schema': installed['login_schema']}
        for envelope, layout in ((3, 3), (2, 3), (1, 1), (2, 2)):
            with self.subTest(envelope=envelope, claimed_layout=layout):
                claim = dict(metadata, format=envelope, policy_schema=layout_profile(layout).policy_digest)
                if envelope != 1:
                    claim['installation_format'] = layout
                (source / 'backup.json').write_bytes(canonical(claim))
                (source / 'backup.json').chmod(0o600)
                prior = self.snapshot(source)
                with patch('packages.service_runtime.backup.allocate_directory') as allocate:
                    for op in (verify_backup, restore):
                        with self.assertRaisesRegex(RuntimeFailure, '^backup_invalid$'):
                            op(self.config, source)
                    allocate.assert_not_called()
                self.assertFalse(destination.exists())
                self.assertEqual(self.snapshot(source), prior)
