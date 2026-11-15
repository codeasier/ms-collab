# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Fixed OBS object GET. No client URLs, redirects, ranges or presigned links."""
from base64 import b64encode
from email.utils import format_datetime
import hashlib
import hmac
import ssl
import socket
import threading
import time
from urllib.parse import quote

from packages.artifact_sync.provider import _bound, _now, _PinnedHTTPS, ProviderError
from packages.artifacts.catalog import instant
from .records import ContentBinding


def request_parts(connection, credential, binding, now):
    _bound(connection, credential)
    if type(binding) is not ContentBinding or not binding.key.startswith(connection.prefix):
        raise ProviderError('invalid_response')
    path = '/' + quote(binding.key, safe='/~')
    date = format_datetime(_now(lambda: now), usegmt=True)
    extra = '' if credential.security_token is None else f'x-obs-security-token:{credential.security_token}\n'
    canonical = f'GET\n\n\n{date}\n{extra}/{connection.bucket}{path}'
    signature = b64encode(hmac.new(credential.secret_key.encode('ascii'), canonical.encode('utf-8'), hashlib.sha1).digest()).decode('ascii')
    headers = {'Date': date, 'Authorization': f'OBS {credential.access_key}:{signature}',
               'Accept-Encoding': 'identity', 'If-Match': binding.etag}
    if credential.security_token is not None:
        headers['x-obs-security-token'] = credential.security_token
    return path, headers


class _ObjectResponse:
    def __init__(self, client, response, timer):
        self._client, self._response, self._timer = client, response, timer

    def read(self, size):
        try:
            return self._response.read1(size)
        except Exception:
            raise ProviderError('provider_unavailable') from None

    def close(self):
        try:
            self._response.close()
            self._client.close()
        except Exception:
            # Keep the deadline abort armed if synchronous cleanup failed.
            raise ProviderError('provider_unavailable') from None
        self._timer.cancel()


class _TimedHTTPS(_PinnedHTTPS):
    def __init__(self, *args, deadline, **kwargs):
        super().__init__(*args, **kwargs)
        self.deadline = deadline
        self._transport_socket = None

    def connect(self):
        super().connect()
        self._transport_socket = self.sock
        # getaddrinfo cannot be force-cancelled by Python. A late resolver/TLS
        # completion must never send the signed HTTP request after the deadline.
        if time.monotonic() >= self.deadline:
            self.close()
            raise TimeoutError()

    def abort(self):
        # HTTPConnection may detach self.sock for Connection: close while
        # HTTPResponse still owns a makefile reference. Keep the original socket.
        sock = self._transport_socket or self.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()


class OBSObjectTransport:
    """Host-computed public endpoint, direct pinned socket, verified TLS hostname."""
    def open(self, connection, credential, binding, now, *, remaining):
        path, headers = request_parts(connection, credential, binding, now)
        if type(remaining) not in (float, int) or not 0 < remaining <= 30:
            raise ProviderError('provider_unavailable')
        client, timer, response = None, None, None
        try:
            client = _TimedHTTPS(connection.host, timeout=min(connection.timeout, 5, remaining),
                                 context=ssl.create_default_context(), deadline=time.monotonic() + remaining)
            timer = threading.Timer(remaining, client.abort)
            timer.daemon = True
            timer.start()
            client.request('GET', path, headers=headers)
            response = client.getresponse()
            if response.status != 200:
                raise ProviderError('provider_unavailable')
            fields = {}
            for name, value in response.getheaders():
                fields.setdefault(name.lower(), []).append(value)
            expected = {'content-length': str(binding.size), 'etag': binding.etag,
                        'last-modified': format_datetime(instant(binding.modified_at), usegmt=True)}
            if (any(fields.get(k) != [v] for k, v in expected.items())
                    or fields.get('content-encoding', ['identity']) != ['identity']
                    or 'transfer-encoding' in fields or 'content-range' in fields):
                raise ProviderError('invalid_response')
            return _ObjectResponse(client, response, timer)
        except Exception:
            if timer is not None:
                timer.cancel()
            if response is not None:
                response.close()
            if client is not None:
                client.close()
            raise ProviderError('provider_unavailable') from None
