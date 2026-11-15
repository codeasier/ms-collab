"""Synthetic inputs only. No account, live endpoint or production metadata."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

from packages.artifacts import Definition, SavedView, Scope
from packages.artifacts_api import view_resource
from packages.artifact_sync import BoundCredential, Connection, IndexPolicy
from packages.identity import AuthenticationError, Credential, Provider, VerifiedIdentity
from packages.workspaces import Membership, ModuleInstance, Principal, SQLiteWorkspaceService, Workspace

NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
ISSUER = 'https://identity.example.invalid'
SCOPE = Scope('alpha', 'source', 'bucket')
CONFIG = Connection(SCOPE, 'synthetic-bucket', 'eu-west-101', prefix='builds/', page_size=2, max_pages=3)
POLICY = IndexPolicy(CONFIG, 'synthetic-secret-ref', ('reader', 'admin'), ('admin',), ('approver',))
BASE = '/api/workspaces/alpha/artifacts/source/bucket'


class Verifier:
    hook = staticmethod(lambda: None)
    def verify(self, credential):
        self.hook()
        if credential.value not in {'synthetic-'+v for v in ('admin', 'approver', 'reader', 'developer', 'other')}:
            raise AuthenticationError()
        return VerifiedIdentity(ISSUER, credential.value[10:], 'synthetic-index', NOW, NOW + timedelta(minutes=5))


def provider(verifier):
    return Provider('test', ISSUER, 'synthetic-index', verifier)


def credential(scope=SCOPE, origin=CONFIG.origin):
    # Deliberately synthetic HMAC inputs. These are not working cloud secrets.
    return BoundCredential(scope, origin, 'SYNTHETICACCESSKEY', 'synthetic-signing-material-not-a-real-key')


def user(name='admin'):
    return Credential('synthetic-'+name)


def initialize(path):
    names = ('admin', 'approver', 'reader', 'developer', 'other')
    scopes = ('alpha', 'beta', 'commons')
    return SQLiteWorkspaceService.initialize(Path(path),
        principals=tuple(Principal(n, ISSUER, n) for n in names),
        workspaces=tuple(Workspace(w, w, 'shared' if w == 'commons' else 'product') for w in scopes),
        memberships=tuple(Membership(w, n, 'admin' if n in ('admin', 'approver') else 'developer' if n == 'developer' else 'viewer')
                          for w in scopes for n in names if n != 'other' and (w != 'beta' or n == 'admin')),
        modules=tuple(ModuleInstance(w, 'artifacts', ('read', 'write')) for w in scopes),
        resources=(view_resource(SavedView(SCOPE, 'files', 'Files', Definition(files_only=True))),),
        operators=frozenset({'admin'}))


def page(rows=(), *, config=CONFIG, marker='', truncated=False, next_marker='', **overrides):
    values = {'Name': config.bucket, 'Prefix': quote(config.prefix, safe=''), 'Marker': quote(marker, safe=''),
              'MaxKeys': str(config.page_size), 'EncodingType': 'url', 'IsTruncated': str(truncated).lower()}
    values.update(overrides)
    fields = ''.join(f'<{k}>{escape(v)}</{k}>' for k, v in values.items())
    if next_marker:
        fields += '<NextMarker>'+escape(quote(next_marker, safe=''))+'</NextMarker>'
    for row in rows:
        key, timestamp, size = row
        fields += '<Contents><Key>'+escape(quote(key, safe=''))+'</Key><LastModified>'+escape(timestamp)+'</LastModified><Size>'+str(size)+'</Size><ETag>discard-me</ETag><Owner><ID>discard-owner</ID></Owner></Contents>'
    return ('<?xml version="1.0" encoding="UTF-8"?><ListBucketResult xmlns="http://obs.myhuaweicloud.com/doc/2015-06-30/">'+fields+'</ListBucketResult>').encode()


class Transport:
    def __init__(self, pages=None):
        self.pages = list(pages or [page((('builds/one.txt', '2026-01-01T23:00:00Z', 12),))])
        self.calls = []
        self.hook = lambda: None
    def list_page(self, config, secret, marker, now):
        self.calls.append((config.scope, marker))
        self.hook()
        return self.pages[len(self.calls)-1]


def call(app, suffix='/quarters', *, token='reader', method='GET', body=None, base=BASE, query='', extra=None):
    raw = json.dumps(body).encode() if body is not None else b''
    environ = {'PATH_INFO': base+suffix, 'QUERY_STRING': query, 'REQUEST_METHOD': method,
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw)}
    if token is not None:
        environ['HTTP_AUTHORIZATION'] = 'Bearer synthetic-'+token
    environ.update(extra or {})
    result = {}
    def start(status, headers):
        result.update(status=int(status.split()[0]), headers=dict(headers))
    result['body'] = json.loads(b''.join(app(environ, start)))
    return result
