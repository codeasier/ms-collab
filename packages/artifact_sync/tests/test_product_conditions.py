# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Compound filters through the mounted WSGI codec and private SQLite record."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
import json
import sqlite3
import tempfile
import unittest

from packages.artifact_download import (ArtifactDownloadAPI, ArtifactDownloader, ContentBinding,
                                       DownloadPolicy, initialize_downloads)
from packages.artifact_download.tests.session_fixtures import SyntheticObjects
from packages.artifact_sync import (ArtifactSynchronizer, IndexedArtifactApplication, initialize_indexes,
                                    initialize_product_presets)
from packages.artifact_sync.tests.authority_fixtures import seed_publication
from packages.artifact_sync.tests.fixtures import BASE, SCOPE, Verifier, call, initialize, provider
from packages.artifact_sync.tests.test_product_view_route import CONFIGURED, descriptor
from packages.artifacts import Artifact, Scope, Snapshot
from packages.workspaces import SQLiteWorkspaceService

VIEW = '/product-views/kit'
PRESET = VIEW + '/quarters/2026Q4/presets/linux'
Q = {'format': 1, 'mode': 'files', 'quarter': '2026Q4', 'recursive': True, 'path': 'pkg/release/'}
STAMP = '2026-11-29T12:00:00Z'
CONDITIONS = [{'operator': '包含', 'keyword': 'kit'}, {'operator': 'AND', 'keyword': ' linux '},
              {'operator': 'AND NOT', 'keyword': 'jupyter'}]
SELECTED = 'pkg/release/KIT-Linux.zip'


