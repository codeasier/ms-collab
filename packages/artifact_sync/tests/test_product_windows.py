# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Civil-window boundaries and exact-scope WSGI behavior, using no external accounts."""
from dataclasses import replace
from datetime import datetime, timedelta
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from packages.artifacts import Artifact, Snapshot
from packages.artifact_sync import IndexedArtifactApplication, initialize_indexes, initialize_product_presets
from packages.artifact_sync.product_windows import resolve_window, separate_window
from packages.artifact_sync.tests.authority_fixtures import configure_index_fixture, seed_publication
from packages.artifact_sync.tests.fixtures import BASE, SCOPE, Verifier, call, initialize, provider
from packages.artifact_sync.tests.test_product_view_route import CONFIGURED, descriptor
from packages.workspaces import SQLiteWorkspaceService, StorageError

VIEW = '/product-views/files'
PRESET = VIEW + '/quarters/2026Q4/presets/linux-kit'
Q = {'format': 1, 'quarter': '2026Q4', 'mode': 'files', 'path': 'pkg/', 'keyword': 'linux',
     'recursive': True, 'relative_range': '30d', 'time_zone': 'Asia/Singapore'}


def parse(iso):
    return datetime.fromisoformat(iso.replace('Z', '+00:00'))


class CivilWindow(unittest.TestCase):
    def test_exact_obs_civil_vectors_including_both_dst_transitions(self):
        # Independently evaluated with the pinned OBS JS helper in these TZs.
        # week/30d subtract elapsed time before snapping; simple calendar-day
        # subtraction yields the WRONG start near midnight after DST changes.
        for zone, now, kind, start, end in [
            ('UTC', '2026-11-18T09:30Z', 'today', '2026-11-18T00:00Z', '2026-11-18T23:59:59Z'),
            ('UTC', '2026-11-18T09:30Z', 'week', '2026-11-11T00:00Z', '2026-11-18T23:59:59Z'),
            ('UTC', '2026-11-18T09:30Z', 'month', '2026-11-01T00:00Z', '2026-11-18T23:59:59Z'),
            ('UTC', '2026-11-18T09:30Z', '30d', '2026-10-19T00:00Z', '2026-11-18T23:59:59Z'),
            ('Asia/Singapore', '2028-02-29T16:00Z', 'month', '2028-02-29T16:00Z', '2028-03-01T15:59:59Z'),
            ('America/New_York', '2026-03-08T17:00Z', 'today', '2026-03-08T05:00Z', '2026-03-09T03:59:59Z'),
            ('America/New_York', '2026-03-09T04:30Z', 'week', '2026-03-01T05:00Z', '2026-03-10T03:59:59Z'),
            ('America/New_York', '2026-11-02T04:30Z', 'today', '2026-11-01T04:00Z', '2026-11-02T04:59:59Z'),
            ('America/New_York', '2026-11-02T04:30Z', '30d', '2026-10-03T04:00Z', '2026-11-02T04:59:59Z'),
        ]:
            with self.subTest(zone=zone, now=now, kind=kind):
                plain, window = separate_window(Q | {'time_zone': zone, 'relative_range': kind})
                self.assertNotIn('time_zone', plain)
                clock = Mock(return_value=parse(now))
                self.assertEqual(resolve_window(window, clock),
                                 {'start_time': parse(start).isoformat(), 'end_time': parse(end).isoformat()})
                clock.assert_called_once_with()

    def test_no_process_timezone_or_naive_clock_fallback(self):
        _, w = separate_window(Q)
        for v in (None, '2026-01-01', datetime(2026, 1, 1)):
            with self.subTest(v=v), self.assertRaises(StorageError):
                resolve_window(w, lambda: v)


