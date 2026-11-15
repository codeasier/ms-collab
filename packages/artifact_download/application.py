# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Same-origin authenticated POST-to-binary adapter, no token or URL in a link."""
import re
from urllib.parse import urlsplit

from packages.artifacts import Scope
from packages.artifact_sync import ProviderError
from packages.workspace_api import WorkspaceApplication
from packages.workspaces import PolicyError
from .service import ArtifactDownloader

_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')


class _Failure(WorkspaceApplication):
    def __init__(self, error):
        self.error = error

    def _dispatch(self, environ):
        raise self.error


class ArtifactDownloadAPI(WorkspaceApplication):
    def __init__(self, *, downloader, origin):
        if type(downloader) is not ArtifactDownloader:
            raise ValueError('configured downloader required')
        try:
            parsed = urlsplit(origin)
            port = parsed.port
        except (ValueError, TypeError):
            raise ValueError('explicit origin required') from None
        if (type(origin) is not str or parsed.username or parsed.password or parsed.path or parsed.query
                or parsed.fragment or not parsed.hostname or any(c.isspace() for c in origin)
                or origin != f'{parsed.scheme}://{parsed.netloc}'
                or not (parsed.scheme == 'https' or (parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and port))):
            raise ValueError('HTTPS origin required; numeric loopback HTTP only for tests')
        self._downloader = downloader
        self._origin, self._host, self._scheme = origin, parsed.netloc, parsed.scheme

    def __call__(self, environ, start_response):
        stream = None
        try:
            if (environ.get('HTTP_HOST') != self._host or environ.get('wsgi.url_scheme') != self._scheme
                    or environ.get('HTTP_ORIGIN', self._origin) != self._origin
                    or environ.get('HTTP_SEC_FETCH_SITE') == 'cross-site'):
                raise PolicyError(403, 'same_origin_required')
            assertion = self._downloader._service._verify_request(environ)
            scope = self._route(environ)
            if any(k in environ for k in ('HTTP_RANGE', 'HTTP_IF_RANGE', 'HTTP_TRANSFER_ENCODING')):
                raise PolicyError(400, 'unsupported_framing')
            body = self._body(environ, {'key'})
            revision = self._revision(environ)
            stream = self._downloader._prepare_verified(assertion, scope, body['key'], expected_revision=revision)
        except ProviderError:
            return _Failure(PolicyError(502, 'provider_unavailable'))(environ, start_response)
        except Exception as error:
            return _Failure(error)(environ, start_response)
        try:
            start_response('200 OK', stream.headers)
            return stream
        except BaseException:
            stream.close()
            raise

    @staticmethod
    def _route(environ):
        path = environ.get('PATH_INFO', '')
        if type(path) is not str or len(path) > 1000 or environ.get('QUERY_STRING', '') or environ.get('REQUEST_METHOD') != 'POST':
            raise PolicyError(404, 'not_found')
        parts = path.split('/')
        if (len(parts) != 8 or parts[:3] != ['', 'api', 'workspaces'] or parts[4] != 'artifact-download'
                or parts[7] != 'download' or any(not _ID.fullmatch(v) for v in parts[3:])):
            raise PolicyError(404, 'not_found')
        return Scope(parts[3], parts[5], parts[6])
