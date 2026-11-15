"""Default fixed-origin transport framing, socket ownership and timeout probes."""
from datetime import timezone
import http.client,io,socket,ssl,threading,time,unittest
from unittest.mock import patch
from packages.artifact_refresh.transport import BoundedOBSReadTransport,_TimedHTTPS,RefreshCleanupError
from packages.artifact_sync import ProviderError
from packages.artifact_sync.tests.fixtures import CONFIG,NOW,credential,page

class Response:
    def __init__(self,body=None,headers=None,status=200):
        self.body=io.BytesIO(body if body is not None else page());self.closed=False;self.status=status
        self.headers=headers or [('Content-Length',str(len(self.body.getvalue()))),('Content-Type','application/xml')]
    def getheaders(self):return self.headers
    def read1(self,n):return self.body.read(n)
    def close(self):self.closed=True;self.body.close()

class Client:
    cleanup_uncertain=False
    def __init__(self,response):self.response=response;self.closed=False;self.requests=[]
    def request(self,*a,**kw):self.requests.append((a,kw))
    def getresponse(self):return self.response
    def abort(self):pass
    def close(self):self.closed=True

class TransportTests(unittest.TestCase):
    def run_transport(self,response,**kwargs):
        client=Client(response)
        with patch('packages.artifact_refresh.transport._TimedHTTPS',return_value=client):
            result=BoundedOBSReadTransport().list_page(CONFIG,credential(),'',NOW,remaining=1,**kwargs)
        return result,client
    def test_exact_xml_and_fixed_get_and_cleanup(self):
        response=Response();raw,client=self.run_transport(response)
        self.assertEqual(raw,page());self.assertTrue(response.closed and client.closed)
        args,kwargs=client.requests[0];self.assertEqual(args[0],'GET');self.assertTrue(args[1].startswith('/?prefix=builds%2F&'))
        self.assertNotIn('http',args[1]);self.assertEqual(kwargs['headers']['Accept-Encoding'],'identity')
    def test_ambiguous_framing_content_and_redirect_are_rejected_and_closed(self):
        variants=([('Content-Length','1'),('content-length','1'),('Content-Type','application/xml')],
            [('Content-Type','application/xml')],[('Content-Length','1'),('Content-Type','text/html')],
            [('Content-Length','1'),('Content-Type','application/xml'),('Transfer-Encoding','chunked')],
            [('Content-Length','1'),('Content-Type','application/xml'),('Content-Encoding','gzip')],
            [('Content-Length','1'),('Content-Type','application/xml'),('Content-Range','bytes 0-0/1')],
            [('Content-Length','999999'),('Content-Type','application/xml')])
        for headers in variants:
            r=Response(headers=headers)
            with self.assertRaises(ProviderError):self.run_transport(r)
            self.assertTrue(r.closed)
        with self.assertRaises(ProviderError):self.run_transport(Response(status=302))
    def test_short_or_overlong_body_rejected(self):
        for body,size in ((b'a',2),(b'ab',1)):
            with self.assertRaises(ProviderError):self.run_transport(Response(body,headers=[('Content-Length',str(size)),('Content-Type','text/xml')]))
    def test_close_failure_is_not_normal_provider_error(self):
        r=Response()
        def fail():raise OSError('private diagnostic')
        r.close=fail
        with self.assertRaises(RefreshCleanupError) as error:self.run_transport(r)
        self.assertNotIn('private diagnostic',str(error.exception))
    def test_timer_shutdown_failure_retained_even_if_main_cleanup_succeeds(self):
        r=Response();client=Client(r);client.cleanup_uncertain=True
        with patch('packages.artifact_refresh.transport._TimedHTTPS',return_value=client):
            with self.assertRaises(RefreshCleanupError):BoundedOBSReadTransport().list_page(CONFIG,credential(),'',NOW,remaining=1)
        self.assertTrue(r.closed and client.closed)
    def test_bound_scope_and_origin_fail_before_client(self):
        with patch('packages.artifact_refresh.transport._TimedHTTPS') as constructor:
            with self.assertRaises(ProviderError):BoundedOBSReadTransport().list_page(CONFIG,credential(origin='https://other.invalid'),'',NOW,remaining=1)
            self.assertFalse(constructor.called)
    def test_private_dns_rejected_before_socket(self):
        addresses=('127.0.0.1','10.0.0.1','169.254.169.254','192.168.1.1','100.64.0.1','224.0.0.1','::1','fc00::1')
        for value in addresses:
            family=socket.AF_INET6 if ':' in value else socket.AF_INET
            with patch('packages.artifact_sync.provider.socket.getaddrinfo',return_value=[(family,socket.SOCK_STREAM,6,'',(value,443))]),patch('packages.artifact_sync.provider.socket.socket') as make:
                with self.assertRaises(ProviderError):BoundedOBSReadTransport().list_page(CONFIG,credential(),'',NOW,remaining=1)
                self.assertFalse(make.called)
    def test_connection_close_detached_socket_is_aborted_and_response_closed(self):
        left,right=socket.socketpair();self.addCleanup(right.close)
        right.sendall(b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 10\r\nContent-Type: application/xml\r\n\r\n')
        owner=_TimedHTTPS(CONFIG.host,context=ssl.create_default_context(),deadline=time.monotonic()+1)
        owner.sock=left;owner._transport_socket=left;owner._HTTPConnection__state=http.client._CS_REQ_SENT
        response=owner.getresponse();self.assertIsNone(owner.sock)
        client=Client(response);client.abort=owner.abort
        client.close=owner.close
        started=time.monotonic()
        with patch('packages.artifact_refresh.transport._TimedHTTPS',return_value=client):
            with self.assertRaises(ProviderError):BoundedOBSReadTransport().list_page(CONFIG,credential(),'',NOW,remaining=.1)
        self.assertLess(time.monotonic()-started,1);self.assertIsNone(response.fp);self.assertEqual(left.fileno(),-1)
    def test_abort_failure_recorded_and_closing_still_attempted(self):
        class BadSocket:
            closed=False
            def shutdown(self,*a):raise OSError()
            def close(self):self.closed=True
        owner=_TimedHTTPS(CONFIG.host,context=ssl.create_default_context(),deadline=time.monotonic()+1)
        sock=BadSocket();owner._transport_socket=sock;owner.abort()
        self.assertTrue(owner.cleanup_uncertain and sock.closed)

if __name__=='__main__':unittest.main()
