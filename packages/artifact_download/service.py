# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Content-bound acquisition followed by continuously authorized bounded output."""
from hashlib import sha256
import hmac
import re
import tempfile
import time
from uuid import uuid4

from packages.artifact_sync import ArtifactSynchronizer, ProviderError
from packages.artifact_sync.provider import _bound, _now, object_key
from packages.artifact_sync.records import authorized, decode_snapshot, scope_key
from packages.artifacts.catalog import instant
from packages.workspaces import PolicyError, StorageError, SQLiteWorkspaceService
from . import records
from .provider import OBSObjectTransport


class DownloadInterrupted(RuntimeError):
    """After binary headers, abort the stream. Never append a JSON error body."""
    def __init__(self):
        super().__init__('download_interrupted')


class ArtifactDownloader:
    def __init__(self, *, service, transport=None, monotonic=time.monotonic):
        if type(service) is not ArtifactSynchronizer or not callable(monotonic):
            raise ValueError('configured synchronizer and monotonic clock required')
        self._service = service
        # Derive cleanup authority from this exact configured identity boundary.
        # A revoked user must not block cleanup of its already admitted lease;
        # no independent store/actor can be supplied by a host or HTTP caller.
        boundary = service._session_boundary
        self._lease_store = boundary._sessions.policy if boundary is not None else service._store
        if type(self._lease_store) is not SQLiteWorkspaceService:
            raise ValueError('configured lease policy store required')
        self._transport = OBSObjectTransport() if transport is None else transport
        self._monotonic = monotonic

    def _current(self, assertion, scope, key, revision, *, pinned=None, lease=None):
        def operation(policy, actor, connection):
            index = authorized(policy, actor, connection, scope, 'read')
            # Archived spaces retain local metadata reads, not fresh external bytes.
            if policy._workspaces[scope.workspace_id].state != 'active':
                raise PolicyError(404, 'not_found')
            grant, raw = records.load(connection, scope)
            if actor not in grant.downloaders or index.published is None:
                raise PolicyError(404, 'not_found')
            binding = next((v for v in grant.bindings if v.key == key), None)
            row = next((r for r in decode_snapshot(index.published, index.policy.connection).artifacts if r.key == key), None)
            if (binding is None or row is None or row.is_dir or row.size != binding.size
                    or instant(row.modified_at) != instant(binding.modified_at)):
                raise PolicyError(404, 'not_found')
            policy._match(revision, index.published_revision)
            value = (actor, index.policy_raw, index.published, index.published_revision, raw, policy._realm, policy._principals[actor].generation)
            if pinned is not None and value != pinned:
                raise PolicyError(412, 'download_changed')
            if lease is not None:
                records.check_schema(connection)
                state = connection.execute(f'SELECT actor,workspace,connection,bucket,published_revision,size,expires,state FROM {records._LEASES} WHERE id=?', (lease[0],)).fetchone()
                expected = (actor, *scope_key(scope), revision, binding.size, lease[1], 'active')
                if state != expected or _now(self._service._clock).timestamp() >= lease[1]:
                    raise PolicyError(409, 'download_expired')
            return value, binding, index
        return operation

    def _availability_verified(self, assertion, scope, key, *, expected_revision):
        """Current exact byte binding only. No lease, provider or credential I/O."""
        try:
            object_key(key)
        except (ValueError, TypeError, UnicodeError):
            raise PolicyError(400, 'invalid_request') from None
        _, binding, _ = self._service._authenticated_transaction(assertion,
            self._current(assertion, scope, key, expected_revision), with_connection=True)
        return {'scope': scope.as_dict(), 'key': key, 'size': binding.size,
                'sha256': binding.sha256, 'published_revision': expected_revision}

    def _prepare_verified(self, assertion, scope, key, *, expected_revision, expected_sha256=None):
        # Trusted adapter seam only: assertion must come from the configured
        # verifier, never a decoded client field or self-reported principal.
        try:
            object_key(key)
        except (ValueError, TypeError, UnicodeError):
            raise PolicyError(400, 'invalid_request') from None
        if expected_sha256 is not None and (type(expected_sha256) is not str
                or re.fullmatch(r'[0-9a-f]{64}', expected_sha256) is None):
            raise PolicyError(400, 'invalid_request')
        ident = str(uuid4())
        deadline = self._monotonic() + records.TTL_SECONDS
        def admit(policy, actor, connection):
            pinned, binding, index = self._current(assertion, scope, key, expected_revision)(policy, actor, connection)
            if expected_sha256 is not None and not hmac.compare_digest(expected_sha256, binding.sha256):
                # A client constraint is never byte authority. Bind the exact
                # confirmed bytes before leases, credentials or provider work.
                raise PolicyError(412, 'download_changed')
            now = _now(self._service._clock).timestamp()
            # Shared persistent admission, not a per-process semaphore. Expiry
            # revokes output but MUST NOT release capacity while an old blocked
            # worker may still own a socket/spool. Crash recovery is host-only.
            active = connection.execute(f'SELECT actor FROM {records._LEASES} WHERE state=?', ('active',)).fetchall()
            if len(active) >= 2 or any(v[0] == actor for v in active):
                raise PolicyError(429, 'download_capacity')
            expires = now + records.TTL_SECONDS
            connection.execute(f'INSERT INTO {records._LEASES} VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (ident, actor, *scope_key(scope), sha256(key.encode('utf-8')).hexdigest(), expected_revision,
                 binding.size, now, expires, 'active', 0))
            return pinned, binding, index, expires
        pinned, binding, index, expires = self._service._authenticated_transaction(assertion, admit, write=True, with_connection=True)
        def current():
            if self._monotonic() >= deadline:
                raise PolicyError(409, 'download_expired')
            result = self._service._authenticated_transaction(assertion,
                self._current(assertion, scope, key, expected_revision, pinned=pinned, lease=(ident, expires)), with_connection=True)
            if self._monotonic() >= deadline:
                raise PolicyError(409, 'download_expired')
            return result
        def finish(state, offered):
            # Trusted cleanup of THIS admitted lease only, even after user revoke.
            # Not an authorization route and does not change any user grant.
            def operation(policy, connection):
                records.check_schema(connection)
                changed = connection.execute(f'UPDATE {records._LEASES} SET state=?,offered=? WHERE id=? AND actor=? AND state=?',
                    (state, offered, ident, pinned[0], 'active')).rowcount
                if changed != 1:
                    raise StorageError('download_lease_changed')
            self._lease_store._transaction(operation, write=True, with_connection=True)
        spool = None
        upstream = None
        try:
            current()
            try:
                credential = self._service._credential_loader(index.policy.credential_ref, scope, index.policy.connection.origin)
                _bound(index.policy.connection, credential)
            except Exception:
                raise ProviderError('credential_unavailable') from None
            current()
            upstream = self._transport.open(index.policy.connection, credential, binding, _now(self._service._clock),
                                            remaining=max(0.0, deadline - self._monotonic()))
            spool = tempfile.TemporaryFile(mode='w+b')  # anonymous, 0600, never a served path
            size, checksum = 0, sha256()
            while True:
                current()
                chunk = upstream.read(min(records.CHUNK_BYTES, binding.size - size + 1))
                current()
                if type(chunk) is not bytes or len(chunk) > min(records.CHUNK_BYTES, binding.size - size + 1):
                    raise ProviderError('invalid_response')
                if not chunk:
                    break
                size += len(chunk)
                if size > binding.size:
                    raise ProviderError('invalid_response')
                checksum.update(chunk)
                spool.write(chunk)
            if size != binding.size or not hmac.compare_digest(checksum.hexdigest(), binding.sha256):
                raise ProviderError('invalid_response')
            upstream.close()
            upstream = None
            current()
            spool.seek(0)
            return DownloadStream(spool, binding, current, finish)
        except BaseException:
            if spool is not None:
                spool.close()
            released = upstream is None
            if upstream is not None:
                try:
                    upstream.close()
                    released = True
                except Exception:
                    pass
            if released:
                finish('failed', 0)
            # Failed upstream cleanup retains the active reservation fail-closed.
            raise


