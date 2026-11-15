# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse

from packages.community_board.model import Scope, BoardItem, Snapshot
from packages.community_sync import *
from packages.community_sync.provider import HTTPSReadTransport, _NoRedirect

SCOPE = Scope("team-a", "gitcode-one", "sample/project")
STAMP = "2026-01-01T00:00:00Z"
LATER = "2026-01-02T00:00:00Z"
LAST = "2026-01-03T00:00:00Z"
TOKEN = "synthetic-test-token"


def row(number=1, **changes):
    value = dict(number=number, title="Synthetic task", state="open", updated_at=STAMP,
                 labels=[{"name": "test"}], body="omitted-private-body", user={"name": "omitted-profile"},
                 html_url="https://example.invalid/omitted", access_token="omitted-provider-field")
    value.update(changes)
    return value


def snapshot(scope=SCOPE, at=LATER, title="Synthetic task", updated=STAMP):
    return Snapshot(scope, at, (BoardItem("issue", 1, title, "open", updated, ("test",)),))


class FakeTransport:
    def __init__(self, pages=None):
        self.pages = pages or {("issues", 1): [row()], ("pulls", 1): []}
        self.calls = []

    def get(self, path, credential, connection):
        self.calls.append((path, credential.scope))
        parsed = urllib.parse.urlsplit(path)
        kind = parsed.path.split("/")[-1]
        page = int(urllib.parse.parse_qs(parsed.query)["page"][0])
        value = self.pages[(kind, page)]
        if isinstance(value, Exception):
            raise value
        return value


class ProviderTests(unittest.TestCase):
    def observe(self, transport=None, **limits):
        return GitCodeProvider(transport or FakeTransport()).observe(
            Connection(SCOPE, **limits), BoundCredential(SCOPE, TOKEN), observed_at=LATER)

    def test_normalized_allowlist(self):
        result = self.observe(FakeTransport({("issues", 1): [row("2", state="opened")],
                                           ("pulls", 1): [row(2, state="merged")]}))
        self.assertEqual([(i.kind, i.number, i.state) for i in result.items],
                         [("issue", 2, "open"), ("pull", 2, "merged")])
        self.assertNotIn("omitted", str([item.as_dict() for item in result.items]))

    def test_credential_scope_mismatch_never_calls_transport(self):
        for scope in (Scope("team-b", SCOPE.connection_id, SCOPE.repository),
                      Scope(SCOPE.workspace_id, "other", SCOPE.repository),
                      Scope(SCOPE.workspace_id, SCOPE.connection_id, "sample/other")):
            transport = FakeTransport()
            with self.assertRaises(ProviderError):
                GitCodeProvider(transport).observe(Connection(SCOPE), BoundCredential(scope, TOKEN), observed_at=LATER)
            self.assertEqual(transport.calls, [])

    def test_credentials_not_repr_and_header_safe(self):
        self.assertNotIn(TOKEN, repr(BoundCredential(SCOPE, TOKEN)))
        for value in ("", "has space", "cr\r", "lf\n", "\x00", "é", "x" * 4097):
            with self.subTest(value=value), self.assertRaises(ValueError):
                BoundCredential(SCOPE, value)

    def test_limits(self):
        for value in ({"max_pages": 0}, {"max_pages": 51}, {"page_size": 101},
                      {"page_size": True}, {"timeout": 61}, {"max_response_bytes": 255}):
            with self.assertRaises(ValueError):
                Connection(SCOPE, **value)

    def test_bounded_scan_not_complete(self):
        transport = FakeTransport({("issues", 1): [row(1)], ("issues", 2): [row(2)],
                                   ("pulls", 1): [row(1)], ("pulls", 2): [row(2)]})
        result = self.observe(transport, page_size=1, max_pages=2)
        self.assertEqual(len(result.items), 4)
        self.assertEqual(len(transport.calls), 4)
        self.assertTrue(all("sort=updated" in call[0] and "state=all" in call[0] for call in transport.calls))

    def test_short_page_stops(self):
        transport = FakeTransport()
        self.observe(transport)
        self.assertEqual(len(transport.calls), 2)

    def test_bad_rows_fail_whole_observation(self):
        for bad in (row(number=True), row(number="I123"), row(title=""), row(state="locked"),
                    row(updated_at="2026-01-01"), row(updated_at="0001-01-01T00:00:00+14:00"), row(updated_at=LAST), row(labels=["plain"]),
                    row(labels=[{}]), row(labels=[{"name": ""}]), row(state=None), "not an object"):
            with self.subTest(bad=bad), self.assertRaises(ProviderError):
                self.observe(FakeTransport({("issues", 1): [bad], ("pulls", 1): []}))

    def test_nonlist_and_oversized_page_fail(self):
        for page in ({"message": "error"}, None, [row(1), row(2)]):
            with self.assertRaises(ProviderError):
                self.observe(FakeTransport({("issues", 1): page}), page_size=1)

    def test_duplicate_page_identity_fails(self):
        with self.assertRaises(ProviderError):
            self.observe(FakeTransport({("issues", 1): [row()], ("issues", 2): [row()],
                                        ("pulls", 1): []}), page_size=1, max_pages=2)

    def test_http_failure_not_empty_success(self):
        with self.assertRaises(ProviderError):
            self.observe(FakeTransport({("issues", 1): ProviderError("provider request failed")}))


