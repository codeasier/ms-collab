"""Synthetic transport responses only. No credentials or live GitCode calls."""
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from packages.identity import AuthenticationError, AuthenticationUnavailable, Credential, Provider
from packages.gitcode_identity import GitCodeConfig, GitCodeIdentityEvidence, GitCodeOAuthClient
from packages.gitcode_identity.client import (HTTPSOnlyTransport, MAX_BODY, Request, Response,
                                            TOKEN_URL, USER_URL)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def grant(**changes):
    value = {"access_token": "synthetic-access-only", "expires_in": 3600,
             "created_at": "2026-01-01T00:00:00Z", "scope": "example_user",
             "refresh_token": "synthetic-refresh-never-used"}
    value.update(changes)
    return value


def response(value, status=200, content_type="application/json"):
    return Response(status, content_type, json.dumps(value).encode())


class StubTransport:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        result = self.replies.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class ClientTests(unittest.TestCase):
    def config(self, **changes):
        values = dict(client_id="synthetic-client", client_secret="synthetic-secret",
                      redirect_uri="https://client.example.invalid/callback",
                      scopes=("example_user",))
        values.update(changes)
        return GitCodeConfig(**values)

    def client(self, replies=None, clock=lambda: NOW):
        transport = StubTransport(replies or [response(grant()), response({"id": "opaque-user-01"})])
        return GitCodeOAuthClient(self.config(), transport=transport, clock=clock), transport

    def test_authorization_url(self):
        client, transport = self.client()
        url = urlsplit(client.authorization_url("a" * 48, prompt="consent"))
        self.assertEqual((url.scheme, url.netloc, url.path), ("https", "gitcode.com", "/oauth/authorize"))
        self.assertEqual(parse_qs(url.query), {
            "client_id": ["synthetic-client"], "redirect_uri": [self.config().redirect_uri],
            "scope": ["example_user"], "state": ["a" * 48], "response_type": ["code"],
            "prompt": ["consent"]})
        self.assertEqual(transport.requests, [])
        self.assertNotIn("synthetic-secret", url.geturl())

    def test_state_required_but_builder_not_entropy_proof(self):
        client, _ = self.client()
        for state in (None, "", "short", "a" * 257, "a" * 32 + "\n", "汉" * 32):
            with self.subTest(state=state), self.assertRaises(ValueError):
                client.authorization_url(state)
        with self.assertRaises(ValueError):
            client.authorization_url("a" * 48, prompt="none")

    def test_explicit_configuration(self):
        for override in ({"client_id": ""}, {"client_secret": ""}, {"client_secret": "x\r\ny"},
                         {"scopes": ()}, {"scopes": ("example_user", "example_user")},
                         {"scopes": ("bad scope",)}, {"max_token_lifetime": timedelta(0)}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.config(**override)

    def test_callback_no_insecure_or_injected_components(self):
        for uri in ("http://example.invalid/callback", "https://u:p@example.invalid/cb",
                    "https://example.invalid/cb?next=x", "https://example.invalid/cb#x",
                    "https://example.invalid:99999/cb", "https:///cb", "https://example.invalid\\x/cb"):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                self.config(redirect_uri=uri)

    def test_exchange_wire_format_and_minimized_evidence(self):
        client, transport = self.client([response(grant()), response({
            "id": "opaque-user-01", "email": "unused@example.invalid", "login": "mutable-name",
            "role": "admin", "organization": {"id": "ignored"}})])
        evidence = client.exchange_identity(Credential("synthetic-code+&/="))
        self.assertEqual(evidence, GitCodeIdentityEvidence("opaque-user-01", NOW, NOW + timedelta(hours=1)))
        post, get = transport.requests
        self.assertEqual(post.method, "POST")
        self.assertEqual(parse_qs(urlsplit(post.url).query), {
            "grant_type": ["authorization_code"], "code": ["synthetic-code+&/="],
            "client_id": ["synthetic-client"]})
        self.assertNotIn("client_secret", post.url)
        self.assertNotIn("synthetic-secret", post.url)
        self.assertIn(b'name="client_secret"\r\n\r\nsynthetic-secret\r\n', post.body)
        self.assertTrue(post.headers["Content-Type"].startswith("multipart/form-data; boundary="))
        self.assertEqual(get.url, USER_URL)
        self.assertEqual(get.headers["Authorization"], "Bearer synthetic-access-only")
        self.assertIsNone(get.body)
        self.assertFalse(hasattr(evidence, "audience"))
        self.assertFalse(hasattr(evidence, "access_token"))
        self.assertFalse(hasattr(evidence, "role"))

    def test_not_a_bearer_verifier(self):
        client, _ = self.client()
        self.assertFalse(hasattr(client, "verify"))
        with self.assertRaises(ValueError):
            Provider("gitcode", "https://gitcode.com", "app", client)

    def test_secret_repr_suppression(self):
        client, transport = self.client()
        client.exchange_identity(Credential("synthetic-code"))
        texts = repr(self.config()) + repr(client) + repr(transport.requests) + repr(response(grant()))
        for secret in ("synthetic-secret", "synthetic-code", "synthetic-access-only", "synthetic-refresh"):
            self.assertNotIn(secret, texts)

    def test_code_must_be_opaque_credential(self):
        client, transport = self.client()
        for value in ("synthetic-code", None, {"code": "synthetic-code"}, Credential("汉字")):
            with self.subTest(value=type(value).__name__), self.assertRaises(AuthenticationError):
                client.exchange_identity(value)
        self.assertFalse(transport.requests)

    def test_grant_expiry_and_scopes_required(self):
        for field in ("access_token", "expires_in", "created_at", "scope"):
            payload = grant()
            del payload[field]
            client, transport = self.client([response(payload)])
            with self.subTest(field=field), self.assertRaises(AuthenticationUnavailable):
                client.exchange_identity(Credential("synthetic-code"))
            self.assertEqual(len(transport.requests), 1)

    def test_lifetime_rejects_wrong_types_and_ranges(self):
        for ttl in (True, "3600", 1.5, 0, -1, 10**100, 32 * 86400):
            client, transport = self.client([response(grant(expires_in=ttl))])
            with self.subTest(ttl=ttl), self.assertRaises(AuthenticationUnavailable):
                client.exchange_identity(Credential("synthetic-code"))
            self.assertEqual(len(transport.requests), 1)

    def test_access_token_header_injection_rejected(self):
        for token in ("", 1, "foo\nbar", "a" * 8193, "空间"):
            client, _ = self.client([response(grant(access_token=token))])
            with self.subTest(token_type=type(token)), self.assertRaises(AuthenticationUnavailable):
                client.exchange_identity(Credential("synthetic-code"))

    def test_missing_requested_scope_denies_without_user_lookup(self):
        client, transport = self.client([response(grant(scope="different_scope"))])
        with self.assertRaises(AuthenticationError):
            client.exchange_identity(Credential("synthetic-code"))
        self.assertEqual(len(transport.requests), 1)

    def test_bad_created_at_unavailable(self):
        for created in (None, 1767225600, "2026-01-01T00:00:00", "invalid"):
            client, _ = self.client([response(grant(created_at=created))])
            with self.subTest(created=created), self.assertRaises(AuthenticationUnavailable):
                client.exchange_identity(Credential("synthetic-code"))

    def test_future_and_expired_grant_deny(self):
        for created in ("2026-01-01T00:00:01Z", "2025-12-31T23:00:00Z"):
            client, _ = self.client([response(grant(created_at=created))])
            with self.subTest(created=created), self.assertRaises(AuthenticationError):
                client.exchange_identity(Credential("synthetic-code"))

    def test_explicit_timezone_normalized_by_instant(self):
        client, _ = self.client([response(grant(created_at="2026-01-01T08:00:00+08:00")), response({"id": "u"})])
        self.assertEqual(client.exchange_identity(Credential("synthetic-code")).grant_expires_at,
                         NOW + timedelta(hours=1))

    def test_expiry_during_user_lookup_denied(self):
        times = iter((NOW, NOW, NOW + timedelta(hours=1)))
        client, _ = self.client(clock=lambda: next(times))
        with self.assertRaises(AuthenticationError):
            client.exchange_identity(Credential("synthetic-code"))

    def test_clock_rollback_denied(self):
        for times in ((NOW, NOW - timedelta(seconds=1)), (NOW, NOW, NOW - timedelta(seconds=1))):
            values = iter(times)
            client, _ = self.client(clock=lambda: next(values))
            with self.subTest(times=times), self.assertRaises(AuthenticationError):
                client.exchange_identity(Credential("synthetic-code"))

    def test_bad_clock_unavailable(self):
        client, transport = self.client(clock=lambda: datetime(2026, 1, 1))
        with self.assertRaises(AuthenticationUnavailable):
            client.exchange_identity(Credential("synthetic-code"))
        self.assertFalse(transport.requests)

    def test_stable_subject_only_no_name_fallback(self):
        for user in ({"login": "name"}, {"id": 123}, {"id": True}, {"id": ""}, {"id": " "},
                     {"id": "../a"}, {"id": "a" * 129}, {"id": "\ud800"}):
            client, _ = self.client([response(grant()), response(user)])
            with self.subTest(user=user), self.assertRaises(AuthenticationUnavailable):
                client.exchange_identity(Credential("synthetic-code"))

    def test_denied_statuses(self):
        for status in (400, 401, 403):
            client, _ = self.client([response({"secret": "do not expose"}, status=status)])
            with self.subTest(status=status), self.assertRaisesRegex(AuthenticationError, "^authentication_required$"):
                client.exchange_identity(Credential("synthetic-code"))
        for status in (401, 403):
            client, _ = self.client([response(grant()), response({}, status=status)])
            with self.assertRaises(AuthenticationError):
                client.exchange_identity(Credential("synthetic-code"))

    def test_other_statuses_are_unavailable_no_retry(self):
        for status in (201, 204, 301, 302, 307, 308, 404, 429, 500, 503):
            client, transport = self.client([response({}, status=status)])
            with self.subTest(status=status), self.assertRaises(AuthenticationUnavailable):
                client.exchange_identity(Credential("synthetic-code"))
            self.assertEqual(len(transport.requests), 1)

    def test_rejects_nonobject_duplicate_nonfinite_and_invalid_json(self):
        for body in (b'[]', b'null', b'{"a":1,"a":2}', b'{"a":NaN}', b'{}{}', b'\xff', b'x' * (MAX_BODY + 1)):
            client, _ = self.client([Response(200, "application/json", body)])
            with self.subTest(body=body[:32]), self.assertRaises(AuthenticationUnavailable):
                client.exchange_identity(Credential("synthetic-code"))

    def test_wrong_content_type(self):
        for content_type in ("text/html", "", None):
            client, _ = self.client([Response(200, content_type, b'{}')])
            with self.subTest(content_type=content_type), self.assertRaises(AuthenticationUnavailable):
                client.exchange_identity(Credential("synthetic-code"))

    def test_transport_exception_is_sanitized(self):
        client, _ = self.client([RuntimeError("synthetic-secret: response body")])
        with self.assertRaises(AuthenticationUnavailable) as cm:
            client.exchange_identity(Credential("synthetic-code"))
        self.assertEqual(str(cm.exception), "authentication_unavailable")
        self.assertIsNone(cm.exception.__cause__)
        self.assertTrue(cm.exception.__suppress_context__)


class HTTPSOnlyTransportTests(unittest.TestCase):
    def test_timeout_bounds(self):
        for value in (0, -1, 31, True, float("inf"), float("nan"), "5"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                HTTPSOnlyTransport(timeout=value)

    def test_fixed_endpoints_only_without_network(self):
        bad = [("GET", "https://evil.example.invalid/user"), ("GET", USER_URL + "?token=x"),
               ("GET", "http://api.gitcode.com/api/v5/user"), ("GET", "https://u:p@api.gitcode.com/api/v5/user"),
               ("GET", "https://api.gitcode.com:443/api/v5/user"), ("GET", USER_URL + "#fragment"),
               ("POST", USER_URL), ("GET", TOKEN_URL)]
        with patch("packages.gitcode_identity.client.HTTPSConnection") as connection:
            for method, url in bad:
                with self.subTest(url=url), self.assertRaises(AuthenticationUnavailable):
                    HTTPSOnlyTransport().send(Request(method, url, {}))
            connection.assert_not_called()

    def fake_connection(self, status=200, body=b'{}'):
        connection = unittest.mock.MagicMock()
        response_object = connection.getresponse.return_value
        response_object.status = status
        response_object.read.return_value = body
        response_object.getheader.return_value = "application/json"
        return connection, response_object

    def test_tls_direct_request_and_bounded_read(self):
        conn, upstream = self.fake_connection()
        with patch("packages.gitcode_identity.client.HTTPSConnection", return_value=conn) as ctor:
            result = HTTPSOnlyTransport(timeout=2).send(Request("GET", USER_URL, {"Accept": "application/json"}))
        self.assertEqual(ctor.call_args.args, ("api.gitcode.com",))
        self.assertTrue(ctor.call_args.kwargs["context"].check_hostname)
        self.assertEqual(ctor.call_args.kwargs["timeout"], 2)
        conn.request.assert_called_once_with("GET", "/api/v5/user", body=None, headers={"Accept": "application/json"})
        upstream.read.assert_called_once_with(MAX_BODY + 1)
        conn.close.assert_called_once()
        self.assertEqual(result.body, b'{}')

    def test_redirect_does_not_follow_or_read_body(self):
        conn, upstream = self.fake_connection(302)
        with patch("packages.gitcode_identity.client.HTTPSConnection", return_value=conn) as ctor:
            result = HTTPSOnlyTransport().send(Request("POST", TOKEN_URL + "?code=synthetic", {}, b"synthetic"))
        self.assertEqual(result.status, 302)
        upstream.read.assert_not_called()
        self.assertEqual(ctor.call_count, 1)
        conn.close.assert_called_once()

    def test_oversized_response_closed_and_sanitized(self):
        conn, _ = self.fake_connection(body=b"x" * (MAX_BODY + 1))
        with patch("packages.gitcode_identity.client.HTTPSConnection", return_value=conn):
            with self.assertRaises(AuthenticationUnavailable):
                HTTPSOnlyTransport().send(Request("GET", USER_URL, {}))
        conn.close.assert_called_once()

    def test_connection_error_closed_and_sanitized(self):
        conn, _ = self.fake_connection()
        conn.request.side_effect = RuntimeError("synthetic-credential")
        with patch("packages.gitcode_identity.client.HTTPSConnection", return_value=conn):
            with self.assertRaisesRegex(AuthenticationUnavailable, "^authentication_unavailable$"):
                HTTPSOnlyTransport().send(Request("GET", USER_URL, {}))
        conn.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
