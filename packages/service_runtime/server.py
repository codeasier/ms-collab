# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Fixed, loopback-only TLS assembly of the pinned Gunicorn HTTP/1 server.

This module is not a general Gunicorn launcher: it never loads Python config,
application import strings, plugins, command-line options, socket activation, or
proxy configuration. Restart the service to change its explicit configuration.
"""
from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import logging
import os
from pathlib import Path
import signal
import ssl
import stat
import sys


GUNICORN_VERSION = "27.0.1"
MAX_BODY_BYTES = 1024 * 1024


class ServerError(RuntimeError):
    """A deliberately input-free startup or server failure."""

    def __init__(self):
        super().__init__("service_runtime_server_failed")


class _DiscardStream(io.TextIOBase):
    def write(self, text):
        return len(text)

    def flush(self):
        pass


class _FixedLogger:
    """Gunicorn's logging interface, without formatting any supplied object.

    Gunicorn's default error logger includes raw request lines and parser errors.
    Only fixed operational codes reach stderr here; access logging is absent.
    """
    def __init__(self, cfg, stream=None):
        self.cfg = cfg
        self._stream = stream if stream is not None else sys.stderr

    def setup(self, cfg):
        self.cfg = cfg

    def _emit(self, code):
        self._stream.write(code + "\n")
        self._stream.flush()

    def warning(self, *args, **kwargs):
        self._emit("service_runtime_warning")

    def error(self, *args, **kwargs):
        self._emit("service_runtime_error")

    critical = error
    exception = error

    def info(self, *args, **kwargs):
        pass

    debug = info
    access = info
    reopen_files = info
    close_on_exec = info

    def log(self, level, *args, **kwargs):
        self.error()


@contextmanager
def _quiet_libraries():
    # Existing standard logging handlers may retain the original stderr stream.
    # Redirection alone would not protect against their exception/request logs.
    previous = logging.root.manager.disable
    logging.disable(sys.maxsize)
    try:
        yield
    finally:
        logging.disable(previous)


def _check_environment():
    # Even an inherited FD could otherwise make a fixed bind listen publicly.
    # Reject rather than silently alter the launching service's environment.
    blocked = {"LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES", "NOTIFY_SOCKET",
               "SCRIPT_NAME", "PATH_INFO", "WEB_CONCURRENCY", "PORT",
               "FORWARDED_ALLOW_IPS", "SENDFILE"}
    if any(name.startswith("GUNICORN_") or name in blocked for name in os.environ):
        raise ServerError()


def _tls_context(certfile, keyfile):
    for value, private in ((certfile, False), (keyfile, True)):
        path = Path(value)
        if not path.is_absolute():
            raise ServerError()
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise ServerError()
        if info.st_mode & (0o077 if private else 0o022):
            raise ServerError()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.options |= ssl.OP_NO_COMPRESSION
    context.set_alpn_protocols(["http/1.1"])
    # Do not prompt or read a password from stdin for an encrypted private key.
    context.load_cert_chain(str(certfile), str(keyfile), password=lambda: "")
    return context


def _freeze_runtime_signals(server):
    # Gunicorn's management signals can otherwise grow the worker pool, reload
    # config or exec a new process. This assembly deliberately needs a restart.
    for name in ("SIGHUP", "SIGTTIN", "SIGTTOU", "SIGUSR2"):
        signal.signal(getattr(signal, name), signal.SIG_IGN)


def _raw_header_policy(worker, request):
    """Enforce policy on Gunicorn's unmerged header list, before WSGI mapping."""
    from gunicorn.http.errors import InvalidHeader

    def reject():
        # Never put header names, values, targets or credentials in exceptions.
        raise InvalidHeader("request_rejected")

    if request.version != (1, 1) or request.scheme != "https":
        reject()
    # No absolute-form, CONNECT authority-form, or asterisk-form routing.
    if not request.uri.startswith("/") or request.uri.startswith("//"):
        reject()
    seen = set()
    for name, value in request.headers:
        name = name.upper()
        if name in seen or "_" in name:
            reject()
        seen.add(name)
        if (name in {"FORWARDED", "X-REAL-IP", "X-URL-SCHEME", "X-SCHEME",
                     "FRONT-END-HTTPS", "X-SSL", "X-ORIGINAL-URL", "X-REWRITE-URL",
                     "X-SCRIPT-NAME", "SCRIPT-NAME", "PATH-INFO", "TRANSFER-ENCODING",
                     "TE", "TRAILER", "EXPECT", "UPGRADE"}
                or name.startswith(("X-FORWARDED-", "PROXY-"))):
            reject()
        if name == "CONTENT-LENGTH":
            # Gunicorn also checks framing. This additionally bounds bodies and
            # refuses alternate numeric representations before application reads.
            if not value.isascii() or not value.isdecimal() or len(value) > 7:
                reject()
            if str(int(value)) != value or int(value) > MAX_BODY_BYTES:
                reject()
        if name == "CONNECTION" and value.lower() not in ("close", "keep-alive"):
            reject()
    if "HOST" not in seen:
        reject()


