# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Narrow GET-only openLiBing-adapter protocol, never credential discovery."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import http.client
import ipaddress
import json
import re
import socket
import ssl
from urllib.parse import urlsplit

from packages.pipeline_api import InvalidRecord, encode_snapshots
from packages.pipelines import PipelineScope, normalize_status

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_HOST = re.compile(r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z")


class ProviderError(RuntimeError):
    """Value-free failure; never includes URLs, tokens or upstream messages."""


def valid_origin(origin):
    if type(origin) is not str:
        raise ValueError("invalid origin")
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or parsed.netloc != parsed.hostname or parsed.path
            or parsed.query or parsed.fragment or origin != "https://" + (parsed.hostname or "")
            or not _HOST.fullmatch(parsed.hostname or "")):
        raise ValueError("invalid origin")
    return parsed.hostname


@dataclass(frozen=True, slots=True)
class Connection:
    scope: PipelineScope
    origin: str
    run_limit: int = 5
    timeout: int = 10
    max_response_bytes: int = 262144

    def __post_init__(self):
        if type(self.scope) is not PipelineScope:
            raise ValueError("invalid scope")
        valid_origin(self.origin)
        for value, low, high in ((self.run_limit, 1, 20), (self.timeout, 1, 15),
                                 (self.max_response_bytes, 256, 524288)):
            if type(value) is not int or not low <= value <= high:
                raise ValueError("invalid limit")


@dataclass(frozen=True, slots=True)
class BoundCredential:
    scope: PipelineScope
    origin: str
    token: str = field(repr=False, compare=False)

    def __post_init__(self):
        if type(self.scope) is not PipelineScope:
            raise ValueError("invalid scope")
        valid_origin(self.origin)
        if (type(self.token) is not str or not 32 <= len(self.token) <= 4096
                or any(not 33 <= ord(c) <= 126 for c in self.token)):
            raise ValueError("invalid credential")


def _bound(connection, credential):
    if (type(connection) is not Connection or type(credential) is not BoundCredential
            or connection.scope != credential.scope or connection.origin != credential.origin):
        raise ProviderError("credential_scope_mismatch")


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


class _PinnedHTTPS(http.client.HTTPSConnection):
    """Resolve once per request, reject non-global addresses, then pin TLS socket."""
    def connect(self):
        addresses = socket.getaddrinfo(self.host, 443, type=socket.SOCK_STREAM)
        if not addresses or len(addresses) > 32:
            raise OSError("invalid DNS response")
        # Reject mixed public/private answers, not merely the selected address.
        for family, socktype, proto, canon, address in addresses:
            ip = ipaddress.ip_address(address[0])
            if (family not in (socket.AF_INET, socket.AF_INET6) or not ip.is_global
                    or ip.is_multicast or ip.is_reserved):
                raise OSError("nonpublic destination")
        family, socktype, proto, canon, address = addresses[0]
        raw = socket.socket(family, socktype, proto)
        try:
            raw.settimeout(self.timeout)
            raw.connect(address)  # No second hostname lookup / DNS rebinding gap.
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


class HTTPSReadTransport:
    """No proxy environment, redirect, cookie, retry, mutation or arbitrary path."""
    def get(self, path, credential, connection):
        _bound(connection, credential)
        prefix = f"/api/v1/pipelines/{connection.scope.pipeline_id}/runs"
        detail = path[len(prefix) + 1:] if type(path) is str and path.startswith(prefix + "/") else ""
        if path != f"{prefix}?limit={connection.run_limit}" and (not _ID.fullmatch(detail) or detail == "latest"):
            raise ProviderError("invalid_request_path")
        client = None
        try:
            client = _PinnedHTTPS(valid_origin(connection.origin), timeout=connection.timeout,
                                  context=ssl.create_default_context())
            client.request("GET", path, headers={"Accept": "application/json", "Accept-Encoding": "identity",
                           "X-Service-Token": credential.token})
            response = client.getresponse()
            if response.status != 200:
                raise ProviderError("provider_unavailable")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise ProviderError("invalid_response")
            if response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise ProviderError("invalid_response")
            raw = response.read(connection.max_response_bytes + 1)
            if len(raw) > connection.max_response_bytes:
                raise ProviderError("response_too_large")
            return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except ProviderError:
            raise
        except Exception:
            raise ProviderError("provider_unavailable") from None
        finally:
            if client is not None:
                client.close()