class DownloadStream:
    """WSGI iterable with close() cleanup even if never iterated by the server."""
    def __init__(self, spool, binding, current, finish):
        self._spool, self.binding, self._current, self._finish = spool, binding, current, finish
        self._closed, self._offered = False, 0

    @property
    def headers(self):
        name = re.sub(r'[^A-Za-z0-9._-]', '_', self.binding.key.rsplit('/', 1)[-1])[:80]
        return [('Content-Type', 'application/octet-stream'), ('Content-Length', str(self.binding.size)),
                ('Content-Disposition', f'attachment; filename="artifact-{name}"'),
                ('X-Artifact-SHA256', self.binding.sha256), ('Cache-Control', 'no-store'),
                ('X-Content-Type-Options', 'nosniff'), ('Vary', 'Authorization'),
                ('Content-Security-Policy', "default-src 'none'; sandbox"), ('Referrer-Policy', 'no-referrer')]

    def __iter__(self):
        return self

    def __next__(self):
        if self._closed:
            raise StopIteration
        try:
            self._current()
            if self._offered == self.binding.size:
                self._spool.close()
                self._finish('complete', self._offered)
                self._closed = True
                raise StopIteration
            chunk = self._spool.read(min(records.CHUNK_BYTES, self.binding.size - self._offered))
            if not chunk:
                raise DownloadInterrupted()
            self._current()
            self._offered += len(chunk)
            return chunk
        except StopIteration:
            raise
        except BaseException:
            self.close()
            raise DownloadInterrupted() from None

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                self._spool.close()
            except Exception:
                return  # Retain the reservation if resource release is uncertain.
            try:
                self._finish('interrupted', self._offered)
            except Exception:
                # Never emit/append raw database failures to binary output. The
                # active reservation remains fail-closed until host recovery.
                pass
