"""Default transport protocol, ownership and bounded waiting, with synthetic I/O."""
import http.client
import io
import socket
import threading
import time
import unittest
from unittest.mock import patch

from packages.community_board import Scope
from packages.community_sync import BoundCredential, Connection, ProviderError
from packages.community_refresh.transport import BoundedGitCodeReadTransport, RefreshCleanupError, _PinnedHTTPS

CONFIG = Connection(Scope('alpha','source','sample/project'))
SECRET = BoundCredential(CONFIG.scope,'synthetic-not-a-real-token')
PATH = '/api/v5/repos/sample/project/issues?state=all&sort=updated&direction=desc&page=1&per_page=100'


class Response:
    def __init__(self, body=b'[]', *, headers=None, status=200):
        self.body=io.BytesIO(body); self.closed=False; self.status=status
        self.headers=headers if headers is not None else [('Content-Length',str(len(body))),('Content-Type','application/json')]
    def getheaders(self): return self.headers
    def read1(self,n): return self.body.read(n)
    def close(self): self.closed=True; self.body.close()


class Client:
    def __init__(self,response): self.response=response; self.closed=False; self.requests=[]
    def guard(self): pass
    def request(self,*a,**kw): self.requests.append((a,kw))
    def getresponse(self): return self.response
    def close(self): self.closed=True