def _identifier(value):
    if type(value) is not str or _ID.fullmatch(value) is None or value == "latest":
        raise ProviderError("invalid_response")
    return value


def _identity(row, pipeline, run_id=None):
    if type(row) is not dict:
        raise ProviderError("invalid_response")
    identifier = _identifier(row.get("id"))
    if run_id is not None and identifier != run_id:
        raise ProviderError("response_scope_mismatch")
    # OBS tolerated missing pipeline_id. This integration deliberately does not.
    if row.get("pipeline_id") != pipeline or ("pipelineId" in row and row["pipelineId"] != pipeline):
        raise ProviderError("response_scope_mismatch")
    return identifier


def _status(row):
    # Legacy COMPLETED is not enough evidence of success. Only explicit conclusion
    # or known universal states are translated; no private provider guesses.
    return normalize_status(row.get("status"), row.get("conclusion"))


def project_run(scope, payload, run_id, observed_at):
    _identity(payload, scope.pipeline_id, run_id)
    raw_stages = payload.get("stages")
    stages = None
    if raw_stages is not None:
        if type(raw_stages) is not list or len(raw_stages) > 40:
            raise ProviderError("invalid_response")
        stages = []
        for stage in raw_stages:
            if type(stage) is not dict or type(stage.get("jobs")) is not list:
                raise ProviderError("invalid_response")
            jobs = []
            for job in stage["jobs"]:
                if type(job) is not dict:
                    raise ProviderError("invalid_response")
                jobs.append({"id": _identifier(job.get("id")), "name": job.get("name"), "status": _status(job),
                             "started_at": job.get("started_at"), "finished_at": job.get("finished_at")})
            stages.append({"name": stage.get("name"), "jobs": jobs})
    return {**scope.as_dict(), "id": run_id, "observed_at": observed_at, "status": _status(payload), "stages": stages}


def collect(connection, credential, *, before_read, transport=None,
            clock=lambda: datetime.now(timezone.utc)):
    """Collect one bounded subset. Caller must supply authorization before_read.

    No storage, pagination promise or public visibility inference. The callback
    runs before each request; dispatch is not atomic with authority changes.
    A previously checked request may still be sent after revocation.
    """
    _bound(connection, credential)
    transport = transport or HTTPSReadTransport()
    prefix = f"/api/v1/pipelines/{connection.scope.pipeline_id}/runs"
    before_read()
    try:
        page = transport.get(f"{prefix}?limit={connection.run_limit}", credential, connection)
    except ProviderError:
        raise
    except Exception:
        raise ProviderError("provider_unavailable") from None
    if type(page) is not dict or type(page.get("items")) is not list or len(page["items"]) > connection.run_limit:
        raise ProviderError("invalid_response")
    ids = [_identity(item, connection.scope.pipeline_id) for item in page["items"]]
    if len(set(ids)) != len(ids):
        raise ProviderError("duplicate_run")
    rows = []
    for run_id in ids:
        before_read()
        try:
            payload = transport.get(f"{prefix}/{run_id}", credential, connection)
        except ProviderError:
            raise
        except Exception:
            raise ProviderError("provider_unavailable") from None
        observed = clock()
        if type(observed) is not datetime or observed.tzinfo is None or observed.utcoffset() is None:
            raise ProviderError("invalid_clock")
        rows.append(project_run(connection.scope, payload, run_id, observed.astimezone(timezone.utc).isoformat()))
    try:
        return encode_snapshots(connection.scope, rows)
    except (InvalidRecord, ValueError, TypeError):
        raise ProviderError("invalid_response") from None
