# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Synthetic real-TLS/CLI acceptance for an ephemeral GitHub-hosted runner.

Run: python -m packages.service_runtime.tests.https_acceptance
No live provider, credentials, outbound application traffic, OS trust changes,
public listener, browser profile or persistent artifact is involved. Output is
fixed pass/fail text, never captured subprocess logs or certificate material.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[3]
MARKER = "synthetic-sensitive-marker-not-a-credential"


def require(value):
    if not value:
        raise AssertionError("synthetic_https_acceptance_failed")


def cli(config, *args, env):
    return subprocess.run([sys.executable, "-m", "packages.service_runtime", "--config", str(config), *args],
                          cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)


def exchange(port, context, raw):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as plain:
        with context.wrap_socket(plain, server_hostname="localhost") as connection:
            require(connection.version() in {"TLSv1.2", "TLSv1.3"})
            connection.sendall(raw)
            chunks = []
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                require(sum(map(len, chunks)) <= 1024 * 1024)
    response = b"".join(chunks)
    require(response.startswith(b"HTTP/1.1 "))
    return int(response.split(b" ", 2)[1]), response


def request(port, context, path="/", method="GET", headers=(), body=b""):
    lines = [f"{method} {path} HTTP/1.1", f"Host: localhost:{port}", "Connection: close"]
    lines.extend(f"{name}: {value}" for name, value in headers)
    if body:
        lines.append(f"Content-Length: {len(body)}")
    return exchange(port, context, ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body)


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            # Isolated synthetic process group only, never an existing service.
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def acceptance():
    # Retain only interpreter/tool paths needed for this synthetic installation.
    env = {key: os.environ[key] for key in ("PATH", "PYTHONPATH") if key in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with tempfile.TemporaryDirectory(prefix="ms-collab-https-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        cert, key = root / "certificate.pem", root / "private-key.pem"
        generated = subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes", "-days", "1",
            "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
            "-keyout", str(key), "-out", str(cert),
        ], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        require(generated.returncode == 0)
        key.chmod(0o600); cert.chmod(0o600)
        # Reserve an ephemeral loopback candidate, then let the real server bind.
        # A competing bind causes a test failure, never fallback to another host.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        config = root / "runtime.json"
        config.write_text(json.dumps({
            "version": 1, "origin": f"https://localhost:{port}", "data_directory": str(root / "data"),
            "modules": ["pipelines"], "artifact_administration": False,
            "login": {"mode": "disabled", "issuer": "https://identity.example.invalid", "audience": "synthetic-client"},
        }))
        config.chmod(0o600)
        initialized = cli(config, "init", env=env)
        require(initialized.returncode == 0)
        require(json.loads(initialized.stdout)["login_enabled"] is False)
        serve = ["serve", "--certfile", str(cert), "--keyfile", str(key), "--port", str(port)]
        # Ambient server options are refused, including Python config execution.
        injected = root / "unexpected-import.py"
        injected.write_text("raise AssertionError('" + MARKER + "')")
        bad_env = dict(env, GUNICORN_CMD_ARGS="--config " + str(injected))
        blocked = cli(config, *serve, env=bad_env)
        require(blocked.returncode != 0 and MARKER.encode() not in blocked.stdout + blocked.stderr)
        process = subprocess.Popen([sys.executable, "-m", "packages.service_runtime", "--config", str(config), *serve],
                                   cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        context = ssl.create_default_context(cafile=str(cert))  # Scoped trust only.
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.set_alpn_protocols(["http/1.1"])
        try:
            ready = False
            for _ in range(100):
                require(process.poll() is None)
                try:
                    status, _ = request(port, context)
                    if status == 200:
                        ready = True
                        break
                except (ConnectionError, TimeoutError, ssl.SSLError, OSError):
                    pass
                time.sleep(0.1)
            require(ready)
            status, bootstrap = request(port, context, "/app/bootstrap")
            require(status == 200 and b'"login_configured":false' in bootstrap)
            status, _ = request(port, context, "/api/workspaces")
            require(status == 401)
            status, disabled = request(port, context, "/auth/start", "POST", (
                ("Origin", f"https://localhost:{port}"), ("X-MS-Collab-Login", "1"),
                ("Content-Type", "application/json")), b"{}")
            require(status == 503 and b"set-cookie:" not in disabled.lower())
            status, _ = request(port, context, "/", headers=(("Origin", "https://other.example.invalid"),))
            require(status == 401)
            status, _ = exchange(port, context, b"GET / HTTP/1.1\r\nHost: other.example.invalid\r\nConnection: close\r\n\r\n")
            require(status == 401)
            # The master must retain the shared lease across worker requests.
            busy = cli(config, "backup", "--directory", str(root / "backup"), env=env)
            require(busy.returncode != 0 and json.loads(busy.stdout)["error"] == "runtime_busy")
            for name in ("Origin", "Cookie", "Authorization", "X-CSRF-Token", "Sec-Fetch-Site", "If-Match"):
                status, response = request(port, context, "/?query=" + MARKER,
                                           headers=((name, MARKER), (name.lower(), MARKER)))
                require(status == 400 and MARKER.encode() not in response)
            for name in ("Forwarded", "X-Forwarded-Proto", "X-Forwarded-For", "X-Real-IP", "X_Forwarded_Proto"):
                status, response = request(port, context, "/?query=" + MARKER, headers=((name, MARKER),))
                require(status == 400 and MARKER.encode() not in response)
            for headers in (
                b"Content-Length: 0\r\nContent-Length: 0\r\n",
                b"Content-Length: 00\r\n",
                b"Content-Length: 0\r\nTransfer-Encoding: chunked\r\n",
                b"Transfer-Encoding: chunked\r\nTransfer-Encoding: chunked\r\n",
                b"Transfer-Encoding: chunked\r\n", b"Cookie: " + b"x" * 4096 + b"\r\n",
            ):
                raw = f"POST /?query={MARKER} HTTP/1.1\r\nHost: localhost:{port}\r\n".encode() + headers + b"\r\n0\r\n\r\n"
                status, response = exchange(port, context, raw)
                require(status == 400 and MARKER.encode() not in response)
            for raw in (
                f"BAD {MARKER} HTTP/1.1\r\nHost: localhost:{port}\r\n\r\n".encode(),
                f"PROXY TCP4 127.0.0.1 127.0.0.1 10000 {port}\r\nGET / HTTP/1.1\r\nHost: localhost:{port}\r\n\r\n".encode(),
            ):
                status, response = exchange(port, context, raw)
                require(status == 400 and MARKER.encode() not in response)
            # Plain HTTP cannot reach the WSGI application through this listener.
            with socket.create_connection(("127.0.0.1", port), timeout=3) as plain:
                plain.sendall(f"GET / HTTP/1.1\r\nHost: localhost:{port}\r\n\r\n".encode())
                try:
                    received = plain.recv(1024)
                except (ConnectionError, TimeoutError):
                    received = b""
                require(b"200 OK" not in received)
            # Invalid clients must not poison the next valid TLS request.
            require(request(port, context)[0] == 200)
        finally:
            stop(process)
            output, errors = process.communicate(timeout=5)
            require(MARKER.encode() not in output + errors)
            require(b"Traceback" not in output + errors)
        require(process.returncode == 0)
        backed_up = cli(config, "backup", "--directory", str(root / "after-stop-backup"), env=env)
        require(backed_up.returncode == 0)  # Normal shutdown released the lease.


def main():
    try:
        acceptance()
    except Exception:
        print("synthetic_https_acceptance_failed", file=sys.stderr)
        return 1
    print("synthetic_https_acceptance_passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
