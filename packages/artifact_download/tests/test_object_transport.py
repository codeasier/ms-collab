"""Synthetic protocol fixtures; no cloud account, credentials or network used."""
from base64 import b64encode
from dataclasses import replace
from email.utils import format_datetime
import hashlib
import hmac
import io
import http.client
import time
import socket
import ssl
import unittest
from unittest.mock import patch, Mock

from packages.artifact_download.provider import OBSObjectTransport, request_parts, _TimedHTTPS
from packages.artifact_download.records import ContentBinding, DownloadPolicy, MAX_BYTES
from packages.artifact_sync.provider import ProviderError, _PinnedHTTPS
from packages.artifacts.catalog import instant
from test_download import BINDING, CONFIG, DATA, GRANT, NOW, credential


class WireResponse:
    status = 200
    def __init__(self):
        self.fields = [('Content-Length', str(len(DATA))), ('ETag', BINDING.etag),
                       ('Last-Modified', format_datetime(instant(BINDING.modified_at),usegmt=True)),
                       ('Content-Type','text/html'), ('Content-Disposition','inline')]
        self.content = io.BytesIO(DATA)
    def getheaders(self): return self.fields
    def read1(self, size): return self.content.read(size)
    def close(self): self.content.close()


class Wire:
    def __init__(self):
        self.response = WireResponse(); self.calls = []; self.closed = False
    def request(self, method, path, headers): self.calls.append((method,path,headers))
    def getresponse(self): return self.response
    def close(self): self.closed=True
    def abort(self): self.closed=True


