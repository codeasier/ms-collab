# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""GET-only GitCode provider. Credentials are explicit and never discovered."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import http.client
import re
import urllib.error
import urllib.parse
import urllib.request

from packages.community_board.model import BoardItem, Scope, Snapshot

_ORIGIN = "https://api.gitcode.com"
_NUMBER = re.compile(r"[1-9][0-9]{0,18}\Z")


class ProviderError(RuntimeError):
    """Safe diagnostic: no response bodies, request headers or URLs."""


@dataclass(frozen=True, slots=True)
class Connection:
    scope: Scope
    page_size: int = 100
    max_pages: int = 10
    timeout: int = 15
    max_response_bytes: int = 2 * 1024 * 1024

    def __post_init__(self):
        if not isinstance(self.scope, Scope):
            raise ValueError("connection requires an explicit scope")
        for value, low, high in ((self.page_size, 1, 100), (self.max_pages, 1, 50),
                                 (self.timeout, 1, 60), (self.max_response_bytes, 256, 2 * 1024 * 1024)):
            if type(value) is not int or not low <= value <= high:
                raise ValueError("invalid connection limit")


@dataclass(frozen=True, slots=True)
class BoundCredential:
    scope: Scope
    token: str = field(repr=False, compare=False)

    def __post_init__(self):
        if not isinstance(self.scope, Scope):
            raise ValueError("credential requires an explicit scope")
        if not isinstance(self.token, str) or not 1 <= len(self.token) <= 4096:
            raise ValueError("invalid credential")
        if any(not 33 <= ord(char) <= 126 for char in self.token):
            raise ValueError("invalid credential")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


class HTTPSReadTransport:
    """Fixed HTTPS origin, no redirects, environment proxies, writes or retries.

    The provider constructs every path. This transport is deliberately not a
    general authenticated HTTP client and rejects arbitrary relative paths.
    """
    def get(self, path: str, credential: BoundCredential, connection: Connection):
        prefix = f"/api/v5/repos/{connection.scope.repository}/"
        try:
            parsed = urllib.parse.urlsplit(path)
            query = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
        except (TypeError, ValueError):
            raise ProviderError("invalid request parameters") from None
        if (credential.scope != connection.scope or parsed.scheme or parsed.netloc
                or parsed.fragment or parsed.path not in (prefix + "issues", prefix + "pulls")):
            raise ProviderError("request scope mismatch")
        if (set(query) != {"state", "sort", "direction", "page", "per_page"}
                or any(len(values) != 1 for values in query.values())
                or query["state"] != ["all"] or query["sort"] != ["updated"]
                or query["direction"] != ["desc"]
                or query["per_page"] != [str(connection.page_size)]
                or query["page"][0] not in {str(p) for p in range(1, connection.max_pages + 1)}):
            raise ProviderError("invalid request parameters")
        request = urllib.request.Request(_ORIGIN + path, method="GET", headers={
            "Authorization": "Bearer " + credential.token,
            "Accept": "application/json", "Accept-Encoding": "identity",
        })
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=connection.timeout) as response:
                if response.status != 200:
                    raise ProviderError("provider request failed")
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise ProviderError("unsupported response encoding")
                payload = response.read(connection.max_response_bytes + 1)
                if len(payload) > connection.max_response_bytes:
                    raise ProviderError("provider response exceeds limit")
            return json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except ProviderError:
            raise
        except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException, RecursionError):
            raise ProviderError("provider request or response failed") from None


def _normalize(kind: str, raw: dict) -> BoardItem:
    if not isinstance(raw, dict):
        raise ValueError("item must be an object")
    number = raw.get("number")
    if isinstance(number, str) and _NUMBER.fullmatch(number):
        number = int(number)
    labels = raw.get("labels", [])
    if not isinstance(labels, list) or len(labels) > 100:
        raise ValueError("invalid labels")
    # Descriptions, URLs, identities, arbitrary nested data are not persisted.
    names = tuple(label["name"] for label in labels if isinstance(label, dict) and "name" in label)
    if len(names) != len(labels):
        raise ValueError("invalid label shape")
    state = raw.get("state")
    if state == "opened":
        state = "open"
    return BoardItem(kind, number, raw.get("title"), state, raw.get("updated_at"), names)


class GitCodeProvider:
    def __init__(self, transport=None):
        # Injectable transport is trusted code, not a user-configurable endpoint.
        self._transport = transport if transport is not None else HTTPSReadTransport()

    def observe(self, connection: Connection, credential: BoundCredential, *, observed_at: str, before_read=None) -> Snapshot:
        if not isinstance(connection, Connection) or not isinstance(credential, BoundCredential):
            raise ProviderError("connection and credential are required")
        if credential.scope != connection.scope:
            raise ProviderError("credential scope mismatch")
        if before_read is not None and not callable(before_read):
            raise ValueError("trusted request guard required")
        items = []
        for kind, endpoint in (("issue", "issues"), ("pull", "pulls")):
            for page in range(1, connection.max_pages + 1):
                path = f"/api/v5/repos/{connection.scope.repository}/{endpoint}?" + urllib.parse.urlencode({
                    "state": "all", "sort": "updated", "direction": "desc",
                    "page": page, "per_page": connection.page_size,
                })
                if before_read is not None:before_read()
                raw = self._transport.get(path, credential, connection)
                if before_read is not None:before_read()
                try:
                    if not isinstance(raw, list) or len(raw) > connection.page_size:
                        raise ValueError("invalid page")
                    items.extend(_normalize(kind, item) for item in raw)
                except (TypeError, ValueError, KeyError, OverflowError):
                    raise ProviderError("provider records violate snapshot contract") from None
                if len(raw) < connection.page_size:
                    break
        try:
            # Duplicate IDs / future timestamps fail the whole refresh, not just a row.
            return Snapshot(connection.scope, observed_at, tuple(items))
        except (TypeError, ValueError, OverflowError):
            raise ProviderError("provider snapshot is inconsistent") from None
