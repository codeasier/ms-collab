"""Explicit synthetic-only HTTPS fixture. Never a deployable login entry point."""
import json
import os
from pathlib import Path
import signal
import ssl
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode
from wsgiref.simple_server import make_server, WSGIRequestHandler



def main():
    if os.environ.get("MS_COLLAB_SYNTHETIC_HOST_TEST") != "1" or len(sys.argv) != 3:
        raise SystemExit("synthetic test opt-in and temporary certificate required")
    class QuietHTTPS(WSGIRequestHandler):
        def log_message(self, *args): pass
        def get_environ(self):
            env = super().get_environ()
            env["HTTPS"] = "on"
            return env
    with tempfile.TemporaryDirectory(prefix="ms-collab-host-fixture-") as temp:
        server = make_server("127.0.0.1", 0, lambda e, s: [], handler_class=QuietHTTPS)
        origin = "https://127.0.0.1:" + str(server.server_port)
        provider = "https://localhost:" + str(server.server_port) + "/synthetic-provider"
        from packages.application_host.tests.fixtures import setup
        app, policy, login, exchange = setup(temp, origin=origin,
            clock=lambda: datetime.now(timezone.utc), authorization_endpoint=provider)
        binding = login.binding
        def fixture(env, start):
            if env.get("PATH_INFO") == "/":
                body = b"<!doctype html><title>Synthetic unified host service test</title><p>Offline provider and temporary databases only.</p>"
                start("200 OK", [("Content-Type", "text/html"),("Content-Length",str(len(body))), ("Cache-Control","no-store")])
                return [body]
            if env.get("PATH_INFO") == "/synthetic-revoke" and env.get("REQUEST_METHOD") == "POST":
                # Explicit fixture-only external revocation to test stale cookies.
                if env.get("HTTP_ORIGIN") != origin:
                    start("403 Forbidden",[("Content-Length","0")]); return [b""]
                login.revoke_subject("admin-sub")
                start("200 OK",[("Content-Length","0"),("Cache-Control","no-store")]); return [b""]
            if env.get("PATH_INFO") == "/synthetic-provider":
                params = parse_qs(env.get("QUERY_STRING", ""), strict_parsing=True)
                if params.get("code_challenge_method") != ["S256"] or params.get("redirect_uri") != [binding.callback_uri]:
                    start("400 Bad Request",[("Content-Length","0")]); return [b""]
                code = exchange.authorize(params["code_challenge"][0])
                target = binding.callback_uri + "?" + urlencode({"code":code,"state":params["state"][0]})
                start("303 See Other",[("Location",target),("Content-Length","0"),("Cache-Control","no-store"),("Referrer-Policy","no-referrer")])
                return [b""]
            return app(env,start)
        server.set_app(fixture)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(sys.argv[1], sys.argv[2])
        server.socket = context.wrap_socket(server.socket, server_side=True)
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit()))
        print(json.dumps({"origin":origin,"provider":provider}),flush=True)
        try: server.serve_forever()
        finally: server.server_close()

if __name__ == "__main__": main()
