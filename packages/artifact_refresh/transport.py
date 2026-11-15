# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Deadline-bound OBS listing only; no object bytes or client-selected URL."""
import math
import ipaddress
import socket
import ssl
import threading
import time
from urllib.parse import urlencode

from packages.artifact_sync.provider import _PinnedHTTPS, _bound, list_headers, object_key, ProviderError


class RefreshCleanupError(ProviderError):
    """The trusted host must confirm resource release before recovering capacity."""
    def __init__(self):
        super().__init__('provider_unavailable')


class _TimedHTTPS(_PinnedHTTPS):
    def __init__(self, *args, deadline, **kwargs):
        super().__init__(*args, **kwargs)
        self.deadline, self._transport_socket = deadline, None
        self.cleanup_uncertain = False
        self._ownership = threading.Lock()

    def connect(self):
        # Keep the same public-only DNS/pinned TLS contract, but own the socket
        # before TCP or TLS I/O, not only after HTTPSConnection.connect returns.
        addresses = socket.getaddrinfo(self.host, 443, type=socket.SOCK_STREAM)
        if not addresses or len(addresses) > 32:
            raise OSError('invalid DNS')
        for family, kind, proto, canon, address in addresses:
            ip = ipaddress.ip_address(address[0])
            if family not in (socket.AF_INET, socket.AF_INET6) or not ip.is_global or ip.is_reserved or ip.is_multicast:
                raise OSError('nonpublic destination')
        family, kind, proto, canon, address = addresses[0]
        with self._ownership:
            raw = socket.socket(family, kind, proto)
            self._transport_socket = raw
        try:
            if time.monotonic() >= self.deadline:
                raise TimeoutError()
            raw.settimeout(self.timeout)
            raw.connect(address)
            # The swap must not race an abort between detach and ownership.
            # TLS handshake itself runs after ownership is registered.
            with self._ownership:
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host, do_handshake_on_connect=False)
                self._transport_socket = self.sock
            if time.monotonic() >= self.deadline:
                raise TimeoutError()
            self.sock.do_handshake()
            if time.monotonic() >= self.deadline:
                raise TimeoutError()
        except BaseException:
            self.close()
            raise

    def abort(self):
        with self._ownership:
            sock = self._transport_socket
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except BaseException:
                    self.cleanup_uncertain = True
                try:
                    sock.close()
                except BaseException:
                    self.cleanup_uncertain = True

    def close(self):
        with self._ownership:
            failed = False
            try:
                super().close()
            except BaseException:
                failed = True
            if self._transport_socket is not None:
                try:
                    self._transport_socket.close()
                except BaseException:
                    failed = True
            self.cleanup_uncertain = self.cleanup_uncertain or failed
            if failed:
                raise RefreshCleanupError()


class BoundedOBSReadTransport:
    def list_page(self, connection, credential, marker, now, *, remaining):
        _bound(connection, credential)
        if type(remaining) not in (int, float) or not math.isfinite(remaining) or not 0 < remaining <= 30:
            raise ProviderError('provider_unavailable')
        if marker:
            try:
                object_key(marker)
            except (ValueError, TypeError, UnicodeError):
                raise ProviderError('invalid_marker') from None
            if not marker.startswith(connection.prefix):
                raise ProviderError('invalid_marker')
        query = {'prefix': connection.prefix, 'max-keys': str(connection.page_size), 'encoding-type': 'url'}
        if marker:
            query['marker'] = marker
        deadline = time.monotonic() + remaining
        client, response, timer = None, None, None
        try:
            client = _TimedHTTPS(connection.host, timeout=min(5, connection.timeout, remaining),
                context=ssl.create_default_context(), deadline=deadline)
            timer = threading.Timer(remaining, client.abort)
            timer.daemon = True
            timer.start()
            client.request('GET', '/?' + urlencode(query), headers=list_headers(connection, credential, now))
            response = client.getresponse()
            fields = {}
            for name, value in response.getheaders():
                fields.setdefault(name.lower(), []).append(value)
            lengths = fields.get('content-length', [])
            if (response.status != 200 or len(lengths) != 1 or not lengths[0].isascii()
                    or not lengths[0].isdigit() or len(lengths[0]) > 7
                    or int(lengths[0]) > connection.max_response_bytes
                    or len(fields.get('content-type', [])) != 1
                    or fields['content-type'][0].split(';', 1)[0].strip().lower() not in ('application/xml', 'text/xml')
                    or fields.get('content-encoding', ['identity']) != ['identity']
                    or 'transfer-encoding' in fields or 'content-range' in fields):
                raise ProviderError('invalid_response')
            size = int(lengths[0]); raw = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise ProviderError('provider_unavailable')
                chunk = response.read1(min(65536, size - len(raw) + 1))
                if time.monotonic() >= deadline:
                    raise ProviderError('provider_unavailable')
                if type(chunk) is not bytes or len(raw) + len(chunk) > size:
                    raise ProviderError('invalid_response')
                if not chunk:
                    break
                raw.extend(chunk)
            if len(raw) != size:
                raise ProviderError('invalid_response')
            return bytes(raw)
        except ProviderError:
            raise
        except Exception:
            raise ProviderError('provider_unavailable') from None
        finally:
            # Join the resource-owning abort thread before declaring cleanup.
            # Cancellation alone cannot prove an already-started abort finished.
            failed = False
            if timer is not None:
                try:
                    timer.cancel()
                    timer.join()
                except BaseException:
                    failed = True
            failed = failed or bool(client is not None and client.cleanup_uncertain)
            for resource in (response, client):
                if resource is not None:
                    try:
                        resource.close()
                    except BaseException:
                        failed = True
            if failed:
                raise RefreshCleanupError() from None