class ProductWindowRoutes(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = tmp.name + '/index.sqlite'
        self.store = initialize(self.path)
        self.now = parse('2026-11-30T15:59:58Z')
        self.policy = replace(CONFIGURED, readers=('admin', 'reader', 'developer'))
        initialize_indexes(self.store, (self.policy,))
        rows = [('Kit_linux_old', '2026-10-30T16:00:00Z'), ('Kit_linux_new', '2026-12-01T15:59:59Z'),
                ('Kit_linux_late', '2026-12-01T15:59:59.000001Z'), ('Other_linux', '2026-11-10T12:00:00Z'),
                ('Kit_mac', '2026-11-10T12:00:00Z'), ('Kit_linux_q3', '2026-09-30T23:00:00Z')]
        seed_publication(self.store, SCOPE, Snapshot(SCOPE, '2026-12-02T00:00:00Z', tuple(
            Artifact('pkg/'+k, stamp, 7) for k, stamp in rows)), actor='approver')
        self.config = descriptor(self.store)
        initialize_product_presets(self.store)
        self.app = self.restart()

    def restart(self):
        case = self
        class Current(Verifier):
            def verify(self, credential):
                return replace(super().verify(credential), issued_at=case.now,
                               expires_at=case.now + timedelta(minutes=5))
        return IndexedArtifactApplication(store=SQLiteWorkspaceService(self.path), providers=(provider(Current()),),
                provider_key='test', clock=lambda: self.now, product_views=[self.config], product_presets=True)

    def query(self, body, *, app=None):
        r = call(app or self.app, VIEW, method='POST', body=body)
        self.assertEqual(r['status'], 200, r)
        return r['body']['data']

    @staticmethod
    def names(result):
        return [r['key'] for r in result['items']]

    def test_rollover_active_save_member_reopen_restart_and_immutable_fixed_dates(self):
        initial = self.query(Q)
        self.assertEqual(self.names(initial), ['pkg/Kit_linux_old'])
        query = Q | {'if_observation': initial['observation'],
                     'if_publication': initial['publication_observation']}
        for tok in ('reader', 'developer', 'other', 'approver'):
            self.assertIn(call(self.app, PRESET, method='POST', token=tok, body=query)['status'], (403, 404))
        saved = call(self.app, PRESET, method='POST', token='admin', body=query)
        self.assertEqual(saved['status'], 200, saved)
        self.assertEqual(saved['body']['data'], {'format': 1, 'kind': 'product-preset', 'preset_id': 'linux-kit',
                                                'query': Q, 'result': initial})
        with sqlite3.connect(self.path) as db:
            raw, = db.execute('SELECT query FROM artifact_product_presets_v1').fetchone()
            self.assertEqual(json.loads(raw), Q)  # no resolved dates or result rows
        fixed = {k: v for k, v in Q.items() if k not in ('relative_range', 'time_zone')}
        fixed.update(start_time='2026-10-30T16:00:00Z', end_time='2026-11-30T15:59:59Z')
        frozen = self.query(fixed)
        self.assertEqual(self.names(frozen), self.names(initial))
        self.assertNotIn('publication_observation', frozen)
        self.assertEqual(frozen['observation'], initial['publication_observation'])
        self.assertEqual(call(self.app, PRESET+'-fixed', method='POST', token='admin',
                              body=fixed | {'if_observation': frozen['observation']})['status'], 200)
        self.now = parse('2026-11-30T16:00:00Z')  # midnight Dec 1 in Singapore; no republish
        restarted = self.restart()
        active = self.query(Q, app=restarted)
        self.assertNotEqual(active['observation'], initial['observation'])
        self.assertEqual(active['publication_observation'], initial['publication_observation'])
        self.assertEqual(self.names(active), ['pkg/Kit_linux_new'])
        opened = call(restarted, PRESET, token='reader')
        self.assertEqual(opened['status'], 200, opened)
        self.assertEqual(opened['body']['data']['result'], active)
        self.assertEqual(opened['body']['data']['query'], Q)
        for endpoint, body, tok in [(VIEW, query | {'page': 2}, 'reader'), (PRESET+'-stale', query, 'admin')]:
            self.assertEqual(call(restarted, endpoint, body=body, method='POST', token=tok)['status'], 412)
        refreshed = call(restarted, PRESET+'-fixed')
        self.assertEqual(refreshed['body']['data']['query'], fixed)
        self.assertEqual(refreshed['body']['data']['result'], frozen)
        self.assertEqual(call(restarted, PRESET.replace('2026Q4', '2026Q3'))['status'], 404)
        self.assertIn(call(restarted, PRESET, base=BASE.replace('alpha', 'beta'))['status'], (403, 404))
        configure_index_fixture(self.store, replace(self.policy, readers=('admin',)), actor='approver')
        self.assertEqual(call(restarted, PRESET)['status'], 404)

    def test_window_never_widens_quarter_and_rejects_mixed_or_ambient_input(self):
        self.now = parse('2026-10-01T00:00Z')
        self.assertEqual(self.names(self.query(Q)), [])
        self.assertEqual(self.names(self.query(Q | {'quarter':'2026Q3'})), ['pkg/Kit_linux_q3'])
        without_zone = {k: v for k, v in Q.items() if k != 'time_zone'}
        without_range = {k: v for k, v in Q.items() if k != 'relative_range'}
        for bad in [without_zone, without_range, Q | {'mode': 'tree'}, Q | {'start_time': None},
                    Q | {'end_time': '2026-10-01T00:00:00Z'}, Q | {'now': '2026-10-01T00:00:00Z'},
                    Q | {'relative_range': 'latest'}, Q | {'relative_range': []},
                    *(Q | {'time_zone': v} for v in [None, ['UTC'], '/etc/localtime', 'localtime', 'EST5EDT',
                                                     'posix/UTC', 'America/../UTC', 'America/UnknownCity'])]:
            with self.subTest(bad=bad):
                self.assertEqual(call(self.app, VIEW, method='POST', body=bad)['status'], 400)

    def test_root_proof_rejects_changed_definition_or_bytes_even_with_same_labels_revision_and_time(self):
        tree = {'format': 1, 'quarter': Q['quarter']}
        root = self.query(tree)
        first = self.query(Q | {'if_publication': root['observation']})
        self.assertEqual(first['publication_observation'], root['observation'])
        self.assertNotEqual(first['observation'], root['observation'])
        self.assertEqual(call(self.app, VIEW, method='POST',
                              body=tree | {'if_publication': root['observation']})['status'], 400)
        for proof in (None, 42, 'A'*64, '0'*63):
            self.assertEqual(call(self.app, VIEW, method='POST', body=Q | {'if_publication': proof})['status'], 400)
        config = self.config
        self.config = {**config, 'product_token': 'other'}
        alternate = self.restart()
        def check(app):
            output = self.query(Q, app=app)  # independently current fresh query
            self.assertEqual((output['catalog_revision'], output['observed_at']),
                             (first['catalog_revision'], first['observed_at']))
            self.assertNotEqual(output['publication_observation'], root['observation'])
            old_root = {'if_publication': root['observation']}
            self.assertEqual(call(app, VIEW, method='POST', body=Q | old_root)['status'], 412)
            self.assertEqual(call(app, PRESET, method='POST', token='admin',
                                  body=Q | old_root | {'if_observation': output['observation']})['status'], 412)
            self.assertEqual(call(app, PRESET, token='reader')['status'], 404)
        check(alternate)
        self.config = config
        # Disposable fixture rebinds the same revision/observed_at to new bytes;
        # not an operation supported by the normal production approval path.
        seed_publication(self.store, SCOPE, Snapshot(SCOPE, root['observed_at'], (
            Artifact('pkg/Kit_linux_diff', '2026-11-10T12:00:00Z', 3),)), actor='approver')
        check(self.restart())