class TransportTests(unittest.TestCase):
    def run_transport(self,response,remaining=1):
        client=Client(response)
        with patch('packages.community_refresh.transport._PinnedHTTPS',return_value=client):
            result=BoundedGitCodeReadTransport().get(PATH,SECRET,CONFIG,remaining=remaining)
        return result,client

    def test_fixed_get_json_and_cleanup(self):
        response=Response(); result,client=self.run_transport(response)
        self.assertEqual(result,[]); self.assertTrue(client.closed and response.closed)
        args,kw=client.requests[0]; self.assertEqual(args,('GET',PATH))
        self.assertEqual(kw['headers']['Authorization'],'Bearer '+SECRET.token)
        self.assertEqual(kw['headers']['Accept-Encoding'],'identity')

    def test_request_scope_and_fixed_path_before_worker(self):
        bad=(PATH.replace('/issues?','/comments?'), 'https://evil.invalid'+PATH, '//evil.invalid'+PATH,
             PATH+'&page=2',PATH.replace('page=1&','page=51&'), PATH.replace('per_page=100','per_page=1'),
             PATH+'#fragment',PATH.replace('state=all','state=open'),PATH.replace('sample/project','other/repo'))
        with patch('packages.community_refresh.transport._PinnedHTTPS') as client:
            for path in bad:
                with self.assertRaises(ProviderError): BoundedGitCodeReadTransport().get(path,SECRET,CONFIG,remaining=1)
            for value in (True,0,-1,31,float('nan'),float('inf')):
                with self.assertRaises(ProviderError): BoundedGitCodeReadTransport().get(PATH,SECRET,CONFIG,remaining=value)
            with self.assertRaises(ProviderError): BoundedGitCodeReadTransport().get(PATH,BoundCredential(Scope('beta','source','sample/project'),'synthetic'),CONFIG,remaining=1)
            self.assertFalse(client.called)

    def test_ambiguous_framing_and_untrusted_content(self):
        variants=([('Content-Length','2'),('content-length','2'),('Content-Type','application/json')],
            [('Content-Type','application/json')], [('Content-Length','2'),('Content-Type','text/html')],
            [('Content-Length','2'),('Content-Type','application/json'),('Content-Encoding','gzip')],
            [('Content-Length','2'),('Content-Type','application/json'),('Transfer-Encoding','chunked')],
            [('Content-Length','2'),('Content-Type','application/json'),('Content-Range','bytes 0-1/2')],
            [('Content-Length','9999999'),('Content-Type','application/json')])
        for headers in variants:
            response=Response(headers=headers)
            with self.assertRaises(ProviderError): self.run_transport(response)
            self.assertTrue(response.closed)
        for status in (301,302,307,429,500):
            with self.assertRaises(ProviderError): self.run_transport(Response(status=status))
        for raw in (b'{"a":1,"a":2}',b'[NaN]',b'[1e999]',b'[-1e999]',b'private-not-json',b'\xff'):
            with self.assertRaises(ProviderError) as error:self.run_transport(Response(raw))
            self.assertNotIn('private',str(error.exception)); self.assertNotIn(SECRET.token,str(error.exception))
        for body,size in ((b'a',2),(b'ab',1)):
            with self.assertRaises(ProviderError):self.run_transport(Response(body,headers=[('Content-Length',str(size)),('Content-Type','application/json')]))

    def test_failure_cleanup_is_uncertain_and_redacted(self):
        response=Response()
        response.close=lambda:(_ for _ in ()).throw(OSError('private-response-marker'))
        with self.assertRaises(RefreshCleanupError) as error:self.run_transport(response)
        self.assertNotIn('private-response-marker',str(error.exception))

    def test_cleanup_blocked_never_signals_completion(self):
        entered,release,closed=threading.Event(),threading.Event(),threading.Event()
        response=Response()
        def close():entered.set();release.wait(3);closed.set()
        response.close=close
        try:
            start=time.monotonic()
            with self.assertRaises(RefreshCleanupError):self.run_transport(response,remaining=.1)
            self.assertLess(time.monotonic()-start,1); self.assertTrue(entered.is_set());self.assertFalse(closed.is_set())
        finally:release.set();self.assertTrue(closed.wait(2))

    def test_dns_unknown_is_bounded_and_no_socket_created_after_deadline(self):
        entered,release,returned=threading.Event(),threading.Event(),threading.Event()
        def dns(*a,**k):entered.set();release.wait(3);returned.set();return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]
        with patch('packages.community_refresh.transport.socket.getaddrinfo',side_effect=dns),patch('packages.community_refresh.transport.socket.socket') as make:
            try:
                start=time.monotonic()
                with self.assertRaises(RefreshCleanupError):BoundedGitCodeReadTransport().get(PATH,SECRET,CONFIG,remaining=.1)
                self.assertLess(time.monotonic()-start,1);self.assertTrue(entered.is_set())
            finally:release.set();self.assertTrue(returned.wait(2))
            self.assertFalse(make.called)

    def test_private_dns_rejected_before_socket(self):
        for address in ('127.0.0.1','10.0.0.1','169.254.169.254','100.64.0.1','224.0.0.1','::1','fc00::1'):
            family=socket.AF_INET6 if ':' in address else socket.AF_INET
            with patch('packages.community_refresh.transport.socket.getaddrinfo',return_value=[(family,socket.SOCK_STREAM,6,'',(address,443))]),patch('packages.community_refresh.transport.socket.socket') as make:
                with self.assertRaises(ProviderError):BoundedGitCodeReadTransport().get(PATH,SECRET,CONFIG,remaining=1)
                self.assertFalse(make.called)

    def test_connection_close_real_response_retains_file_until_cleanup(self):
        left,right=socket.socketpair();self.addCleanup(right.close)
        right.sendall(b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 2\r\nContent-Type: application/json\r\n\r\n[]')
        owner=_PinnedHTTPS(deadline=time.monotonic()+2,timeout=1)
        owner.sock=left;owner._owned=left;owner._HTTPConnection__state=http.client._CS_REQ_SENT
        # getresponse calls owner's close before response body has been read.
        response=owner.getresponse();self.assertIsNone(owner.sock)
        owner.guard()
        client=Client(response);client.guard=owner.guard;client.close=owner.close
        with patch('packages.community_refresh.transport._PinnedHTTPS',return_value=client):
            self.assertEqual(BoundedGitCodeReadTransport().get(PATH,SECRET,CONFIG,remaining=1),[])
        self.assertIsNone(response.fp);self.assertEqual(left.fileno(),-1)

    def test_slow_read1_returns_bounded_with_owned_worker(self):
        left,right=socket.socketpair();self.addCleanup(right.close)
        right.sendall(b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 10\r\nContent-Type: application/json\r\n\r\n[')
        owner=_PinnedHTTPS(deadline=time.monotonic()+.15,timeout=.15)
        owner.sock=left;owner._owned=left;owner._HTTPConnection__state=http.client._CS_REQ_SENT
        response=owner.getresponse();client=Client(response);client.guard=owner.guard;client.close=owner.close
        closed=threading.Event();original=client.close
        def close():
            try:original()
            finally:closed.set()
        client.close=close
        with patch('packages.community_refresh.transport._PinnedHTTPS',return_value=client):
            start=time.monotonic()
            with self.assertRaises(ProviderError):BoundedGitCodeReadTransport().get(PATH,SECRET,CONFIG,remaining=.15)
            self.assertLess(time.monotonic()-start,1)
        self.assertTrue(closed.wait(2));self.assertIsNone(response.fp);self.assertEqual(left.fileno(),-1)


if __name__=='__main__':unittest.main()