def _build_application(application, certfile, keyfile, port, tls_context, diagnostics):
    """Build only from trusted class objects and explicit settings (no CLI)."""
    import gunicorn
    if gunicorn.__version__ != GUNICORN_VERSION:
        raise ServerError()
    from gunicorn.app.base import BaseApplication
    from gunicorn.arbiter import Arbiter
    from gunicorn.config import Config
    from gunicorn.http.errors import (InvalidResponseHeader, InvalidResponseHeaderName,
                                      ParseException)
    from gunicorn import util
    from gunicorn.workers.sync import SyncWorker

    class SafeSyncWorker(SyncWorker):
        def handle_error(self, request, client, address, exc):
            client_error = isinstance(exc, (ParseException, ssl.SSLError)) and not isinstance(
                exc, (InvalidResponseHeader, InvalidResponseHeaderName))
            status, reason = ((400, "Bad Request") if client_error
                              else (500, "Internal Server Error"))
            self.log.warning() if client_error else self.log.error()
            try:
                util.write_error(client, status, reason, "Request rejected")
            except Exception:
                pass

    class SafeLogger(_FixedLogger):
        def __init__(self, cfg):
            super().__init__(cfg, diagnostics)

    class FixedApplication(BaseApplication):
        def do_load_config(self):
            # BaseApplication's default handler prints exception input. We do not.
            self.cfg = Config(prog="ms-collab-service-runtime")
            self.load_config()
            self.cfg.validate()

        def load_config(self):
            settings = {
                "bind": [f"127.0.0.1:{port}"], "workers": 2, "threads": 1,
                "worker_class": SafeSyncWorker, "timeout": 30, "graceful_timeout": 10,
                "keepalive": 0, "backlog": 64, "max_requests": 1000,
                "max_requests_jitter": 100, "preload_app": True,
                "limit_request_line": 4096, "limit_request_fields": 64,
                "limit_request_field_size": 4096, "http_parser": "python",
                "protocol": "http", "http_protocols": "h1", "http2_cleartext": "off",
                "certfile": str(certfile), "keyfile": str(keyfile),
                "ssl_context": lambda config, factory: tls_context,
                "proxy_protocol": "off", "proxy_allow_ips": "",
                "forwarded_allow_ips": "", "forwarder_headers": "",
                "secure_scheme_headers": {}, "header_map": "refuse",
                "permit_obsolete_folding": False, "strip_header_spaces": False,
                "permit_unconventional_http_method": False,
                "permit_unconventional_http_version": False, "casefold_http_method": False,
                "pre_request": _raw_header_policy, "when_ready": _freeze_runtime_signals,
                "accesslog": None, "errorlog": None, "logger_class": SafeLogger,
                "capture_output": False, "logconfig": None, "logconfig_dict": {},
                "logconfig_json": None, "syslog": False, "statsd_host": None,
                "enable_backlog_metric": False, "control_socket_disable": True,
                "control_socket": None, "dirty_apps": [], "dirty_workers": 0,
                "reload": False, "spew": False, "daemon": False, "reuse_port": False,
                "sendfile": False, "raw_env": [], "pythonpath": None,
                "wsgi_app": None, "paste": None, "pidfile": None, "umask": 0o077,
            }
            for name, value in settings.items():
                self.cfg.set(name, value)

        def load(self):
            def guarded(environ, start_response):
                # A WSGI errors stream must not re-enable arbitrary input logs.
                environ["wsgi.errors"] = _DiscardStream()
                return application(environ, start_response)
            return guarded

        def run(self):
            # Unlike BaseApplication.run(), no interpolation of startup errors.
            return Arbiter(self).run()

    return FixedApplication()


def run_server(config_path, certfile, keyfile, port):
    """Run the fixed TLS service; only the master owns/releases its shared lease.

    Call from the standalone CLI, not a multi-threaded embedding: stderr/stdout
    are suppressed while Gunicorn runs because upstream startup and worker-fork
    failures otherwise print exception text outside its logger interface.
    """
    owner_pid = os.getpid()
    application = None
    diagnostics = sys.stderr
    try:
        with _quiet_libraries(), redirect_stdout(_DiscardStream()), redirect_stderr(_DiscardStream()):
            _check_environment()
            if type(port) is not int or not 1 <= port <= 65535:
                raise ServerError()
            context = _tls_context(certfile, keyfile)
            from .runtime import create_application
            application = create_application(config_path)
            server = _build_application(application, certfile, keyfile, port, context, diagnostics)
            server.run()
    except SystemExit as exc:
        # The worker fork unwinds this stack too. Never swallow its exit, and
        # never explicitly unlock a lease shared with the still-running master.
        if os.getpid() != owner_pid:
            raise
        if exc.code not in (0, None):
            raise ServerError() from None
    except Exception:
        raise ServerError() from None
    finally:
        if application is not None and os.getpid() == owner_pid:
            try:
                application.close()
            except Exception:
                raise ServerError() from None
