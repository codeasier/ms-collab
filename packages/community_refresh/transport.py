# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Fixed GitCode GETs with byte, whole-page time and resource ownership bounds."""
import http.client
import ipaddress
import json
import math
import socket
import ssl
import threading
import time
from urllib.parse import parse_qs, urlsplit

from packages.community_sync import BoundCredential, Connection, ProviderError
from packages.community_sync.provider import _unique_object

_HOST = 'api.gitcode.com'


class RefreshCleanupError(ProviderError):
    """Worker/resource termination is unknown. Its durable lease must stay active."""
    def __init__(self):
        super().__init__('provider_unavailable')


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError('nonfinite_json_number')
    return value


def _request(path, credential, connection):
    if type(connection) is not Connection or type(credential) is not BoundCredential or credential.scope != connection.scope:
        raise ProviderError('request_scope_mismatch')
    try:
        if type(path) is not str or not path.isascii() or len(path) > 1000:
            raise ValueError()
        parsed = urlsplit(path)
        query = parse_qs(parsed.query, strict_parsing=True, max_num_fields=5)
        prefix = f'/api/v5/repos/{connection.scope.repository}/'
        if (parsed.scheme or parsed.netloc or parsed.fragment
                or parsed.path not in (prefix + 'issues', prefix + 'pulls')
                or path != parsed.path + '?' + parsed.query
                or set(query) != {'state', 'sort', 'direction', 'page', 'per_page'}
                or any(len(v) != 1 for v in query.values())
                or query['state'] != ['all'] or query['sort'] != ['updated']
                or query['direction'] != ['desc'] or query['per_page'] != [str(connection.page_size)]
                or query['page'][0] not in {str(p) for p in range(1, connection.max_pages + 1)}):
            raise ValueError()
    except (ValueError, TypeError):
        raise ProviderError('invalid_request_parameters') from None


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, *, deadline, timeout):
        super().__init__(_HOST, 443, timeout=timeout, context=ssl.create_default_context())
        self.deadline, self._owned = deadline, None

    def guard(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        self.timeout = min(self.timeout, remaining)
        # HTTPResponse may close the last socket file reference at exact EOF.
        # Deadline checks still apply, but a fully closed fd needs no timeout.
        if self._owned is not None and self._owned.fileno() != -1:
            self._owned.settimeout(self.timeout)

    def connect(self):
        self.guard()
        addresses = socket.getaddrinfo(_HOST, 443, type=socket.SOCK_STREAM)
        self.guard()
        if not addresses or len(addresses) > 32:
            raise OSError('invalid DNS')
        for family, kind, proto, canon, address in addresses:
            ip = ipaddress.ip_address(address[0])
            if family not in (socket.AF_INET, socket.AF_INET6) or not ip.is_global or ip.is_reserved or ip.is_multicast:
                raise OSError('nonpublic destination')
        family, kind, proto, canon, address = addresses[0]
        # The worker owns raw TCP before connect, and TLS before handshake.
        # No second thread closes or detaches a resource while ownership changes.
        self._owned = socket.socket(family, kind, proto)
        self.guard(); self._owned.connect(address); self.guard()
        self._owned = self._context.wrap_socket(self._owned, server_hostname=_HOST, do_handshake_on_connect=False)
        self.sock = self._owned
        self.guard(); self.sock.do_handshake(); self.guard()

    def close(self):
        failed = False
        try:
            super().close()
        except BaseException:
            failed = True
        if self._owned is not None:
            try:
                self._owned.close()
            except BaseException:
                failed = True
        if failed:
            raise RefreshCleanupError()


class BoundedGitCodeReadTransport:
    """Trusted `get(..., remaining=seconds)` returns decoded JSON only after cleanup.

    A single daemon worker isolates DNS and OS I/O that Python cannot hard-cancel.
    If it has not completed cleanup by the deadline, raise RefreshCleanupError;
    never release or reuse its durable admission slot, even if it finishes later.
    No retries, redirects, proxies, compression or alternate destination.
    """
    def get(self, path, credential, connection, *, remaining):
        _request(path, credential, connection)
        if type(remaining) not in (int, float) or not math.isfinite(remaining) or not 0 < remaining <= 30:
            raise ProviderError('invalid_deadline')
        deadline = time.monotonic() + remaining
        finished, outcome = threading.Event(), []
        def worker():
            client, response, value, error = None, None, None, None
            try:
                client = _PinnedHTTPS(deadline=deadline, timeout=min(5, connection.timeout, remaining))
                client.guard()
                client.request('GET', path, headers={'Authorization': 'Bearer ' + credential.token,
                    'Accept': 'application/json', 'Accept-Encoding': 'identity', 'Connection': 'close'})
                client.guard(); response = client.getresponse(); client.guard()
                fields = {}
                for name, val in response.getheaders():
                    fields.setdefault(name.lower(), []).append(val)
                sizes = fields.get('content-length', [])
                if (response.status != 200 or len(sizes) != 1 or not sizes[0].isascii()
                        or not sizes[0].isdigit() or len(sizes[0]) > 7 or int(sizes[0]) > connection.max_response_bytes
                        or len(fields.get('content-type', [])) != 1
                        or fields['content-type'][0].split(';', 1)[0].strip().lower() != 'application/json'
                        or fields.get('content-encoding', ['identity']) != ['identity']
                        or 'transfer-encoding' in fields or 'content-range' in fields):
                    raise ProviderError('invalid_response')
                size, raw = int(sizes[0]), bytearray()
                while True:
                    client.guard(); chunk = response.read1(min(65536, size - len(raw) + 1)); client.guard()
                    if type(chunk) is not bytes or len(raw) + len(chunk) > size:
                        raise ProviderError('invalid_response')
                    if not chunk:
                        break
                    raw.extend(chunk)
                if len(raw) != size:
                    raise ProviderError('invalid_response')
                value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object,
                                   parse_float=_finite_float,
                                   parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                client.guard()
            except BaseException:
                error = ProviderError('provider_unavailable')
            finally:
                for resource in (response, client):
                    if resource is not None:
                        try:
                            resource.close()
                        except BaseException:
                            error = RefreshCleanupError()
                # Publish completion ONLY after all owned cleanup has returned.
                outcome.append((value, error)); finished.set()
        thread = threading.Thread(target=worker, name='community-refresh-get', daemon=True)
        try:
            thread.start()
            completed = finished.wait(max(0, deadline - time.monotonic()))
        except BaseException:
            # Even an interrupted start/wait is not proof that no worker owns I/O.
            raise RefreshCleanupError() from None
        if not completed:
            raise RefreshCleanupError()
        value, error = outcome[0]
        if error is not None:
            raise error
        if time.monotonic() >= deadline:
            raise ProviderError('provider_unavailable')
        return value
