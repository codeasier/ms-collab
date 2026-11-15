"""Synthetic-only configured verification, persisted policy and real HTTP."""
from datetime import datetime, timedelta, timezone
import http.client
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from wsgiref.simple_server import make_server, WSGIRequestHandler

from packages.artifacts import Artifact, ArtifactCatalog, Definition, SavedView, Scope, Snapshot
from packages.artifacts_api import ArtifactApplication, catalog_resource, view_resource
from packages.artifacts_api.records import record_id
from packages.identity import AuthenticationError, Credential, Provider, VerifiedIdentity
from packages.workspaces import Membership, ModuleInstance, Principal, Resource, SQLiteWorkspaceService, Workspace

NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
ISSUER = 'https://identity.example.invalid'
BASE = '/api/workspaces/alpha/artifacts/source/bucket'


class TestVerifier:
    """Only a test credential map, never exported as an application provider."""
    def __init__(self):
        self.hook = lambda: None

    def verify(self, credential):
        self.hook()
        if type(credential) is not Credential or credential.value not in {'test-reader', 'test-admin', 'test-outsider', 'test-disabled'}:
            raise AuthenticationError()
        return VerifiedIdentity(ISSUER, credential.value[5:], 'test-artifacts', NOW, NOW + timedelta(seconds=60))


def snapshot(workspace, *, connection='source', bucket='bucket'):
    return Snapshot(Scope(workspace, connection, bucket), '2026-01-02T00:00:00Z', (
        Artifact(f'{workspace}/one.txt', '2026-01-01T00:00:00Z', 12),
        Artifact(f'{workspace}/two.txt', '2026-01-01T00:01:00Z', 23),
        Artifact(f'{workspace}/older.txt', '2025-12-31T23:59:59Z', 4)))


def initialize(path):
    rows = []
    for w in ('alpha', 'beta', 'commons'):
        s = snapshot(w)
        rows.extend((catalog_resource(s), view_resource(SavedView(s.scope, 'files', 'Files', Definition(files_only=True)))))
    # Same bucket ID on a second connection must not collide.
    rows.append(catalog_resource(snapshot('alpha', connection='second')))
    return SQLiteWorkspaceService.initialize(path,
        principals=tuple(Principal(name, ISSUER, name, name != 'disabled') for name in ('admin', 'reader', 'outsider', 'disabled')),
        workspaces=tuple(Workspace(w, w.title(), 'shared' if w == 'commons' else 'product') for w in ('alpha', 'beta', 'commons')),
        memberships=tuple(Membership(w, 'admin', 'admin') for w in ('alpha', 'beta', 'commons')) +
            (Membership('alpha', 'reader', 'viewer'), Membership('commons', 'reader', 'viewer'), Membership('alpha', 'disabled', 'viewer')),
        modules=tuple(ModuleInstance(w, 'artifacts', ('read', 'write')) for w in ('alpha', 'beta', 'commons')),
        resources=tuple(rows), operators=frozenset({'admin'}))


def call(app, suffix='/quarters', *, method='GET', token='test-reader', body=None, raw=None, query='', base=BASE, extra=None):
    payload = raw if raw is not None else (json.dumps(body).encode() if body is not None else b'')
    environ = {'REQUEST_METHOD': method, 'PATH_INFO': base + suffix, 'QUERY_STRING': query,
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(payload)), 'wsgi.input': io.BytesIO(payload)}
    if token is not None:
        environ['HTTP_AUTHORIZATION'] = 'Bearer ' + token
    environ.update(extra or {})
    output = {}
    def start(status, headers):
        output.update(status=int(status.split()[0]), headers=dict(headers))
    data = b''.join(app(environ, start))
    output['body'] = json.loads(data)
    assert int(output['headers']['Content-Length']) == len(data)
    return output


class ArtifactHTTPTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.path = Path(d.name) / 'store.sqlite3'
        self.store = initialize(self.path)
        self.verifier = TestVerifier(); self.now = NOW
        self.app = ArtifactApplication(store=self.store, providers=(Provider('test', ISSUER, 'test-artifacts', self.verifier),),
                                       provider_key='test', clock=lambda: self.now)

    def query(self, **kwargs):
        kwargs.setdefault('body', {'quarter': '2026Q1', 'definition': {'files_only': True}})
        return call(self.app, '/query', method='POST', **kwargs)

    def mutate_record(self, kind, transform, view=None):
        scope = Scope('alpha', 'source', 'bucket')
        ident = record_id(scope, kind, view)
        with sqlite3.connect(self.path) as c:
            text = c.execute('SELECT text FROM resources WHERE workspace_id=? AND id=?', ('alpha', ident)).fetchone()[0]
            c.execute('UPDATE resources SET text=? WHERE workspace_id=? AND id=?', (transform(text), 'alpha', ident))

    def test_quarters_scope_revision_and_no_store(self):
        r = call(self.app)
        self.assertEqual(r['status'], 200)
        self.assertEqual(r['body']['data'], {'scope': Scope('alpha', 'source', 'bucket').as_dict(), 'catalog_revision': 1,
                                           'quarters': ['2026Q1', '2025Q4']})
        self.assertEqual(r['headers']['Cache-Control'], 'no-store')
        self.assertNotIn('Set-Cookie', r['headers'])

    def test_query_filter_sort_page_and_virtual_parents(self):
        r = self.query(body={'quarter': '2026Q1', 'definition': {'files_only': True, 'sort_by': 'size', 'order': 'desc'}, 'page_size': 1})
        data = r['body']['data']
        self.assertEqual((data['total'], data['items'][0]['key']), (2, 'alpha/two.txt'))
        r = self.query(body={'quarter': '2026Q1', 'definition': {'recursive': False}})
        self.assertEqual(r['body']['data']['items'][0]['key'], 'alpha/')
        self.assertTrue(r['body']['data']['items'][0]['virtual'])

    def test_named_view_narrows_and_cannot_grant(self):
        r = call(self.app, '/views/files', query='quarter=2026Q1&page_size=1')
        self.assertEqual(r['status'], 200); self.assertEqual(r['body']['data']['view_revision'], 1)
        self.assertEqual(len(r['body']['data']['items']), 1)
        self.assertEqual(call(self.app, '/views/files', query='quarter=2026Q1', base=BASE.replace('alpha', 'beta'))['status'], 404)

    def test_unknown_view_no_broad_fallback(self):
        self.assertEqual(call(self.app, '/views/missing', query='quarter=2026Q1')['status'], 404)

    def test_missing_quarter_is_not_latest_fallback(self):
        r = self.query(body={'quarter': '2024Q2', 'definition': {}})
        self.assertFalse(r['body']['data']['quarter_available']); self.assertEqual(r['body']['data']['items'], [])

    def test_same_bucket_other_connection_not_a_fallback(self):
        self.assertEqual(call(self.app, base=BASE.replace('/source/', '/missing/'))['status'], 404)
        self.assertEqual(call(self.app, base=BASE.replace('/source/', '/second/'))['status'], 200)
        self.assertEqual(call(self.app, base=BASE.replace('/bucket', '/missing'))['status'], 404)

    def test_cross_space_and_missing_space_indistinguishable(self):
        a = self.query(base=BASE.replace('alpha', 'beta'))
        b = self.query(base=BASE.replace('alpha', 'unknown'))
        self.assertEqual((a['status'], a['body']), (b['status'], b['body']))
        self.assertEqual(a['status'], 404)

    def test_shared_space_is_explicit_not_union(self):
        r = self.query(base=BASE.replace('alpha', 'commons'))
        self.assertTrue(all(x['key'].startswith('commons/') for x in r['body']['data']['items']))
        self.assertEqual(self.query(base=BASE.replace('alpha', 'commons'), token='test-outsider')['status'], 404)

    def test_forged_identity_headers_do_not_authenticate(self):
        r = call(self.app, token=None, extra={'REMOTE_USER': 'admin', 'HTTP_X_USER': 'admin', 'HTTP_COOKIE': 'actor=admin'})
        self.assertEqual(r['status'], 401)
        self.assertEqual(self.query(body={'quarter': '2026Q1', 'definition': {}, 'actor': 'admin'})['status'], 400)

    def test_unknown_and_disabled_identity(self):
        for token in ('unknown', 'test-disabled'):
            self.assertEqual(call(self.app, token=token)['status'], 401)

    def test_revoke_during_verification_is_current(self):
        self.verifier.hook = lambda: self.store.set_membership('admin', 'alpha', 'reader', role='viewer', state='revoked', expected_revision=1)
        self.assertEqual(self.query()['status'], 404)

    def test_module_disable_and_missing_read_capability(self):
        self.store.set_module_state('admin', 'alpha', 'artifacts', state='disabling', expected_revision=1)
        self.assertEqual(self.query()['status'], 409)
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE modules SET state='enabled',can_read=0 WHERE workspace_id='alpha'")
        self.assertEqual(self.query()['status'], 403)

    def test_archived_reads_and_revocation(self):
        self.store.set_workspace_state('admin', 'alpha', state='archived', expected_revision=1)
        self.assertEqual(self.query()['status'], 200)
        self.store.set_membership('admin', 'alpha', 'reader', role='viewer', state='revoked', expected_revision=1)
        self.assertEqual(self.query()['status'], 404)

    def test_suspended_space_denies_metadata(self):
        self.store.set_workspace_state('admin', 'alpha', state='suspended', expected_revision=1)
        self.assertEqual(self.query()['status'], 409)

    def test_schema_scope_mismatch_fails_closed(self):
        self.mutate_record('catalog', lambda s: s.replace('"workspace_id":"alpha"', '"workspace_id":"beta"'))
        r = self.query(); self.assertEqual(r['status'], 503)
        self.assertEqual(r['body'], {'error': 'storage_unavailable'})

    def test_view_scope_mismatch_fails_closed(self):
        self.mutate_record('view', lambda s: s.replace('"bucket_id":"bucket"', '"bucket_id":"other"'), 'files')
        self.assertEqual(call(self.app, '/views/files', query='quarter=2026Q1')['status'], 503)

    def test_malformed_records_fail_closed_and_hide_contents(self):
        for text in ('not JSON synthetic-secret', '{"schema":1,"schema":1}', '[]'):
            with self.subTest(text=text):
                self.mutate_record('catalog', lambda _: text)
                r = self.query(); self.assertEqual(r['status'], 503)
                self.assertEqual(r['body'], {'error': 'storage_unavailable'})

    def test_unexpected_stored_url_or_extra_field_rejected(self):
        self.mutate_record('catalog', lambda s: s.replace('"size":12', '"size":12,"url":"https://files.example.invalid/a"'))
        self.assertEqual(self.query()['status'], 503)

    def test_duplicate_fields_and_nonfinite_rejected(self):
        for raw in (b'{"quarter":"2026Q1","quarter":"2026Q1","definition":{}}',
                    b'{"quarter":"2026Q1","definition":{},"page":NaN}', b'\xff'):
            self.assertEqual(self.query(raw=raw)['status'], 400)

    def test_query_validation(self):
        for body in ({'quarter':'bad', 'definition':{}}, {'quarter':'2026Q1','definition':{'path':'../'}},
                     {'quarter':'2026Q1','definition':{'conditions':[{'keyword':'x','operator':'OR'}]}},
                     {'quarter':'2026Q1','definition':{},'page':True}, {'quarter':'2026Q1','definition':{},'page_size':101},
                     {'quarter':'2026Q1','definition':{},'workspace_id':'beta'}):
            self.assertEqual(self.query(body=body)['status'], 400)

    def test_view_query_validation(self):
        for query in ('', 'quarter=2026Q1&quarter=2026Q2', 'quarter=2026Q1&actor=admin', 'quarter=2026Q1&page=-1'):
            self.assertEqual(call(self.app, '/views/files', query=query)['status'], 400)

    def test_body_limits_and_content_type(self):
        self.assertEqual(self.query(raw=b'x'*16385)['status'], 413)
        self.assertEqual(self.query(extra={'CONTENT_TYPE':'text/plain'})['status'], 415)
        self.assertEqual(self.query(extra={'CONTENT_LENGTH':''})['status'], 411)
        self.assertEqual(self.query(extra={'CONTENT_LENGTH':'999'})['status'], 400)

    def test_unsupported_mutations_do_not_write(self):
        for verb in ('PUT', 'DELETE', 'PATCH'):
            self.assertEqual(call(self.app, '/query', method=verb)['status'], 404)
        self.assertEqual(self.store.audit_events('admin', 'alpha'), ())

    def test_expiry_during_materialization_returns_no_metadata(self):
        original = ArtifactCatalog.query
        def delayed(*args, **kwargs):
            result = original(*args, **kwargs); self.now = NOW + timedelta(minutes=2); return result
        with patch.object(ArtifactCatalog, 'query', delayed):
            self.assertEqual(self.query()['status'], 401)

    def test_authorize_before_decoding_metadata(self):
        with patch('packages.artifacts_api.application.load_catalog', side_effect=AssertionError('must not decode')):
            self.assertEqual(self.query(base=BASE.replace('alpha', 'beta'))['status'], 404)

    def test_literal_filters_no_sql_or_wildcards(self):
        for keyword in ('%', '_', "' OR 1=1 --"):
            r = self.query(body={'quarter':'2026Q1', 'definition':{'conditions':[{'keyword':keyword}]}})
            self.assertEqual(r['body']['data']['items'], [])

    def test_record_ids_are_unambiguous_and_bootstrap_is_bounded(self):
        self.assertNotEqual(record_id(Scope('alpha','a.b','c'), 'catalog'), record_id(Scope('alpha','a','b.c'),'catalog'))
        with self.assertRaises(ValueError):
            catalog_resource(Snapshot(Scope('alpha','source','bucket'), '2026-01-02T00:00:00Z',
                tuple(Artifact('x'*100 + str(i), '2026-01-01T00:00:00Z') for i in range(100))))

    def test_current_persisted_metadata_not_process_cache(self):
        self.mutate_record('catalog', lambda s: s.replace('one.txt', 'updated.txt'))
        self.assertIn('alpha/updated.txt', [r['key'] for r in self.query()['body']['data']['items']])

    def test_catalog_and_view_share_one_snapshot(self):
        # WAL allows an independent writer to commit after the read snapshot.
        with sqlite3.connect(self.path) as c:
            c.execute('PRAGMA journal_mode=WAL')
        from packages.artifacts_api.records import load_catalog
        def after_load(*args):
            result = load_catalog(*args)
            self.mutate_record('view', lambda s: s.replace('"files_only":true', '"files_only":false'), 'files')
            return result
        with patch('packages.artifacts_api.application.load_catalog', after_load):
            response = call(self.app, '/views/files', query='quarter=2026Q1')
        self.assertEqual(response['status'], 200)
        self.assertTrue(all(not r['is_dir'] for r in response['body']['data']['items']))
        following = call(self.app, '/views/files', query='quarter=2026Q1')
        self.assertTrue(any(r['is_dir'] for r in following['body']['data']['items']))

    def test_actual_loopback_http(self):
        class Quiet(WSGIRequestHandler):
            def log_message(self, *args): pass
        server = make_server('127.0.0.1', 0, self.app, handler_class=Quiet)
        thread = Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            client.request('POST', BASE+'/query', body=json.dumps({'quarter':'2026Q1','definition':{}}),
                           headers={'Authorization':'Bearer test-reader','Content-Type':'application/json'})
            r = client.getresponse(); self.assertEqual(r.status, 200)
            self.assertEqual(json.loads(r.read())['data']['scope']['workspace_id'], 'alpha')
            client.close()
        finally:
            server.shutdown(); server.server_close(); thread.join(3)


if __name__ == '__main__':
    unittest.main()