class ProductConditions(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = tmp.name + '/policy.sqlite'
        self.store = initialize(self.db)
        self.policy = replace(CONFIGURED, readers=('admin', 'reader', 'developer'))
        beta = Scope('beta', SCOPE.connection_id, SCOPE.bucket_id)
        beta_policy = replace(self.policy, connection=replace(self.policy.connection, scope=beta),
                              readers=('admin',), refreshers=('admin',), approvers=('admin',))
        initialize_indexes(self.store, (self.policy, beta_policy))
        # Identical quarter/scope labels in another workspace, not an empty unconfigured route.
        rows = [('pkg/release/kit-linux-Oct31', '2026-10-31T00:00:00Z'),
                ('pkg/release/kit-linux-Dec01', '2026-12-01T23:59:59Z'),
                ('pkg/release/kit-linux-Q3', '2026-09-29T00:00:00Z')]
        rows += [(key, STAMP) for key in (SELECTED, 'pkg/release/kit-linux-Jupyter',
                 'pkg/release/other-linux', 'pkg/release/kit-mac', 'pkg/release/kit-linux.sha256',
                 'pkg/debug/kit-linux', 'pkg/release/kit-win-jupyter', 'pkg/release/kit-linux/')]
        self.snapshot = Snapshot(SCOPE, '2026-12-02T00:00:00Z', tuple(
            Artifact(k, stamp, 0 if k.endswith('/') else 7, k.endswith('/')) for k, stamp in rows))
        seed_publication(self.store, SCOPE, self.snapshot, actor='approver')
        seed_publication(self.store, beta, Snapshot(beta, STAMP, (Artifact('pkg/release/kit-linux-beta', STAMP, 7),)),
                         actor='admin')
        self.views = [descriptor(self.store, scope, 'kit', token='kit') for scope in (SCOPE, beta)]
        initialize_product_presets(self.store)
        self.now = datetime(2026, 11, 30, 12, tzinfo=timezone.utc)
        case = self
        class Current(Verifier):
            def verify(self, credential):
                return replace(super().verify(credential), issued_at=case.now, expires_at=case.now+timedelta(minutes=5))
        self.identity = provider(Current())
        self.app = self.restart()

    def restart(self):
        return IndexedArtifactApplication(store=SQLiteWorkspaceService(self.db), providers=(self.identity,),
            provider_key='test', clock=lambda: self.now, product_views=self.views, product_presets=True)

    def active(self, body, **kw):
        r = call(self.app, VIEW, method='POST', body=body, **kw)
        self.assertEqual(r['status'], 200, r)
        return r['body']['data']

    def keys(self, result):
        return [i['key'] for i in result['items']]

    def test_admin_save_second_member_reopen_after_restart_rollover_and_exact_scope(self):
        q = Q | {'conditions': CONDITIONS, 'relative_range': '30d', 'time_zone': 'UTC'}
        root = self.active({'format': 1, 'quarter': '2026Q4'})
        result = self.active(q | {'if_publication': root['observation']})
        self.assertEqual(self.keys(result), [SELECTED, 'pkg/release/kit-linux-Oct31'])
        pinned = q | {'if_observation': result['observation'], 'if_publication': root['observation']}
        for field in ('if_observation', 'if_publication'):
            missing = {k: v for k, v in pinned.items() if k != field}
            self.assertEqual(call(self.app, PRESET, method='POST', token='admin', body=missing)['status'], 400)
        for who in ('reader', 'developer', 'other'):
            self.assertIn(call(self.app, PRESET, method='POST', body=pinned, token=who)['status'], (403, 404))
        self.assertEqual(call(self.app, PRESET, method='POST', token='admin',
                         body=pinned | {'if_publication': '0'*64})['status'], 412)
        saved = call(self.app, PRESET, method='POST', body=pinned, token='admin')
        self.assertEqual(saved['status'], 200, saved)
        self.assertEqual(saved['body']['data']['query'], q)
        with sqlite3.connect(self.db) as db:
            raw, = db.execute('SELECT query FROM artifact_product_presets_v1').fetchone()
            self.assertEqual(json.loads(raw), q)
        self.now += timedelta(days=1)
        app = self.restart()
        opened = call(app, PRESET, token='reader')
        self.assertEqual(opened['status'], 200, opened)
        self.assertEqual(self.keys(opened['body']['data']['result']), [SELECTED, 'pkg/release/kit-linux-Dec01'])
        self.assertEqual(opened['body']['data']['query'], q)
        self.assertEqual(call(app, VIEW, method='POST', body=pinned)['status'], 412)
        for route, base, who in ((PRESET, BASE, 'other'), (PRESET, BASE.replace('alpha','beta'), 'reader'),
                                 (PRESET, BASE.replace('alpha','beta'), 'admin'),
                                 (PRESET.replace('2026Q4','2026Q3'), BASE, 'reader')):
            denied = call(app, route, token=who, base=base)
            self.assertIn(denied['status'], (403, 404), denied)
            self.assertNotIn('query', denied['body'])
        beta = self.active(q, base=BASE.replace('alpha','beta'), token='admin')
        self.assertEqual(self.keys(beta), ['pkg/release/kit-linux-beta'])

    def test_sql_or_precedence_does_not_escape_fixed_scope_time_or_pagination(self):
        # win OR (linux AND NOT jupyter): the win-jupyter row must survive.
        terms = [{'operator': '包含', 'keyword': 'win'}, *CONDITIONS[1:]]
        terms[1] = {'operator': 'OR', 'keyword': 'linux'}
        q = Q | {'conditions': terms, 'start_time': STAMP, 'end_time': STAMP, 'page_size': 1}
        first = self.active(q)
        second = self.active(q | {'page': 2, 'if_observation': first['observation']})
        self.assertEqual((first['total'], second['total'], self.keys(first), self.keys(second)),
                         (2, 2, [SELECTED], ['pkg/release/kit-win-jupyter']))
        all_or = self.active(Q | {'conditions': [{'operator':'包含','keyword':'not-real'},
                                                 {'operator':'OR','keyword':'%'}]})
        self.assertNotIn('pkg/release/other-linux', self.keys(all_or))
        self.assertNotIn('pkg/debug/kit-linux', self.keys(all_or))

    def test_invalid_conditions_never_widen_reads_or_persist_and_damaged_storage_fails_closed(self):
        first = CONDITIONS[0]
        bad = [None, [], 'kit', [first]*9, [{'operator':'OR','keyword':'kit'}],
               [first, {'operator':'NOT','keyword':'x'}], [first, {'operator':'包含','keyword':'x'}],
               [first | {'keyword': ' '}], [first | {'keyword': 'x'*101}], [first | {'keyword': '\n'}],
               [[first]], [{'conditions':[first]}], [first | {'key':'x'}], [first | {'keyword': 1}]]
        valid = Q | {'conditions': CONDITIONS}
        proof = self.active(valid)['observation']
        for payload in [*(Q | {'conditions': v} for v in bad), valid | {'keyword': 'linux'},
                        {'format': 1, 'quarter':'2026Q4', 'mode':'tree', 'conditions': CONDITIONS}]:
            for endpoint in (VIEW, PRESET):
                with self.subTest(payload=payload, endpoint=endpoint):
                    out = call(self.app, endpoint, method='POST', token='admin', body=payload | {'if_observation': proof})
                    self.assertEqual((out['status'], out['body']), (400, {'error': 'invalid_request'}))
        raw = json.dumps(valid).replace('"keyword": "kit"', '"keyword": "kit", "keyword": "%"').encode()
        self.assertEqual(call(self.app, VIEW, method='POST', extra={
            'wsgi.input': io.BytesIO(raw), 'CONTENT_LENGTH': str(len(raw))})['status'], 400)
        self.assertEqual(call(self.app, PRESET)['status'], 404)
        self.assertEqual(call(self.app, PRESET, token='admin', method='POST',
                              body=valid | {'if_observation': proof})['status'], 200)
        with sqlite3.connect(self.db) as db:
            db.execute('UPDATE artifact_product_presets_v1 SET query=?', (json.dumps(Q | {'conditions': []}),))
        self.assertEqual(call(self.app, PRESET)['status'], 503)

    def test_member_uses_returned_leaf_but_only_independent_byte_grant_can_download(self):
        objects = SyntheticObjects()
        objects.data = b'fixture'
        binding = ContentBinding(SELECTED, 7, STAMP, '"synth-v1"', sha256(objects.data).hexdigest())
        initialize_downloads(self.store, (DownloadPolicy(SCOPE, ('reader',), (binding,)),))
        service = ArtifactSynchronizer(store=self.store, providers=(self.identity,), provider_key='test',
                                       clock=lambda: self.now, credential_loader=objects.loader)
        api = ArtifactDownloadAPI(downloader=ArtifactDownloader(service=service, transport=objects),
                                  origin='https://app.example.invalid')
        result = self.active(Q | {'conditions': CONDITIONS, 'start_time': STAMP, 'end_time': STAMP}, token='reader')
        self.assertEqual(self.keys(result), [SELECTED])
        self.assertIs(result['source']['download_available'], False)
        def download(who):
            raw = json.dumps({'key': self.keys(result)[0]}).encode()
            env = {'PATH_INFO': '/api/workspaces/alpha/artifact-download/source/bucket/download',
                   'REQUEST_METHOD':'POST', 'wsgi.url_scheme':'https', 'HTTP_HOST':'app.example.invalid',
                   'CONTENT_TYPE':'application/json', 'CONTENT_LENGTH':str(len(raw)), 'wsgi.input':io.BytesIO(raw),
                   'HTTP_AUTHORIZATION':'Bearer synthetic-' + who, 'HTTP_IF_MATCH':f'"{result["catalog_revision"]}"'}
            status = []
            stream = api(env, lambda s, _: status.append(int(s.split()[0])))
            try:
                return status[0], b''.join(stream)
            finally:
                if hasattr(stream, 'close'):
                    stream.close()
        for who in ('admin', 'developer', 'other'):
            self.assertIn(download(who)[0], (403, 404))
        self.assertEqual(len(objects.calls), 0)
        status, data = download('reader')
        self.assertEqual((status, data, sha256(data).hexdigest()), (200, b'fixture', binding.sha256))
        self.assertEqual(len(objects.calls), 1)
