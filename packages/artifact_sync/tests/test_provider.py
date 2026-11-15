"""Offline wire-protocol, resource bounds and credential routing checks."""
from dataclasses import replace
from datetime import timedelta
from hashlib import sha1
import base64
import hmac
import socket
import unittest
from unittest.mock import MagicMock, patch

from packages.artifact_sync import BoundCredential, Connection, OBSReadTransport, ProviderError
from packages.artifact_sync.provider import _PinnedHTTPS, collect, list_headers, parse_page
from packages.artifacts import Scope
from fixtures import CONFIG, NOW, SCOPE, Transport, credential, page


class ProviderTests(unittest.TestCase):
    def test_signature_uses_header_not_url_and_exact_bucket(self):
        headers = list_headers(CONFIG, credential(), NOW)
        payload = b'GET\n\n\nFri, 02 Jan 2026 00:00:00 GMT\n/synthetic-bucket/'
        expected = base64.b64encode(hmac.new(credential().secret_key.encode(), payload, sha1).digest()).decode()
        self.assertEqual(headers['Authorization'], 'OBS SYNTHETICACCESSKEY:'+expected)
        self.assertNotIn(credential().secret_key, repr(headers))

    def test_session_token_is_signed_and_repr_redacted(self):
        secret = replace(credential(), security_token='synthetic-session-token')
        headers = list_headers(CONFIG, secret, NOW)
        payload = b'GET\n\n\nFri, 02 Jan 2026 00:00:00 GMT\nx-obs-security-token:synthetic-session-token\n/synthetic-bucket/'
        expected = base64.b64encode(hmac.new(secret.secret_key.encode(), payload, sha1).digest()).decode()
        self.assertTrue(headers['Authorization'].endswith(expected))
        for value in (secret.secret_key, secret.access_key, secret.security_token):
            self.assertNotIn(value, repr(secret))

    def test_binding_mismatch_prevents_transport(self):
        for secret in (replace(credential(), scope=Scope('beta','source','bucket')), replace(credential(), origin='https://example.invalid')):
            with self.assertRaisesRegex(ProviderError, '^credential_scope_mismatch$'):
                collect(CONFIG, secret, before_read=lambda: None, transport=Transport(), clock=lambda: NOW)

    def test_connection_rejects_endpoint_injection_and_unbounded_values(self):
        for field, values in {'bucket':['a.b','a/b','localhost','UPPER','ab'], 'region':['localhost','eu-west-1.evil','x\n'],
                              'prefix':['../','relative','/root/','bad\\path/'], 'page_size':[True,0,101], 'max_pages':[0,11],
                              'window_hours':[0,721], 'timeout':[0,16], 'offset_minutes':[841]}.items():
            for value in values:
                # localhost is an allowed bucket label on the fixed OBS cloud suffix.
                if field == 'bucket' and value == 'localhost': continue
                with self.subTest(field=field,value=value), self.assertRaises(ValueError):
                    replace(CONFIG, **{field:value})
        with self.assertRaises(ValueError): replace(CONFIG,page_size=100,max_pages=10)

    def test_encoded_literal_keys_and_discarded_metadata(self):
        name = 'builds/空间/%2f+file.sha256'
        rows, marker = parse_page(page(((name,'2026-01-01T23:30:00+00:00',7),)), CONFIG, '')
        self.assertEqual(rows[0].key, name); self.assertEqual(marker,'')
        self.assertNotIn('discard', repr(rows))

    def test_recent_window_filters_old_but_keeps_checksum_and_markers(self):
        pages = [page((('builds/a','2025-12-31T23:59:59Z',3),('builds/b/','2026-01-01T00:00:00Z',0)), truncated=True, next_marker='builds/b/'),
                 page((('builds/b/file.sha256','2026-01-01T23:00:00Z',64),), marker='builds/b/')]
        checks=[]; transport=Transport(pages)
        result=collect(CONFIG,credential(),before_read=lambda:checks.append(1),clock=lambda:NOW,transport=transport)
        self.assertEqual([r.key for r in result.objects],['builds/b/','builds/b/file.sha256'])
        self.assertEqual((result.pages,result.scanned,len(checks)),(2,3,2))

    def test_future_and_timezone_missing_timestamps_fail(self):
        for stamp in ('2026-01-02T00:00:01Z','2026-01-01T23:00:00'):
            with self.assertRaises(ProviderError):
                collect(CONFIG,credential(),before_read=lambda:None,clock=lambda:NOW,transport=Transport([page((('builds/a',stamp,1),))]))

    def test_schema_and_scope_mismatches_fail(self):
        for field,value in [('Name','other'),('Prefix','other%2F'),('Marker','builds%2Fx'),('MaxKeys','1000'),('EncodingType','none'),('IsTruncated','True')]:
            with self.subTest(field=field),self.assertRaises(ProviderError): parse_page(page(**{field:value}),CONFIG,'')
        for raw in (b'<x/>',b'<bad', b'\xff',page().replace(b'<Name>',b'<Name x="1">'),
                    page().replace(b'<Marker></Marker>',b'<Marker></Marker><Marker></Marker>'),
                    page().replace(b'</ListBucketResult>',b'<CommonPrefixes/></ListBucketResult>')):
            with self.assertRaises(ProviderError): parse_page(raw,CONFIG,'')

    def test_xml_entities_and_oversize_rejected(self):
        for raw in (b'<!DOCTYPE x [<!ENTITY x "bad">]>'+page(),b'x'*(CONFIG.max_response_bytes+1)):
            with self.assertRaises(ProviderError):parse_page(raw,CONFIG,'')

    def test_invalid_keys_sizes_and_order_fail(self):
        rows = [(('/outside','2026-01-01T00:00:00Z',1),),
                (('builds/../x','2026-01-01T00:00:00Z',1),),
                (('builds/dir/','2026-01-01T00:00:00Z',1),),
                (('builds/a','2026-01-01T00:00:00Z',9007199254740992),),
                (('builds/b','2026-01-01T00:00:00Z',1),('builds/a','2026-01-01T00:00:00Z',1)),
                (('builds/a','2026-01-01T00:00:00Z',1),('builds/a','2026-01-01T00:00:00Z',1))]
        for values in rows:
            with self.assertRaises(ProviderError):parse_page(page(values),CONFIG,'')
        with self.assertRaises(ProviderError):parse_page(page((('builds/a','2026-01-01T00:00:00Z',1),)).replace(b'builds%2Fa',b'builds%2Gx'),CONFIG,'')

    def test_truncation_must_advance_exactly_and_scan_limit_is_failure(self):
        row=(('builds/a','2026-01-01T00:00:00Z',1),)
        for raw in (page(row,truncated=True),page(row,truncated=True,next_marker='builds/b'),page(row,next_marker='builds/a'),page(truncated=True,next_marker='builds/a')):
            with self.assertRaises(ProviderError):parse_page(raw,CONFIG,'')
        config=replace(CONFIG,max_pages=1)
        with self.assertRaisesRegex(ProviderError,'^scan_limit_reached$'):
            collect(config,credential(),before_read=lambda:None,clock=lambda:NOW,transport=Transport([page(row,truncated=True,next_marker='builds/a')]))

    def test_denial_before_next_page_stops_network(self):
        transport=Transport([page((('builds/a','2026-01-01T00:00:00Z',1),),truncated=True,next_marker='builds/a')])
        def check():
            if transport.calls:raise PermissionError('revoked')
        with self.assertRaises(PermissionError):collect(CONFIG,credential(),before_read=check,clock=lambda:NOW,transport=transport)
        self.assertEqual(len(transport.calls),1)

    def test_real_transport_is_get_only_bounded_and_closed(self):
        client=MagicMock();response=client.getresponse.return_value
        response.status=200;response.getheader.side_effect=lambda k,d='':{'Content-Type':'application/xml','Content-Encoding':'identity'}.get(k,d)
        response.read.return_value=page()
        with patch('packages.artifact_sync.provider._PinnedHTTPS',return_value=client) as ctor:
            self.assertEqual(OBSReadTransport().list_page(CONFIG,credential(),'builds/a +%',NOW),page())
            self.assertEqual(ctor.call_args.args,(CONFIG.host,))
            method,path=client.request.call_args.args
            self.assertEqual(method,'GET');self.assertIn('marker=builds%2Fa+%2B%25',path)
            self.assertNotIn('SYNTHETICACCESSKEY',path);self.assertNotIn('synthetic-signing',path)
            response.read.assert_called_once_with(CONFIG.max_response_bytes+1);client.close.assert_called_once()

    def test_transport_never_follows_redirects_and_redacts_errors(self):
        for status in (301,302,307,403,500):
            client=MagicMock();client.getresponse.return_value.status=status
            with patch('packages.artifact_sync.provider._PinnedHTTPS',return_value=client),self.assertRaisesRegex(ProviderError,'^provider_unavailable$'):
                OBSReadTransport().list_page(CONFIG,credential(),'',NOW)
            self.assertEqual(client.request.call_count,1);client.close.assert_called_once()
        with patch('packages.artifact_sync.provider._PinnedHTTPS',side_effect=RuntimeError('DO_NOT_LEAK')):
            with self.assertRaisesRegex(ProviderError,'^provider_unavailable$'):OBSReadTransport().list_page(CONFIG,credential(),'',NOW)

    def test_transport_content_and_size_bounds(self):
        for content,encoding,raw in [('text/html','identity',b''),('application/xml','gzip',b''),('application/xml','identity',b'x'*(CONFIG.max_response_bytes+1))]:
            client=MagicMock();response=client.getresponse.return_value;response.status=200
            response.getheader.side_effect=lambda k,d='':{'Content-Type':content,'Content-Encoding':encoding}.get(k,d)
            response.read.return_value=raw
            with patch('packages.artifact_sync.provider._PinnedHTTPS',return_value=client),self.assertRaises(ProviderError):
                OBSReadTransport().list_page(CONFIG,credential(),'',NOW)

    def test_dns_rejects_private_mixed_and_pins_public_address(self):
        def answer(ip):return (socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,443))
        for ips in (['127.0.0.1'],['10.1.2.3'],['169.254.169.254'],['93.184.216.34','127.0.0.1'],[]):
            with patch('packages.artifact_sync.provider.socket.getaddrinfo',return_value=[answer(i) for i in ips]),patch('packages.artifact_sync.provider.socket.socket') as sock:
                with self.assertRaises(OSError):_PinnedHTTPS(CONFIG.host).connect()
                sock.assert_not_called()
        context=MagicMock();raw=MagicMock()
        with patch('packages.artifact_sync.provider.socket.getaddrinfo',return_value=[answer('93.184.216.34')]) as dns,patch('packages.artifact_sync.provider.socket.socket',return_value=raw):
            _PinnedHTTPS(CONFIG.host,context=context).connect()
            self.assertEqual(dns.call_count,1);raw.connect.assert_called_once_with(('93.184.216.34',443))
            context.wrap_socket.assert_called_once_with(raw,server_hostname=CONFIG.host)


if __name__ == '__main__':unittest.main()
