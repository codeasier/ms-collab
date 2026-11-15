"""Real authorization/database and stream lifecycle; only synthetic cloud bytes."""
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
import http.client
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from threading import Barrier, Thread
import unittest
from unittest.mock import patch
from wsgiref.simple_server import make_server, WSGIRequestHandler

from packages.artifact_download import ArtifactDownloadAPI, ArtifactDownloader, ContentBinding, DownloadPolicy, DownloadInterrupted, initialize_downloads
from packages.artifact_download import records
from packages.artifact_sync import ArtifactSynchronizer, initialize_indexes
from packages.artifact_sync.records import policy_text as index_text
from packages.workspaces import StorageError
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'artifact_sync' / 'tests'))
from fixtures import CONFIG, NOW, POLICY, SCOPE, Transport, Verifier, credential, initialize, page, provider

DATA = b'synthetic-content-not-production' * 5000
KEY = 'builds/synthetic.bin'
BINDING = ContentBinding(KEY, len(DATA), '2026-01-01T23:00:00Z', '"synthetic-etag"', sha256(DATA).hexdigest())
GRANT = DownloadPolicy(SCOPE, ('reader',), (BINDING,))
BASE = '/api/workspaces/alpha/artifact-download/source/bucket/download'


class Response:
    def __init__(self, data):
        self.data = io.BytesIO(data)
        self.closed = False
        self.reads = 0
        self.hook = lambda: None
    def read(self, size):
        self.reads += 1
        result = self.data.read(size)
        self.hook()
        return result
    def close(self):
        self.closed = True


class Objects:
    def __init__(self):
        self.calls = []
        self.data = DATA
        self.hook = lambda: None
        self.read_hook = lambda: None
    def open(self, config, secret, binding, now, *, remaining):
        self.calls.append((config, binding))
        self.hook()
        self.response = Response(self.data)
        self.response.hook = self.read_hook
        return self.response


class DownloadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'policy.sqlite3'
        self.store = initialize(self.path)
        initialize_indexes(self.store, (POLICY,))
        initialize_downloads(self.store, (GRANT,))
        self.now, self.mono = NOW, 10.0
        self.loads = []
        self.verifier = Verifier()
        self.listing = Transport([page(((KEY, BINDING.modified_at, len(DATA)),))])
        def loader(ref, scope, origin):
            self.loads.append((ref, scope, origin)); return credential(scope, origin)
        self.args = dict(store=self.store, providers=(provider(self.verifier),), provider_key='test', clock=lambda: self.now)
        self.service = ArtifactSynchronizer(**self.args, credential_loader=loader, transport=self.listing)
        from fixtures import user
        candidate = self.service.refresh(user('admin'), SCOPE, expected_revision=1)
        self.service.approve(user('approver'), SCOPE, expected_revision=2, candidate_digest=candidate['candidate_digest'])
        self.loads.clear()
        self.objects = Objects()
        self.downloader = ArtifactDownloader(service=self.service, transport=self.objects, monotonic=lambda: self.mono)
        self.api = ArtifactDownloadAPI(downloader=self.downloader, origin='https://app.example.invalid')

    def call(self, *, who='reader', body=None, raw=None, revision=3, extra=None, consume=True, api=None):
        raw = raw if raw is not None else json.dumps({'key': KEY} if body is None else body).encode()
        env = {'PATH_INFO': BASE, 'REQUEST_METHOD': 'POST', 'QUERY_STRING': '', 'HTTP_HOST': 'app.example.invalid',
               'wsgi.url_scheme': 'https', 'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw)}
        if who is not None: env['HTTP_AUTHORIZATION'] = 'Bearer synthetic-' + who
        if revision is not None: env['HTTP_IF_MATCH'] = f'"{revision}"'
        env.update(extra or {})
        result = {}
        def start(status, headers): result.update(status=int(status.split()[0]), headers=dict(headers))
        stream = (api or self.api)(env, start)
        result['stream'] = stream
        if consume:
            result['raw'] = b''.join(stream)
            if hasattr(stream, 'close'): stream.close()
        return result

    def leases(self):
        with sqlite3.connect(self.path) as c:
            return c.execute('SELECT actor,state,offered,size FROM artifact_download_leases_v1 ORDER BY rowid').fetchall()

    def revoke(self):
        self.store.set_membership('admin', 'alpha', 'reader', role='viewer', state='revoked', expected_revision=1)

    def test_full_verified_bytes_no_url_no_inline_media_and_durable_audit(self):
        result = self.call()
        self.assertEqual(result['status'], 200); self.assertEqual(result['raw'], DATA)
        self.assertEqual(result['headers']['Content-Type'], 'application/octet-stream')
        self.assertEqual(result['headers']['X-Artifact-SHA256'], BINDING.sha256)
        self.assertEqual(result['headers']['Content-Disposition'], 'attachment; filename="artifact-synthetic.bin"')
        self.assertEqual(result['headers']['Cache-Control'], 'no-store')
        self.assertNotIn('Location', result['headers']); self.assertNotIn('Set-Cookie', result['headers'])
        self.assertEqual(self.leases(), [('reader', 'complete', len(DATA), len(DATA))])
        self.assertTrue(self.objects.response.closed)
        with sqlite3.connect(self.path) as c:
            raw = repr(c.execute('SELECT * FROM artifact_download_leases_v1').fetchall())
        self.assertNotIn(KEY, raw); self.assertNotIn('synthetic-secret', raw)

    def test_grants_not_inferred_from_reader_admin_or_verified_identity(self):
        for who in ('admin', 'approver', 'developer', 'other'):
            self.assertEqual(self.call(who=who)['status'], 404)
        self.assertEqual(self.call(who=None, extra={'REMOTE_USER': 'reader', 'HTTP_COOKIE': 'identity=reader'})['status'], 401)
        self.assertEqual(self.loads, []); self.assertEqual(self.objects.calls, []); self.assertEqual(self.leases(), [])

    def test_unapproved_unbound_or_changed_metadata_never_downloaded(self):
        for update in ("UPDATE artifact_indexes_v1 SET published=NULL,published_revision=NULL",
                       "UPDATE artifact_download_policies_v1 SET policy='{}'"):
            with sqlite3.connect(self.path) as c:
                c.execute('SAVEPOINT hold')
                # Run actual request after committing mutation; restore below.
                rows = c.execute('SELECT * FROM artifact_indexes_v1').fetchall()
                grants = c.execute('SELECT policy FROM artifact_download_policies_v1').fetchone()[0]
                c.execute(update)
            self.assertIn(self.call()['status'], (404, 503)); self.assertEqual(self.loads, [])
            with sqlite3.connect(self.path) as c:
                c.execute('UPDATE artifact_indexes_v1 SET published=?,published_revision=?', (rows[0][6], rows[0][7]))
                c.execute('UPDATE artifact_download_policies_v1 SET policy=?', (grants,))
        for binding in (replace(BINDING, key='builds/missing'), replace(BINDING, size=len(DATA)-1), replace(BINDING, modified_at='2026-01-01T22:00:00Z')):
            with sqlite3.connect(self.path) as c: c.execute('UPDATE artifact_download_policies_v1 SET policy=?', (records.policy_text(replace(GRANT, bindings=(binding,))),))
            self.assertEqual(self.call()['status'], 404)
        self.assertEqual(self.objects.calls, [])

    def test_cross_scope_no_shared_or_other_bucket_fallback(self):
        for path in (BASE.replace('alpha', 'beta'), BASE.replace('alpha', 'commons'), BASE.replace('/source/', '/different/'), BASE.replace('/bucket/', '/different/')):
            self.assertEqual(self.call(extra={'PATH_INFO': path})['status'], 404)
        self.assertEqual(self.loads, [])

    def test_revision_routes_same_origin_and_body_are_strict(self):
        for extra in ({'REQUEST_METHOD':'GET'}, {'QUERY_STRING':'token=bad'}, {'PATH_INFO':BASE+'/extra'}):
            self.assertEqual(self.call(extra=extra)['status'],404)
        for extra in ({'HTTP_HOST':'evil.invalid'}, {'HTTP_ORIGIN':'https://evil.invalid'}, {'wsgi.url_scheme':'http'}, {'HTTP_SEC_FETCH_SITE':'cross-site'}):
            self.assertEqual(self.call(extra=extra)['status'],403)
        self.assertEqual(self.call(revision=None)['status'],428)
        self.assertEqual(self.call(revision=2)['status'],412)
        for extra in ({'HTTP_RANGE':'bytes=0-5'}, {'HTTP_IF_RANGE':'etag'}, {'HTTP_TRANSFER_ENCODING':'chunked'}):
            self.assertEqual(self.call(extra=extra)['status'],400)
        for body in ({'key':KEY, 'url':'https://private.invalid'}, {'actor':'reader'}, {'key':123}, {'key':'https://private.invalid'}, {'key':'../file'}, {'key':'\ud800'}):
            self.assertEqual(self.call(body=body)['status'],400)
        for raw in (b'{"key":"a","key":"b"}', b'NaN', b'[]', b'\xff'):
            self.assertEqual(self.call(raw=raw)['status'],400)
        self.assertEqual(self.call(extra={'CONTENT_LENGTH':'17000'})['status'],413)
        self.assertEqual(self.loads, [])

    def test_hash_mismatch_short_excess_and_exception_emit_no_content(self):
        for data in (DATA[:-1], DATA+b'x', b'x'*len(DATA)):
            self.objects.data = data
            result = self.call(); self.assertEqual(result['status'],502)
            self.assertEqual(result['raw'], b'{"error":"provider_unavailable"}')
            self.assertTrue(self.objects.response.closed)
        self.assertTrue(all(v[1:3] == ('failed',0) for v in self.leases()))

    def test_revoke_after_upstream_read_emits_zero_bytes(self):
        self.objects.read_hook = self.revoke
        result = self.call(); self.assertEqual(result['status'],404)
        self.assertEqual(self.objects.response.reads,1); self.assertTrue(self.objects.response.closed)
        self.assertEqual(self.leases()[0][1:3], ('failed',0))

    def test_expiry_during_buffering_and_after_headers_aborts(self):
        self.objects.read_hook = lambda: setattr(self, 'mono', 40)
        self.assertEqual(self.call()['status'],409)
        self.mono = 10; self.objects.read_hook = lambda: None
        stream = self.call(consume=False)['stream']
        self.now += timedelta(minutes=6)
        with self.assertRaises(DownloadInterrupted): next(stream)
        self.assertTrue(stream._spool.closed)

    def test_midstream_revoke_stops_remaining_bytes_and_records_offered_only(self):
        stream = self.call(consume=False)['stream']
        self.assertEqual(len(next(stream)), records.CHUNK_BYTES)
        self.revoke()
        with self.assertRaisesRegex(DownloadInterrupted, '^download_interrupted$'): next(stream)
        self.assertEqual(self.leases()[0][1:3], ('interrupted', records.CHUNK_BYTES))
        self.assertTrue(stream._spool.closed)

    def test_disconnect_and_never_iterated_close_release_capacity(self):
        stream = self.call(consume=False)['stream']; stream.close(); stream.close()
        self.assertEqual(self.leases()[0][1:3], ('interrupted',0))
        self.assertEqual(self.call()['status'],200)

    def test_new_candidate_does_not_invalidate_but_new_approval_does(self):
        stream = self.call(consume=False)['stream']
        with sqlite3.connect(self.path) as c: c.execute('UPDATE artifact_indexes_v1 SET revision=4,candidate=published')
        self.assertEqual(len(next(stream)), records.CHUNK_BYTES)
        with sqlite3.connect(self.path) as c: c.execute('UPDATE artifact_indexes_v1 SET revision=5,published_revision=5')
        with self.assertRaises(DownloadInterrupted): next(stream)

    def test_grant_policy_or_current_member_changes_interrupt(self):
        for mutation in ('grant','index','module'):
            with self.subTest(mutation=mutation):
                stream = self.call(consume=False)['stream']
                with sqlite3.connect(self.path) as c:
                    if mutation=='grant': c.execute('UPDATE artifact_download_policies_v1 SET policy=?',(records.policy_text(replace(GRANT,downloaders=('admin',))),))
                    if mutation=='index': c.execute('UPDATE artifact_indexes_v1 SET policy=?',(index_text(replace(POLICY,readers=('admin',))),))
                    if mutation=='module': c.execute("UPDATE modules SET can_read=0 WHERE workspace_id='alpha'")
                with self.assertRaises(DownloadInterrupted): next(stream)
                with sqlite3.connect(self.path) as c:
                    c.execute('UPDATE artifact_download_policies_v1 SET policy=?',(records.policy_text(GRANT),))
                    c.execute('UPDATE artifact_indexes_v1 SET policy=?',(index_text(POLICY),))
                    c.execute("UPDATE modules SET can_read=1 WHERE workspace_id='alpha'")

    def test_per_actor_capacity_shared_by_independent_service_instances(self):
        stream = self.call(consume=False)['stream']
        service = ArtifactSynchronizer(**self.args,credential_loader=self.service._credential_loader)
        other = ArtifactDownloadAPI(downloader=ArtifactDownloader(service=service,transport=self.objects,monotonic=lambda:self.mono),origin='https://app.example.invalid')
        before=len(self.objects.calls)
        self.assertEqual(self.call(api=other)['status'],429); self.assertEqual(len(self.objects.calls),before)
        stream.close(); self.assertEqual(self.call(api=other)['status'],200)

    def test_admission_race_has_only_one_winner(self):
        barrier=Barrier(2); results=[]
        def worker():
            barrier.wait();results.append(self.call(consume=False))
        threads=[Thread(target=worker) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(sorted(r['status'] for r in results),[200,429])
        for r in results:
            if hasattr(r['stream'],'close'):r['stream'].close()
        self.assertEqual(len(self.objects.calls),1)

    def test_expiry_does_not_overbook_still_owned_resource(self):
        old=self.call(consume=False)['stream'];self.now+=timedelta(seconds=31);self.mono+=31
        self.assertEqual(self.call()['status'],429)
        with self.assertRaises(DownloadInterrupted):next(old)
        new=self.call(consume=False)['stream']
        self.assertEqual(b''.join(new),DATA)

    def test_bad_or_missing_schema_fail_closed_before_secret(self):
        with sqlite3.connect(self.path) as c:c.execute('DROP TABLE artifact_download_leases_v1')
        self.assertEqual(self.call()['status'],503);self.assertEqual(self.loads,[])
        with sqlite3.connect(self.path) as c, self.assertRaises(StorageError): records.check_schema(c)

    def test_admission_audit_failure_rolls_back_and_does_not_call_provider(self):
        with sqlite3.connect(self.path) as c:c.execute("CREATE TRIGGER refuse BEFORE INSERT ON artifact_download_leases_v1 BEGIN SELECT RAISE(ABORT,'DO_NOT_LEAK'); END")
        result=self.call();self.assertEqual(result['status'],503);self.assertNotIn(b'DO_NOT_LEAK',result['raw'])
        self.assertEqual(self.leases(),[]);self.assertEqual(self.loads,[])

    def test_initialization_idempotent_and_rejects_changed_policy(self):
        initialize_downloads(self.store,(GRANT,))
        with self.assertRaises(ValueError):initialize_downloads(self.store,(replace(GRANT,downloaders=('admin',)),))
        self.assertEqual(self.call()['status'],200)

    def test_credential_scope_mismatch_is_redacted_and_no_object_network(self):
        self.service._credential_loader=lambda *_:credential(replace(SCOPE,workspace_id='beta'))
        result=self.call();self.assertEqual(result['status'],502);self.assertEqual(self.objects.calls,[])

    def test_global_capacity_and_reader_grants_are_both_required(self):
        grant = replace(GRANT, downloaders=('reader', 'admin', 'approver'))
        index = replace(POLICY, readers=('reader', 'admin', 'approver'))
        with sqlite3.connect(self.path) as c:
            c.execute('UPDATE artifact_download_policies_v1 SET policy=?', (records.policy_text(grant),))
            c.execute('UPDATE artifact_indexes_v1 SET policy=?', (index_text(index),))
        one=self.call(consume=False)['stream']; two=self.call(who='admin',consume=False)['stream']
        self.assertEqual(self.call(who='approver')['status'],429)
        one.close(); self.assertEqual(self.call(who='approver')['status'],200); two.close()

    def test_actor_rebinding_and_assertion_expiry_rechecked_before_output(self):
        stream=self.call(consume=False)['stream']
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE principals SET subject='rebound' WHERE id='reader'")
        with self.assertRaises(DownloadInterrupted):next(stream)
        self.assertTrue(stream._spool.closed)

    def test_zero_byte_object_still_requires_grant_hash_and_checks(self):
        from packages.artifact_sync.records import snapshot_text,decode_snapshot
        binding=replace(BINDING,size=0,sha256=sha256(b'').hexdigest())
        with sqlite3.connect(self.path) as c:
            snap=decode_snapshot(c.execute('SELECT published FROM artifact_indexes_v1').fetchone()[0], CONFIG)
            snap=replace(snap,artifacts=(replace(snap.artifacts[0],size=0),))
            c.execute('UPDATE artifact_indexes_v1 SET published=?',(snapshot_text(snap),))
            c.execute('UPDATE artifact_download_policies_v1 SET policy=?',(records.policy_text(replace(GRANT,bindings=(binding,))),))
        self.objects.data=b''
        result=self.call();self.assertEqual(result['status'],200);self.assertEqual(result['raw'],b'')
        self.assertEqual(self.leases(),[('reader','complete',0,0)])

    def test_schema_constraints_and_canonical_policy_checked_on_each_request(self):
        with sqlite3.connect(self.path) as c:
            c.execute('DROP TABLE artifact_download_leases_v1')
            c.execute(records._LEASE_CREATE.replace("CHECK(state IN ('active','complete','interrupted','failed'))", ''))
        self.assertEqual(self.call()['status'],503);self.assertEqual(self.loads,[])
        with self.assertRaises(StorageError):initialize_downloads(self.store,(GRANT,))

    def test_close_audit_failure_is_bounded_and_does_not_append_raw_error(self):
        stream=self.call(consume=False)['stream']
        with sqlite3.connect(self.path) as c:c.execute("CREATE TRIGGER fail_close BEFORE UPDATE ON artifact_download_leases_v1 BEGIN SELECT RAISE(ABORT,'DO_NOT_LEAK'); END")
        stream.close();self.assertTrue(stream._spool.closed)
        self.assertEqual(self.call()['status'],429)
        self.now+=timedelta(seconds=31);self.mono+=31
        with sqlite3.connect(self.path) as c:c.execute('DROP TRIGGER fail_close')
        # Even expired, audit failure cannot silently free possibly held resources.
        self.assertEqual(self.call()['status'],429)

    def test_credential_loader_revoke_and_provider_failure_cleanup(self):
        self.service._credential_loader=lambda *_:(self.revoke(),credential())[1]
        result=self.call();self.assertEqual(result['status'],404);self.assertEqual(self.objects.calls,[])
        self.assertEqual(self.leases()[0][1:3],('failed',0))

    def test_upstream_close_failure_never_loses_spool_or_releases_owned_capacity(self):
        import packages.artifact_download.service as module
        created=[];original=module.tempfile.TemporaryFile
        def temp(*args,**kwargs):
            f=original(*args,**kwargs);created.append(f);return f
        def fail(): raise OSError('SYNTHETIC_CLOSE_ERROR')
        self.objects.read_hook=lambda:setattr(self.objects.response,'close',fail)
        with patch.object(module.tempfile,'TemporaryFile',side_effect=temp):result=self.call()
        self.assertEqual(result['status'],500);self.assertNotIn(b'SYNTHETIC_CLOSE_ERROR',result['raw'])
        self.assertTrue(created[0].closed);self.assertEqual(self.leases()[0][1:3],('active',0))
        self.now+=timedelta(seconds=31);self.mono+=31
        self.assertEqual(self.call()['status'],429)

    def test_archive_denies_new_external_read_and_interrupts_active_stream(self):
        stream=self.call(consume=False)['stream']
        self.store.set_workspace_state('admin','alpha',state='archived',expected_revision=1)
        with self.assertRaises(DownloadInterrupted):next(stream)
        before=len(self.objects.calls)
        self.assertEqual(self.call()['status'],404);self.assertEqual(len(self.objects.calls),before)

    def test_start_response_failure_closes_uniterated_spool(self):
        prepared=[]
        original=self.downloader._prepare_verified
        def prepare(*args,**kwargs):
            stream=original(*args,**kwargs);prepared.append(stream);return stream
        self.downloader._prepare_verified=prepare
        # Failure occurs after acquisition but before the server accepts headers.
        original_api=self.api
        def reject(env,start):
            def broken(*_):raise OSError('synthetic disconnected server')
            return original_api(env,broken)
        with self.assertRaises(OSError):self.call(api=reject)
        self.assertTrue(prepared[0]._spool.closed);self.assertEqual(self.leases()[0][1:3],('interrupted',0))

    def test_real_http_midstream_revoke_is_truncated_not_json_success(self):
        class Quiet(WSGIRequestHandler):
            def log_message(self,*args):pass
        server=make_server('127.0.0.1',0,lambda *_:None,handler_class=Quiet)
        api=ArtifactDownloadAPI(downloader=self.downloader,origin=f'http://127.0.0.1:{server.server_port}')
        def application(env,start):
            stream=api(env,start)
            def output():
                try:
                    first=True
                    for chunk in stream:
                        yield chunk
                        if first:self.revoke();first=False
                finally:
                    if hasattr(stream,'close'):stream.close()
            return output()
        server.set_app(application)
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            conn.request('POST',BASE,json.dumps({'key':KEY}),{'Content-Type':'application/json','If-Match':'"3"','Authorization':'Bearer synthetic-reader'})
            response=conn.getresponse();self.assertEqual(response.status,200)
            with self.assertRaises(http.client.IncompleteRead) as caught:response.read()
            self.assertEqual(caught.exception.partial,DATA[:records.CHUNK_BYTES]);conn.close()
        finally:server.shutdown();thread.join();server.server_close()

    def test_real_loopback_http_stream(self):
        class Quiet(WSGIRequestHandler):
            def log_message(self,*args):pass
        server=make_server('127.0.0.1',0,lambda *_:None,handler_class=Quiet)
        server.set_app(ArtifactDownloadAPI(downloader=self.downloader,origin=f'http://127.0.0.1:{server.server_port}'))
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            conn.request('POST',BASE,json.dumps({'key':KEY}),{'Content-Type':'application/json','If-Match':'"3"','Authorization':'Bearer synthetic-reader'})
            response=conn.getresponse();self.assertEqual(response.status,200);self.assertEqual(response.read(),DATA);conn.close()
        finally:server.shutdown();thread.join();server.server_close()


if __name__=='__main__':unittest.main()