class ProviderTests(unittest.TestCase):
    def test_signed_literal_key_and_session_token_are_header_only(self):
        secret = replace(credential(),security_token='synthetic-session-material')
        binding = replace(BINDING,key='builds/a b+%2F?x=#中~.bin')
        path,headers = request_parts(CONFIG,secret,binding,NOW)
        self.assertEqual(path,'/builds/a%20b%2B%252F%3Fx%3D%23%E4%B8%AD~.bin')
        date=format_datetime(NOW,usegmt=True)
        canonical=f'GET\n\n\n{date}\nx-obs-security-token:synthetic-session-material\n/synthetic-bucket{path}'
        signature=b64encode(hmac.new(secret.secret_key.encode(),canonical.encode(),hashlib.sha1).digest()).decode()
        self.assertEqual(headers['Authorization'],f'OBS {secret.access_key}:{signature}')
        self.assertEqual(headers['If-Match'],BINDING.etag)
        self.assertNotIn(secret.access_key,path);self.assertNotIn('?',path)
        self.assertNotIn(secret.secret_key,repr(secret))

    def test_fixed_host_identity_encoding_tls_and_close(self):
        wire=Wire()
        with patch('packages.artifact_download.provider._TimedHTTPS',return_value=wire) as ctor:
            response=OBSObjectTransport().open(CONFIG,credential(),BINDING,NOW,remaining=10.0)
            self.assertEqual(ctor.call_args.args,(CONFIG.host,))
            self.assertEqual(ctor.call_args.kwargs['timeout'],5)
            context=ctor.call_args.kwargs['context']
            self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED);self.assertTrue(context.check_hostname)
            self.assertEqual(wire.calls[0][:2],('GET','/builds/synthetic.bin'))
            self.assertEqual(wire.calls[0][2]['Accept-Encoding'],'identity')
            self.assertEqual(response.read(7),DATA[:7]);response.close();self.assertTrue(wire.closed)

    def test_redirect_range_compression_and_critical_header_ambiguity_rejected(self):
        changes=[]
        for code in (201,206,301,302,307,304,403,412):
            changes.append(lambda r,code=code:setattr(r,'status',code))
        for field in (('Content-Length',str(len(DATA))),('Content-Length','0'),('ETag',BINDING.etag),
                      ('Last-Modified','yesterday'),('Content-Encoding','gzip'),('Content-Encoding','identity'),
                      ('Transfer-Encoding','chunked'),('Content-Range','bytes 0-1/2')):
            changes.append(lambda r,field=field:r.fields.append(field))
        # one identity content encoding is fine, so explicitly duplicate it
        changes[-3]=lambda r:r.fields.extend([('Content-Encoding','identity')]*2)
        for change in changes:
            wire=Wire();change(wire.response)
            with patch('packages.artifact_download.provider._TimedHTTPS',return_value=wire),self.assertRaises(ProviderError):
                OBSObjectTransport().open(CONFIG,credential(),BINDING,NOW,remaining=1)
            self.assertTrue(wire.closed)
        for name in ('content-length','etag','last-modified'):
            wire=Wire();wire.response.fields=[p for p in wire.response.fields if p[0].lower()!=name]
            with patch('packages.artifact_download.provider._TimedHTTPS',return_value=wire),self.assertRaises(ProviderError):
                OBSObjectTransport().open(CONFIG,credential(),BINDING,NOW,remaining=1)

    def test_scope_prefix_and_timing_fail_before_socket(self):
        for secret,binding,remaining in ((credential(origin='https://other.invalid'),BINDING,1),
                (credential(),replace(BINDING,key='other/key'),1),(credential(),BINDING,0),(credential(),BINDING,31)):
            with patch('packages.artifact_download.provider._TimedHTTPS') as ctor,self.assertRaises(ProviderError):
                OBSObjectTransport().open(CONFIG,secret,binding,NOW,remaining=remaining)
            ctor.assert_not_called()

    def test_private_dns_rejected_before_socket_and_tls_hostname_pinned(self):
        for ip in ('127.0.0.1','10.0.0.1','169.254.169.254','192.168.1.1','224.0.0.1','::1','fc00::1','::ffff:127.0.0.1'):
            family=socket.AF_INET6 if ':' in ip else socket.AF_INET
            answer=[(family,socket.SOCK_STREAM,6,'',(ip,443))]
            with patch('packages.artifact_sync.provider.socket.getaddrinfo',return_value=answer),patch('packages.artifact_sync.provider.socket.socket') as sock:
                client=_PinnedHTTPS(CONFIG.host,timeout=1,context=ssl.create_default_context())
                with self.assertRaises(OSError):client.connect()
                sock.assert_not_called()
        context=Mock();raw=Mock()
        answer=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))]
        with patch('packages.artifact_sync.provider.socket.getaddrinfo',return_value=answer),patch('packages.artifact_sync.provider.socket.socket',return_value=raw):
            client=_PinnedHTTPS(CONFIG.host,timeout=1,context=context);client.connect()
            raw.connect.assert_called_once_with(('8.8.8.8',443));context.wrap_socket.assert_called_once_with(raw,server_hostname=CONFIG.host)

    def test_network_deadline_aborts_socket_and_late_connect_never_sends_request(self):
        client=_TimedHTTPS(CONFIG.host,timeout=1,context=ssl.create_default_context(),deadline=1)
        client.sock=Mock();sock=client.sock;client.abort();sock.shutdown.assert_called_once_with(socket.SHUT_RDWR);sock.close.assert_called_once()
        with patch('packages.artifact_sync.provider._PinnedHTTPS.connect'),patch('packages.artifact_download.provider.time.monotonic',return_value=2):
            with self.assertRaises(TimeoutError):client.connect()

    def test_connection_close_response_file_and_deadline_socket_not_detached(self):
        # Real http.client socketpair proves will_close detaches client.sock but
        # the response owns a live makefile; no internet connection is involved.
        from packages.artifact_download.provider import _ObjectResponse
        left,right=socket.socketpair()
        try:
            client=_TimedHTTPS(CONFIG.host,timeout=1,context=ssl.create_default_context(),deadline=time.monotonic()+2)
            client.sock=left;client._transport_socket=left
            client.putrequest('GET','/synthetic');client.endheaders();right.recv(4096)
            right.sendall(b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 100\r\n\r\nx')
            response=client.getresponse();self.assertIsNone(client.sock);self.assertIsNotNone(response.fp)
            timer=Mock();wrapped=_ObjectResponse(client,response,timer)
            self.assertEqual(wrapped.read(1),b'x')
            client.abort()
            self.assertEqual(wrapped.read(1),b'')
            wrapped.close();self.assertTrue(response.isclosed());self.assertIsNone(response.fp)
            timer.cancel.assert_called_once()
        finally:left.close();right.close()

    def test_binding_bounds_and_no_header_or_path_injection(self):
        for fields in ({'size':True},{'size':MAX_BYTES+1},{'key':'builds/'},{'key':'//evil.invalid/a'},
                       {'key':'a/../b'},{'key':'a\x00b'},{'etag':'W/"weak"'},{'etag':'"a"\r\nx:y'},
                       {'sha256':'X'*64},{'modified_at':'2026-01-01T00:00:00.001Z'}):
            with self.assertRaises(ValueError):replace(BINDING,**fields)
        for fields in ({'downloaders':('reader','reader')},{'downloaders':()},{'bindings':(BINDING,BINDING)}):
            with self.assertRaises(ValueError):replace(GRANT,**fields)


if __name__=='__main__':unittest.main()
