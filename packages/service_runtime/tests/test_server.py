# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Transport-policy tests using Gunicorn's actual parser, without sockets."""
import contextlib
import importlib.util
import io
import logging
import os
from pathlib import Path
import ssl
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from packages.service_runtime import server


HAS_GUNICORN = importlib.util.find_spec("gunicorn") is not None


class LauncherTests(unittest.TestCase):
    def test_environment_overrides_fail_closed_without_logging_values(self):
        for name in ("GUNICORN_CMD_ARGS", "GUNICORN_FD", "GUNICORN_PID", "LISTEN_FDS",
                     "LISTEN_PID", "LISTEN_FDNAMES", "NOTIFY_SOCKET", "SCRIPT_NAME",
                     "PATH_INFO", "WEB_CONCURRENCY", "PORT", "FORWARDED_ALLOW_IPS", "SENDFILE"):
            with self.subTest(name=name), patch.dict(os.environ, {name: "synthetic-sensitive-input"}, clear=True):
                with self.assertRaisesRegex(server.ServerError, "^service_runtime_server_failed$"):
                    server._check_environment()

    def test_invalid_port_never_creates_application(self):
        for port in (True, False, 0, -1, 65536, "8443", None):
            with self.subTest(port=port), patch.dict(os.environ, {}, clear=True), patch.object(server, "_tls_context") as tls:
                with self.assertRaises(server.ServerError):
                    server.run_server("sensitive-config", "cert", "key", port)
                tls.assert_not_called()

    def test_tls_key_permissions_and_symlink_rejected_before_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            cert, key = Path(directory) / "cert", Path(directory) / "key"
            cert.write_text("not-a-certificate"); cert.chmod(0o600)
            key.write_text("not-a-private-key"); key.chmod(0o644)
            with patch.object(server.ssl, "SSLContext") as context:
                with self.assertRaises(server.ServerError):
                    server._tls_context(cert, key)
                context.assert_not_called()
                key.chmod(0o600)
                link = Path(directory) / "link"; link.symlink_to(key)
                with self.assertRaises(server.ServerError):
                    server._tls_context(cert, link)
                context.assert_not_called()

    def test_tls_floor_alpn_and_no_password_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            cert, key = Path(directory) / "cert", Path(directory) / "key"
            for path in (cert, key): path.touch(mode=0o600)
            fake = Mock(options=0)
            with patch.object(server.ssl, "SSLContext", return_value=fake) as context:
                self.assertIs(server._tls_context(cert, key), fake)
                context.assert_called_once_with(ssl.PROTOCOL_TLS_SERVER)
                self.assertEqual(fake.minimum_version, ssl.TLSVersion.TLSv1_2)
                fake.set_alpn_protocols.assert_called_once_with(["http/1.1"])
                self.assertEqual(fake.load_cert_chain.call_args.kwargs["password"](), "")

    def test_startup_failure_and_direct_prints_do_not_leak(self):
        output = io.StringIO()
        app = Mock()
        def fail(*args):
            print("synthetic-sensitive-input")
            print("synthetic-sensitive-input", file=server.sys.stderr)
            raise ValueError("synthetic-sensitive-input")
        with patch.dict(os.environ, {}, clear=True), patch.object(server, "_tls_context"), \
                patch("packages.service_runtime.runtime.create_application", return_value=app), \
                patch.object(server, "_build_application", side_effect=fail), \
                contextlib.redirect_stderr(output), contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(server.ServerError, "^service_runtime_server_failed$"):
                server.run_server("config", "cert", "key", 8443)
        self.assertEqual(output.getvalue(), "")
        app.close.assert_called_once_with()

    def test_existing_library_logger_cannot_bypass_redirection(self):
        output = io.StringIO(); log = logging.getLogger("synthetic-acceptance")
        handler = logging.StreamHandler(output); log.addHandler(handler)
        prior = logging.root.manager.disable
        try:
            with server._quiet_libraries():
                log.error("synthetic-sensitive-input", exc_info=True)
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(logging.root.manager.disable, prior)
        finally:
            log.removeHandler(handler)

    def test_master_closes_lease_on_clean_exit(self):
        app = Mock(); runner = Mock(); runner.run.side_effect = SystemExit(0)
        with patch.dict(os.environ, {}, clear=True), patch.object(server, "_tls_context"), \
                patch("packages.service_runtime.runtime.create_application", return_value=app), \
                patch.object(server, "_build_application", return_value=runner):
            server.run_server("config", "cert", "key", 8443)
        app.close.assert_called_once_with()

    def test_worker_exit_does_not_close_shared_master_lease(self):
        app = Mock(); runner = Mock(); runner.run.side_effect = SystemExit(0)
        with patch.dict(os.environ, {}, clear=True), patch.object(server, "_tls_context"), \
                patch("packages.service_runtime.runtime.create_application", return_value=app), \
                patch.object(server, "_build_application", return_value=runner), \
                patch.object(server.os, "getpid", side_effect=[100, 101, 101]):
            with self.assertRaises(SystemExit):
                server.run_server("config", "cert", "key", 8443)
        app.close.assert_not_called()

    def test_logger_never_formats_input_or_exception(self):
        class Toxic:
            def __str__(self):
                raise AssertionError("must not format any input")
        output = io.StringIO(); log = server._FixedLogger(None, output)
        for method in ("debug", "info", "access", "warning", "error", "critical", "exception"):
            getattr(log, method)(Toxic(), Toxic(), exc_info=True)
        self.assertEqual(output.getvalue().splitlines(), ["service_runtime_warning"] + ["service_runtime_error"] * 3)


