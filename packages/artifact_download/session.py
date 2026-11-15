# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Session-only binary composition. No legacy bearer adapter or token conversion."""
from dataclasses import dataclass, field
import re

from packages.artifacts import Scope
from packages.artifact_sync import ProviderError
from packages.session_api import SessionWorkspaceApplication
from packages.workspace_api import WorkspaceApplication
from packages.workspaces import PolicyError
from .service import ArtifactDownloader

_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')


@dataclass(frozen=True, slots=True)
class ArtifactDownloadConfiguration:
    """Trusted host dependencies only; never constructed from request fields."""
    credential_loader: object = field(repr=False)
    transport: object = field(default=None, repr=False)

    def __post_init__(self):
        if not callable(self.credential_loader):
            raise ValueError('explicit scoped credential loader required')
        if self.transport is not None and not callable(getattr(self.transport, 'open', None)):
            raise ValueError('explicit object transport required')


class ArtifactSessionDownloadAPI(WorkspaceApplication):
    def __init__(self, *, boundary, downloader):
        if (type(boundary) is not SessionWorkspaceApplication or type(downloader) is not ArtifactDownloader
                or downloader._service._session_boundary is not boundary
                or downloader._lease_store is not boundary._sessions.policy):
            raise ValueError('same exact session and policy authority required')
        self._boundary, self._downloader = boundary, downloader

    def __call__(self, environ, start_response):
        stream = None
        try:
            assertion = self._boundary._verify_request(environ)
            scope, action = self._route(environ)
            if any(k in environ for k in ('HTTP_RANGE', 'HTTP_IF_RANGE', 'HTTP_TRANSFER_ENCODING')):
                raise PolicyError(400, 'unsupported_framing')
            body = self._body(environ, {'key'} if action == 'availability' else {'key', 'expected_sha256'})
            revision = self._revision(environ)
            if action == 'availability':
                result = self._downloader._availability_verified(assertion, scope, body['key'], expected_revision=revision)
                return self._boundary._respond(environ, start_response, dispatch=lambda _: result)
            stream = self._downloader._prepare_verified(assertion, scope, body['key'], expected_revision=revision, expected_sha256=body['expected_sha256'])
        except ProviderError:
            return self._failure(environ, start_response, PolicyError(502, 'provider_unavailable'))
        except Exception as error:
            return self._failure(environ, start_response, error)
        try:
            headers = [(name, value) for name, value in stream.headers
                       if name not in {'Vary', 'Content-Security-Policy'}]
            headers += [('Vary', 'Cookie, Origin'), ('Pragma', 'no-cache'),
                        ('Content-Security-Policy', "default-src 'none'; sandbox; frame-ancestors 'none'")]
            start_response('200 OK', headers)
            return stream
        except BaseException:
            stream.close()
            raise

    def _failure(self, environ, start_response, error):
        def fail(_):
            raise error
        return self._boundary._respond(environ, start_response, dispatch=fail)

    @staticmethod
    def _route(environ):
        path = environ.get('PATH_INFO', '')
        if type(path) is not str or len(path) > 1000 or environ.get('QUERY_STRING', '') or environ.get('REQUEST_METHOD') != 'POST':
            raise PolicyError(404, 'not_found')
        parts = path.split('/')
        if (len(parts) != 8 or parts[:3] != ['', 'api', 'workspaces'] or parts[4] != 'artifact-download'
                or parts[7] not in {'availability', 'download'} or any(not _ID.fullmatch(v) for v in parts[3:])):
            raise PolicyError(404, 'not_found')
        return Scope(parts[3], parts[5], parts[6]), parts[7]
