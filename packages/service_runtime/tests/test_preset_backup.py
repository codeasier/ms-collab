# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""B3 cold transfer of real saved-query rows, using offline synthetic actors."""
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from packages.artifact_sync import IndexedArtifactApplication, initialize_indexes
from packages.artifact_sync.tests.authority_fixtures import seed_publication
from packages.artifact_sync.tests.fixtures import SCOPE, Verifier, call, initialize as fixture_policy, provider
from packages.artifact_sync.tests.test_product_view_route import CONFIGURED, descriptor, snapshot
from packages.login_state import LoginCoordinator, SQLiteLoginStore
from packages.service_runtime import (
    RuntimeFailure, backup, check, create_application, initialize, load_config, restore, verify_backup,
)
from packages.service_runtime.runtime import Lease, canonical, manifest
from packages.service_runtime.schema import layout_profile
from packages.workspace_admin_api.tests.transfer_fixtures import SyntheticExchange, issue_session
from packages.workspaces import SQLiteWorkspaceService


VIEW = '/product-views/files'
PRESET = VIEW + '/quarters/2026Q2/presets/'
NOW = datetime(2026, 4, 3, 12, tzinfo=timezone.utc)


class Current(Verifier):
    def verify(self, credential):
        return replace(super().verify(credential), issued_at=NOW, expires_at=NOW + timedelta(minutes=5))


class PresetColdBackupTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / 'service.json'
        self.source = self.root / 'source'
        self.archive = self.root / 'archive'
        self.target = self.root / 'restore'
        self.configure(self.source)
        initialize(self.config, layout=3)

    def configure(self, target):
        self.config.write_bytes(canonical({
            'version': 1, 'origin': 'https://app.example.invalid', 'data_directory': str(target),
            'modules': ['artifacts'], 'artifact_administration': True, 'login': {
                'mode': 'disabled', 'issuer': 'https://identity.example.invalid', 'audience': 'fixture-client',
            },
        }))
        self.config.chmod(0o600)

    @staticmethod
    def files(directory):
        return {p.name: p.read_bytes() for p in directory.iterdir()}

    @staticmethod
    def rows(database, sql):
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
            return db.execute(sql).fetchall()

    def provision_presets(self):
        # Prepare a fresh reviewed-layout test database before placing it in the
        # isolated installation. There is no runtime enrollment or provider.
        path = self.root / 'fixture-policy.sqlite'
        store = fixture_policy(path)
        with closing(sqlite3.connect(path)) as db:
            for statement in layout_profile(3).module_schema:
                db.execute(statement)
        initialize_indexes(store, (replace(CONFIGURED, readers=('admin', 'reader')),))
        seed_publication(store, SCOPE, snapshot(names=(
            'pkg/Kit_linux', 'pkg/Kit_linux_jupyter', 'pkg/Kit_mac', 'pkg/Other_linux',
        )), actor='approver')
        view = descriptor(store)
        (self.source / 'policy.sqlite').write_bytes(path.read_bytes())
        app = self.product_app(self.source, view)
        queries = {'regular': {'keyword': 'mac', 'start_time': '2026-04-01T00:00:00Z'},
                   'weekly': {'relative_range': 'week', 'time_zone': 'Asia/Shanghai', 'conditions': [
                       {'operator': '包含', 'keyword': 'linux'},
                       {'operator': 'AND NOT', 'keyword': 'jupyter'},
                   ]}}
        root = call(app, VIEW, method='POST', body={'format': 1, 'quarter': '2026Q2'})
        self.assertEqual(root['status'], 200, root)
        publication = root['body']['data']['observation']
        evidence = {}
        for name, predicates in queries.items():
            query = dict(format=1, mode='files', quarter='2026Q2', recursive=True, path='pkg/') | predicates
            active = call(app, VIEW, method='POST', body=query)
            self.assertEqual(active['status'], 200, active)
            pinned = query | {'if_observation': active['body']['data']['observation']}
            if name == 'weekly':
                pinned['if_publication'] = publication
            saved = call(app, PRESET + name, method='POST', body=pinned, token='admin')
            self.assertEqual(saved['status'], 200, saved)
            self.assertEqual(saved['body']['data']['query'], query)
            evidence[name] = call(app, PRESET + name, token='reader')
            self.assertEqual(evidence[name]['status'], 200)
            keys = [item['key'] for item in evidence[name]['body']['data']['result']['items']]
            self.assertEqual(keys, ['pkg/Kit_mac' if name == 'regular' else 'pkg/Kit_linux'])
        return view, evidence

    @staticmethod
    def product_app(directory, view):
        return IndexedArtifactApplication(store=SQLiteWorkspaceService(directory / 'policy.sqlite'),
            providers=(provider(Current()),), provider_key='test', clock=lambda: NOW,
            product_views=[view], product_presets=True)

    def test_nonempty_presets_cold_backup_and_fresh_restore_are_byte_and_semantically_exact(self):
        view, evidence = self.provision_presets()
        login = SQLiteLoginStore(self.source / 'login.sqlite', binding=load_config(self.config).binding)
        clock = lambda: datetime.now(timezone.utc)
        issue_session(login, 'admin', clock)
        LoginCoordinator(login, exchange=SyntheticExchange(login.binding, clock)).begin()
        for key in ('sessions', 'attempts'):
            self.assertEqual(self.rows(self.source / 'login.sqlite', 'SELECT count(*) FROM ' + key), [(1,)])
        before = self.files(self.source)
        backup_result = {'backup_valid': True, 'sessions_excluded': True, 'live_restore_tested': False}
        self.assertEqual(backup(self.config, self.archive), backup_result)
        with self.assertRaisesRegex(RuntimeFailure, '^destination_exists$'):
            backup(self.config, self.archive)
        frozen = self.files(self.archive)
        self.assertEqual(set(frozen), {'policy.sqlite', 'backup.json'})
        self.assertEqual(frozen['policy.sqlite'], before['policy.sqlite'])
        info = json.loads(frozen['backup.json'])
        self.assertEqual(set(info), {'format', 'installation_format', 'binding', 'policy_schema',
            'login_schema', 'policy_sha256', 'policy_bytes', 'clock_floor', 'created_at', 'sessions_excluded'})
        self.assertEqual((info['format'], info['installation_format']), (3, 3))
        self.assertEqual(info['policy_sha256'], sha256(before['policy.sqlite']).hexdigest())
        self.assertEqual(info['policy_bytes'], len(before['policy.sqlite']))
        self.assertIs(info['sessions_excluded'], True)
        self.assertEqual(self.archive.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all((p.stat().st_mode & 0o777) == 0o600 for p in self.archive.iterdir()))
        self.configure(self.target)
        self.assertEqual(verify_backup(self.config, self.archive), backup_result)
        self.assertFalse(self.target.exists())
        self.assertEqual(restore(self.config, self.archive), {
            'restore_valid': True, 'sessions_restored': False, 'login_enabled': False, 'production_ready': False,
        })
        restored = self.files(self.target)
        self.assertEqual(restored['policy.sqlite'], before['policy.sqlite'])
        self.assertEqual(restored['installation.json'], canonical(manifest(load_config(self.config), layout=3)))
        query = 'SELECT * FROM artifact_product_presets_v1 ORDER BY preset'
        stored = self.rows(self.source / 'policy.sqlite', query)
        self.assertEqual(len(stored), 2)
        self.assertEqual(self.rows(self.target / 'policy.sqlite', query), stored)
        for key in ('sessions', 'attempts'):
            self.assertEqual(self.rows(self.target / 'login.sqlite', 'SELECT count(*) FROM ' + key), [(0,)])
        floor, = self.rows(self.target / 'login.sqlite', 'SELECT clock FROM metadata')[0]
        self.assertGreaterEqual(floor, info['clock_floor'])
        result = check(self.config)
        self.assertFalse(result['login_enabled'])
        self.assertFalse(result['production_ready'])
        with self.assertRaisesRegex(RuntimeFailure, '^runtime_unavailable$'):
            create_application(self.config)
        with self.assertRaisesRegex(RuntimeFailure, '^destination_exists$'):
            restore(self.config, self.archive)
        self.assertEqual(self.files(self.target), restored)
        # This independently composed offline fixture is not the installation
        # factory. It proves the restored meaning without allowing I3 to serve.
        app = self.product_app(self.target, view)
        for name, receipt in evidence.items():
            self.assertEqual(call(app, PRESET + name, token='reader'), receipt)
            self.assertEqual(call(app, PRESET + name, token='other')['status'], 404)
        self.assertEqual(self.files(self.source), before)
        self.assertEqual(self.files(self.archive), frozen)

    def test_tampered_b3_is_rejected_without_allocating_or_removing_a_target(self):
        backup(self.config, self.archive)
        frozen = self.files(self.archive)
        info = json.loads(frozen['backup.json'])
        self.configure(self.target)
        bad = [dict(info, **{field: value}) for field, value in (
            ('format', '3'), ('format', 3.0), ('format', 4),
            ('installation_format', 2), ('installation_format', 3.0),
            ('policy_schema', layout_profile(2).policy_digest),
            ('login_schema', '0' * 64), ('binding', '0' * 64),
            ('policy_bytes', True), ('sessions_excluded', False),
            ('policy_sha256', '0' * 64), ('unexpected', ''),
        )]
        bad.append({k: v for k, v in info.items() if k != 'installation_format'})
        damaged = frozen['policy.sqlite'][:-1] + bytes([frozen['policy.sqlite'][-1] ^ 1])
        inputs = [(canonical(v), frozen['policy.sqlite']) for v in bad]
        inputs += [(frozen['backup.json'], damaged)]
        # Even a recalculated correct digest cannot make a different SQLite
        # schema admissible under the pinned installation schema.
        altered = self.root / 'altered.sqlite'
        altered.write_bytes(frozen['policy.sqlite'])
        with closing(sqlite3.connect(altered)) as db:
            db.execute('CREATE TABLE test_only_corruption (value TEXT)')
        d = altered.read_bytes()
        inputs += [(canonical(dict(info, policy_sha256=sha256(d).hexdigest(), policy_bytes=len(d))), d)]
        with patch('packages.service_runtime.backup.allocate_directory') as allocate:
            for i, (header, payload) in enumerate(inputs):
                with self.subTest(case=i):
                    (self.archive / 'backup.json').write_bytes(header)
                    (self.archive / 'policy.sqlite').write_bytes(payload)
                    copied = self.files(self.archive)
                    for convert in (verify_backup, restore):
                        with self.assertRaisesRegex(RuntimeFailure, '^backup_invalid$'):
                            convert(self.config, self.archive)
                    self.assertEqual(self.files(self.archive), copied)
                    self.assertFalse(self.target.exists())
            allocate.assert_not_called()

    def test_restore_failure_releases_lease_without_activating_or_reusing_partial_target(self):
        backup(self.config, self.archive)
        before = self.files(self.archive)
        self.configure(self.target)
        with patch('packages.service_runtime.backup.SQLiteLoginStore.initialize', side_effect=OSError):
            with self.assertRaisesRegex(RuntimeFailure, '^restore_incomplete$'):
                restore(self.config, self.archive)
        self.assertTrue(self.target.is_dir())
        partial = self.files(self.target)
        self.assertEqual(set(partial), {'.runtime.lock', 'policy.sqlite'})
        with Lease(self.target, exclusive=True) as lease:
            self.assertTrue(lease.valid())
        with self.assertRaises(RuntimeFailure):
            check(self.config)
        with self.assertRaisesRegex(RuntimeFailure, '^destination_exists$'):
            restore(self.config, self.archive)
        self.assertEqual(self.files(self.target), partial)
        self.assertEqual(self.files(self.archive), before)