class Response(io.BytesIO):
    def __init__(self, payload=b"[]", status=200, headers=None):
        super().__init__(payload)
        self.status = status
        self.headers = headers or {}


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.connection = Connection(SCOPE, max_response_bytes=256)
        self.credential = BoundCredential(SCOPE, TOKEN)
        self.path = "/api/v5/repos/sample/project/issues?state=all&sort=updated&direction=desc&page=1&per_page=100"

    def get(self, response):
        with patch("urllib.request.build_opener") as build:
            build.return_value.open.return_value = response
            value = HTTPSReadTransport().get(self.path, self.credential, self.connection)
            return value, build

    def test_get_fixed_origin_header_only_no_proxy(self):
        result, build = self.get(Response())
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(result, [])
        self.assertTrue(request.full_url.startswith("https://api.gitcode.com/api/v5/repos/sample/project/"))
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertNotIn(TOKEN, request.full_url)
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1], _NoRedirect)

    def test_redirect_handler_never_follows(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.invalid"))

    def test_http_status_errors_body_not_exposed(self):
        for code in (301, 302, 307, 401, 403, 404, 429, 500):
            with patch("urllib.request.build_opener") as build:
                build.return_value.open.side_effect = urllib.error.HTTPError(
                    "https://example.invalid/" + TOKEN, code, TOKEN, {}, io.BytesIO(TOKEN.encode()))
                with self.assertRaises(ProviderError) as error:
                    HTTPSReadTransport().get(self.path, self.credential, self.connection)
                self.assertNotIn(TOKEN, str(error.exception))

    def test_malformed_query_error_is_sanitized(self):
        with self.assertRaises(ProviderError) as error:
            HTTPSReadTransport().get(self.path + "&" + TOKEN, self.credential, self.connection)
        self.assertNotIn(TOKEN, str(error.exception))

    def test_payload_errors(self):
        for response in (Response(b"x" * 257), Response(b"not-json"), Response(b"\xff"),
                         Response(b'{"a":1,"a":2}'), Response(b"[NaN]"), Response(status=204),
                         Response(headers={"Content-Encoding": "gzip"})):
            with self.assertRaises(ProviderError):
                self.get(response)

    def test_reject_other_paths_and_query(self):
        for path in ("https://evil.invalid", "/api/v5/repos/sample/other/issues?x=1",
                     self.path.replace("issues", "comments"), self.path + "&access_token=x",
                     self.path + "&page=2", self.path.replace("page=1", "page=999")):
            with patch("urllib.request.build_opener") as build:
                with self.assertRaises(ProviderError):
                    HTTPSReadTransport().get(path, self.credential, self.connection)
                build.assert_not_called()


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "observations.sqlite"
        self.store = SnapshotStore(self.path)
        self.store.initialize()

    def test_private_file_no_implicit_reinitialize(self):
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            self.store.initialize()
        other = SnapshotStore(self.path.with_name("absent"))
        with self.assertRaises(StoreError):
            other.load(SCOPE)
        self.assertFalse(other.path.exists())

    def test_roundtrip_after_restart(self):
        result = self.store.publish(snapshot(), expected_revision=0)
        self.assertEqual(result.revision, 1)
        self.assertEqual(SnapshotStore(self.path).load(SCOPE), result)

    def test_all_scope_parts_isolated(self):
        self.store.publish(snapshot(), expected_revision=0)
        for scope in (Scope("public", SCOPE.connection_id, SCOPE.repository),
                      Scope(SCOPE.workspace_id, "other", SCOPE.repository),
                      Scope(SCOPE.workspace_id, SCOPE.connection_id, "sample/other")):
            self.assertIsNone(self.store.load(scope))
            self.store.publish(snapshot(scope, title="Different"), expected_revision=0)
            self.assertEqual(self.store.load(scope).snapshot.items[0].title, "Different")
        self.assertEqual(self.store.load(SCOPE).snapshot.items[0].title, "Synthetic task")

    def test_revision_compare_and_swap(self):
        first = self.store.publish(snapshot(), expected_revision=0)
        newer = snapshot(at=LAST, updated=LATER, title="Changed")
        with self.assertRaises(Conflict):
            self.store.publish(newer, expected_revision=0)
        self.assertEqual(self.store.load(SCOPE), first)
        self.assertEqual(self.store.publish(newer, expected_revision=1).revision, 2)

    def test_stale_snapshot_and_item_rejected(self):
        first = self.store.publish(snapshot(at=LAST, updated=LATER), expected_revision=0)
        for stale in (snapshot(), snapshot(at=LAST, updated=STAMP)):
            with self.assertRaises(ValueError):
                self.store.publish(stale, expected_revision=1)
            self.assertEqual(self.store.load(SCOPE), first)

    def test_equal_item_timestamp_changed_payload_rejected(self):
        first = self.store.publish(snapshot(), expected_revision=0)
        with self.assertRaises(Conflict):
            self.store.publish(snapshot(at=LAST, title="Ambiguous"), expected_revision=1)
        self.assertEqual(self.store.load(SCOPE), first)

    def test_equal_observation_changed_items_rejected(self):
        self.store.publish(snapshot(), expected_revision=0)
        with self.assertRaises(Conflict):
            self.store.publish(Snapshot(SCOPE, LATER, ()), expected_revision=1)

    def test_identical_publish_no_revision_churn(self):
        first = self.store.publish(snapshot(), expected_revision=0)
        self.assertEqual(self.store.publish(snapshot(), expected_revision=1), first)

    def test_missing_is_not_closed(self):
        self.store.publish(snapshot(), expected_revision=0)
        empty = self.store.publish(Snapshot(SCOPE, LAST, ()), expected_revision=1)
        self.assertEqual(empty.snapshot.items, ())

    def test_corruption_fails_closed(self):
        self.store.publish(snapshot(), expected_revision=0)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE snapshots SET payload=?", ('{"token":"not-a-valid-snapshot"}',))
        with self.assertRaises(StoreError):
            self.store.load(SCOPE)
        with self.assertRaises(StoreError):
            self.store.publish(snapshot(at=LAST), expected_revision=1)

    def test_unknown_version_fails_closed(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE metadata SET version=9")
        with self.assertRaises(StoreError):
            self.store.load(SCOPE)

    def test_refresh_end_to_end_and_allowlist_storage(self):
        result = refresh(Connection(SCOPE), BoundCredential(SCOPE, TOKEN), self.store,
                         provider=GitCodeProvider(FakeTransport()), observed_at=LATER)
        self.assertEqual(result.snapshot, snapshot())
        payload = self.path.read_bytes()
        self.assertNotIn(TOKEN.encode(), payload)
        self.assertNotIn(b"omitted", payload)

    def test_failed_second_endpoint_keeps_old_snapshot(self):
        first = self.store.publish(snapshot(), expected_revision=0)
        provider = GitCodeProvider(FakeTransport({("issues", 1): [row(title="New", updated_at=LATER)],
                                                 ("pulls", 1): ProviderError("upstream unavailable")}))
        with self.assertRaises(ProviderError):
            refresh(Connection(SCOPE), BoundCredential(SCOPE, TOKEN), self.store,
                    provider=provider, observed_at=LAST)
        self.assertEqual(self.store.load(SCOPE), first)

    def test_injected_provider_scope_mismatch_not_published(self):
        other = Scope("team-b", SCOPE.connection_id, SCOPE.repository)
        class WrongProvider:
            def observe(self, *args, **kwargs):
                return snapshot(scope=other)
        with self.assertRaises(ProviderError):
            refresh(Connection(SCOPE), BoundCredential(SCOPE, TOKEN), self.store,
                    provider=WrongProvider(), observed_at=LATER)
        self.assertIsNone(self.store.load(SCOPE))
        self.assertIsNone(self.store.load(other))

    def test_overlapping_refresh_rejected(self):
        store = self.store
        class Overlap(FakeTransport):
            def get(self, *args):
                if not self.calls:
                    store.publish(snapshot(), expected_revision=0)
                return super().get(*args)
        with self.assertRaises(Conflict):
            refresh(Connection(SCOPE), BoundCredential(SCOPE, TOKEN), store,
                    provider=GitCodeProvider(Overlap()), observed_at=LAST)
        self.assertEqual(store.load(SCOPE).revision, 1)


if __name__ == "__main__":
    unittest.main()