@unittest.skipUnless(HAS_GUNICORN, "install the hash-pinned service runtime requirements")
class GunicornPolicyTests(unittest.TestCase):
    def setUp(self):
        self.logs = io.StringIO()
        self.app = server._build_application(lambda env, start: [b"ok"], "/tmp/synthetic-cert", "/tmp/synthetic-key",
                                             8443, ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER), self.logs)
        self.cfg = self.app.cfg

    def request(self, raw):
        from gunicorn.http.parser import RequestParser
        request = next(RequestParser(self.cfg, [raw], ("127.0.0.1", 10000)))
        self.cfg.pre_request(None, request)
        return request

    def rejected(self, raw):
        from gunicorn.http.errors import ParseException
        with self.assertRaises(ParseException): self.request(raw)

    def test_fixed_transport_and_no_config_or_plugin_loading(self):
        self.assertEqual(self.cfg.address, [("127.0.0.1", 8443)])
        self.assertEqual(self.cfg.workers, 2); self.assertEqual(self.cfg.threads, 1)
        self.assertEqual(self.cfg.timeout, 30); self.assertEqual(self.cfg.graceful_timeout, 10)
        self.assertEqual(self.cfg.http_parser, "python"); self.assertEqual(self.cfg.http_protocols, ["h1"])
        self.assertEqual(self.cfg.forwarded_allow_ips, []); self.assertEqual(self.cfg.forwarder_headers, [])
        self.assertEqual(self.cfg.proxy_allow_ips, []); self.assertEqual(self.cfg.proxy_protocol, "off")
        self.assertEqual(self.cfg.secure_scheme_headers, {}); self.assertEqual(self.cfg.header_map, "refuse")
        self.assertTrue(self.cfg.control_socket_disable); self.assertEqual(self.cfg.dirty_apps, [])
        self.assertIsNone(self.cfg.wsgi_app); self.assertFalse(self.cfg.reload)
        self.assertIsNone(self.cfg.accesslog); self.assertIsNone(self.cfg.logconfig)
        with tempfile.TemporaryDirectory() as directory:
            malicious = Path(directory) / "gunicorn.conf.py"
            malicious.write_text("raise AssertionError('configuration must not execute')")
            old = os.getcwd()
            try:
                os.chdir(directory)
                self.setUp()
            finally:
                os.chdir(old)

    def test_incompatible_version_refused(self):
        with patch("gunicorn.__version__", "0.0.0"):
            with self.assertRaises(server.ServerError): self.setUp()

    def test_valid_http11_request(self):
        request = self.request(b"GET /?opaque=synthetic HTTP/1.1\r\nHost: localhost:8443\r\nConnection: close\r\n\r\n")
        self.assertEqual(request.scheme, "https")

    def test_duplicate_security_headers_rejected_before_wsgi_merging(self):
        headers = ("Origin", "Referer", "Cookie", "Authorization", "X-CSRF-Token", "If-Match",
                   "Idempotency-Key", "Sec-Fetch-Site", "X-MS-Collab-Login", "Content-Type")
        for name in headers:
            with self.subTest(name=name):
                self.rejected((f"GET / HTTP/1.1\r\nHost: localhost\r\n{name}: synthetic-one\r\n"
                               f"{name.lower()}: synthetic-two\r\n\r\n").encode())
        self.rejected(b"GET / HTTP/1.1\r\nHost: localhost\r\nhost: localhost\r\n\r\n")

    def test_framing_ambiguity_and_unsupported_transfer_rejected(self):
        for headers in (
            b"Content-Length: 0\r\nContent-Length: 0\r\n",
            b"Content-Length: 0\r\nContent-Length: 1\r\n",
            b"Transfer-Encoding: chunked\r\nContent-Length: 0\r\n",
            b"Transfer-Encoding: chunked\r\nTransfer-Encoding: chunked\r\n",
            b"Transfer-Encoding: chunked\r\n", b"TE: trailers\r\n", b"Trailer: Cookie\r\n",
            b"Content-Length: 00\r\n", b"Content-Length: 0,0\r\n", b"Content-Length: +0\r\n", b"Content-Length: 1048577\r\n",
            b"Expect: 100-continue\r\n", b"Connection: close, Cookie\r\n"):
            with self.subTest(headers=headers):
                self.rejected(b"POST / HTTP/1.1\r\nHost: localhost\r\n" + headers + b"\r\n0\r\n\r\n")

    def test_proxy_headers_and_protocol_never_trusted(self):
        for name in ("Forwarded", "X-Forwarded-For", "X-Forwarded-Proto", "X-Forwarded-Host", "X-Real-IP",
                     "X-Original-URL", "X-Rewrite-URL", "X-Url-Scheme", "Front-End-Https", "Proxy-Authorization",
                     "X_Forwarded_Proto", "Script_Name", "Path_Info"):
            with self.subTest(name=name):
                self.rejected(f"GET / HTTP/1.1\r\nHost: localhost\r\n{name}: synthetic-input\r\n\r\n".encode())
        self.rejected(b"PROXY TCP4 127.0.0.1 127.0.0.1 10000 8443\r\nGET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

    def test_limits_and_noncanonical_requests_rejected(self):
        for raw in (
            b"GET / HTTP/1.1\r\n\r\n", b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n",
            b"GET https://localhost/ HTTP/1.1\r\nHost: localhost\r\n\r\n",
            b"GET //localhost/ HTTP/1.1\r\nHost: localhost\r\n\r\n",
            b"GET / HTTP/1.1\r\nHost : localhost\r\n\r\n",
            b"GET / HTTP/1.1\r\nHost: localhost\r\nCookie: first\r\n second\r\n\r\n",
            b"GET /" + b"x" * 4096 + b" HTTP/1.1\r\nHost: localhost\r\n\r\n",
            b"GET / HTTP/1.1\r\nHost: localhost\r\nCookie: " + b"x" * 4096 + b"\r\n\r\n",
            b"GET / HTTP/1.1\r\nHost: localhost\r\n" + b"X-Header: x\r\n" * 64 + b"\r\n"):
            self.rejected(raw)

    def test_parser_and_application_errors_never_echo_to_response_or_log(self):
        from gunicorn.http.errors import InvalidHeader, InvalidResponseHeader
        worker = object.__new__(self.cfg.worker_class)
        worker.log = self.cfg.logger_class(self.cfg)
        for error, status in ((InvalidHeader("synthetic-sensitive-input"), b"400"),
                              (ValueError("synthetic-sensitive-input"), b"500"),
                              (InvalidResponseHeader("synthetic-sensitive-input"), b"500")):
            client = Mock()
            worker.handle_error(types.SimpleNamespace(uri="/?secret=synthetic-sensitive-input"), client, None, error)
            body = b"".join(call.args[0] for call in client.sendall.call_args_list)
            self.assertIn(status, body)
            self.assertNotIn(b"synthetic-sensitive-input", body)
        self.assertNotIn("synthetic-sensitive-input", self.logs.getvalue())

    def test_wsgi_error_stream_cannot_log_inputs(self):
        environment = {"wsgi.errors": self.logs}
        self.app.wsgi()(environment, Mock())
        environment["wsgi.errors"].write("synthetic-sensitive-input")
        self.assertEqual(self.logs.getvalue(), "")

    def test_runtime_management_signals_are_disabled(self):
        with patch.object(server.signal, "signal") as handler:
            self.cfg.when_ready(None)
            self.assertEqual(handler.call_count, 4)
            for call in handler.call_args_list: self.assertEqual(call.args[1], server.signal.SIG_IGN)


if __name__ == "__main__":
    unittest.main()
