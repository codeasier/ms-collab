"""Synthetic WSGI + SQLite + test bound-code adapter. No live provider or data."""
from datetime import datetime, timedelta, timezone
import io
import json
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from packages.identity import AuthenticationError, AuthenticationUnavailable, Credential, VerifiedIdentity
from packages.login_state import LoginBinding, LoginCoordinator, SQLiteLoginStore, s256_challenge
from packages.session_api import SessionPolicyStore, SessionRequest, SessionWorkspaceApplication
from packages.workspaces import PolicyError, SQLiteWorkspaceService
from packages.workspace_api.tests.test_application import initialize
from packages.session_api.application import _SESSION, _CSRF, _BROWSER

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
ORIGIN = "https://app.example.invalid"
BINDING = LoginBinding("https://identity.example.invalid", "synthetic-client", ORIGIN + "/auth/callback")
BASE = "/api/workspaces/alpha/modules/board/resources/same"


class Clock:
    def __init__(self): self.now = NOW
    def __call__(self): return self.now


class SyntheticExchange:
    def __init__(self, clock): self.clock, self.codes, self.calls = clock, {}, 0
    def authorize(self, challenge, subject="admin-sub"):
        code = "synthetic-" + str(len(self.codes))
        self.codes[code] = (challenge, subject)
        return code
    def exchange_bound_code(self, *, code, verifier, callback_uri):
        self.calls += 1
        record = self.codes.pop(code.value, None)
        if record is None or record[0] != s256_challenge(verifier) or callback_uri != BINDING.callback_uri:
            raise AuthenticationError()
        return VerifiedIdentity(BINDING.issuer, record[1], BINDING.audience,
                                self.clock(), self.clock() + timedelta(minutes=30))


def call(app, path=BASE, *, method="GET", cookies=None, csrf=None, origin=None,
         body=None, query="", extra=None, revision=None):
    data = json.dumps(body).encode() if body is not None else b""
    env = {"wsgi.url_scheme": "https", "HTTP_HOST": "app.example.invalid", "REQUEST_METHOD": method,
           "PATH_INFO": path, "QUERY_STRING": query, "CONTENT_TYPE": "application/json",
           "CONTENT_LENGTH": str(len(data)), "wsgi.input": io.BytesIO(data)}
    if cookies is not None: env["HTTP_COOKIE"] = cookies
    if csrf is not None: env["HTTP_X_CSRF_TOKEN"] = csrf
    if origin is not None: env["HTTP_ORIGIN"] = origin
    if revision is not None: env["HTTP_IF_MATCH"] = revision
    if extra: env.update(extra)
    result = {}
    def start(status, headers):
        result["status"], result["headers"] = int(status.split()[0]), headers
    raw = b"".join(app(env, start))
    result["body"] = json.loads(raw)
    return result


def cookie_values(response):
    return {value.split("=", 1)[0]: value.split("=", 1)[1].split(";", 1)[0]
            for name, value in response["headers"] if name == "Set-Cookie"}


def cookie_header(values): return "; ".join(k + "=" + v for k, v in values.items())


def protected_worker(policy_path, login_path, token, csrf, entered, release, queue):
    try:
        p = SQLiteWorkspaceService(policy_path)
        l = SQLiteLoginStore(login_path, binding=BINDING, clock=lambda: NOW)
        store = SessionPolicyStore(policy=p, login=l)
        def operation(policy, actor):
            entered.set()
            if not release.wait(10): raise RuntimeError("test timeout")
            return policy.update_resource(actor, "alpha", "board", "same", text="committed-before-logout", expected_revision=1).text
        queue.put(store.transaction(SessionRequest(Credential(token), Credential(csrf)), operation, write=True))
    except Exception as error: queue.put(type(error).__name__)


def logout_worker(login_path, token, csrf, ready, done, queue):
    try:
        store = SQLiteLoginStore(login_path, binding=BINDING, clock=lambda: NOW)
        ready.set()
        store.logout(Credential(token), Credential(csrf))
        queue.put("logout")
    except Exception as error: queue.put(type(error).__name__)
    finally: done.set()


class SessionHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.policy_path, self.login_path = Path(self.temp.name) / "policy.sqlite", Path(self.temp.name) / "login.sqlite"
        self.policy = initialize(self.policy_path)
        self.clock = Clock()
        self.login = SQLiteLoginStore.initialize(self.login_path, binding=BINDING, clock=self.clock)
        self.sessions = SessionPolicyStore(policy=self.policy, login=self.login)
        self.exchange = SyntheticExchange(self.clock)
        self.app = SessionWorkspaceApplication(sessions=self.sessions, origin=ORIGIN,
                exchange=self.exchange, authorization_endpoint=BINDING.issuer + "/authorize",
                authorization_scopes=("synthetic_user",))
        self.session = self.make_session()
        self.cookies = cookie_header({_SESSION:self.session.token.value, _CSRF:self.session.csrf.value})

    def make_session(self, subject="admin-sub"):
        c = LoginCoordinator(self.login, exchange=self.exchange)
        attempt = c.begin(); code = self.exchange.authorize(attempt.code_challenge, subject)
        return c.complete(parameters=(("state", attempt.state.value), ("code", code)),
                          browser_binding=attempt.browser_binding, callback_uri=BINDING.callback_uri)

    def request(self, **kwargs):
        return call(self.app, cookies=kwargs.pop("cookies", self.cookies), **kwargs)

    def write(self, **kwargs):
        values = dict(method="PUT", body={"text":"updated"}, revision='"1"',
                      origin=ORIGIN, csrf=self.session.csrf.value)
        values.update(kwargs)
        return self.request(**values)

    def begin_http(self):
        return call(self.app, "/auth/start", method="POST", body={}, origin=ORIGIN,
                    extra={"HTTP_X_MS_COLLAB_LOGIN":"1"})

    def test_existing_read_and_write_routes_use_session(self):
        self.assertEqual(self.request()["body"]["data"]["text"], "Alpha")
        self.assertEqual(self.write()["status"], 200)
        self.assertEqual(self.policy.read_resource("admin", "alpha", "board", "same").text, "updated")
        self.assertEqual(len(self.policy.audit_events("admin", "alpha")), 1)

    def test_synthetic_cookie_flow_sets_scoped_flags_and_no_body_credentials(self):
        start = self.begin_http(); self.assertEqual(start["status"], 200)
        target = start["body"]["data"]["authorization_url"]
        params = parse_qs(urlsplit(target).query)
        self.assertEqual(urlsplit(target).netloc, "identity.example.invalid")
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertEqual(params["scope"], ["synthetic_user"])
        self.assertNotIn("code_verifier", params)
        code = self.exchange.authorize(params["code_challenge"][0])
        browser_cookie = cookie_values(start)
        response = call(self.app, "/auth/callback", query=urlencode({"state":params["state"][0], "code":code}),
                        cookies=cookie_header(browser_cookie), extra={"HTTP_SEC_FETCH_SITE":"cross-site"})
        self.assertEqual(response["status"], 303)
        self.assertEqual(dict(response["headers"])["Location"], "/")
        values = cookie_values(response)
        self.assertEqual(values[_BROWSER], "")
        for name, header in response["headers"]:
            if name != "Set-Cookie": continue
            self.assertIn("; Path=/;", header); self.assertIn("; Secure;", header)
            self.assertNotIn("Domain=", header)
            if header.startswith(_CSRF + "="):
                self.assertNotIn("HttpOnly", header); self.assertIn("SameSite=Strict", header)
            else:
                self.assertIn("HttpOnly", header); self.assertIn("SameSite=Lax", header)
        self.assertNotIn(values[_SESSION], json.dumps(response["body"]))
        self.assertNotIn(values[_CSRF], json.dumps(response["body"]))
        cookies = cookie_header({k:v for k,v in values.items() if v})
        self.assertEqual(call(self.app, cookies=cookies)["status"], 200)
        # Same callback is spent, despite the original pre-login cookie.
        again = call(self.app, "/auth/callback", query=urlencode({"state":params["state"][0], "code":code}),
                     cookies=cookie_header(browser_cookie))
        self.assertEqual(again["status"], 401)

    def test_unconfigured_provider_closed_for_start_and_callback(self):
        app = SessionWorkspaceApplication(sessions=self.sessions, origin=ORIGIN)
        before = self.count("attempts")
        start = call(app, "/auth/start", method="POST", body={}, origin=ORIGIN,
                     extra={"HTTP_X_MS_COLLAB_LOGIN":"1"})
        cb = call(app, "/auth/callback", query="code=synthetic&state=synthetic")
        self.assertEqual((start["status"], cb["status"]), (503, 503))
        self.assertFalse(cookie_values(start)); self.assertFalse(cookie_values(cb))
        self.assertEqual(self.count("attempts"), before)

    def count(self, table):
        with sqlite3.connect(self.login_path) as db: return db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]

    def test_missing_forged_and_bearer_credentials_fail_closed(self):
        for cookies, extra in ((None,{}), (self.cookies,{"HTTP_AUTHORIZATION":"Bearer ignored"}),
                               (cookie_header({_SESSION:"x"*43}),{}),
                               (cookie_header({_SESSION:"admin"}),{"HTTP_X_USER_ID":"admin"})):
            self.assertEqual(self.request(cookies=cookies, extra=extra)["status"], 401)

    def test_exact_https_host_and_origin_no_forwarded_trust(self):
        for extra in ({"wsgi.url_scheme":"http"}, {"HTTP_HOST":"evil.example.invalid"},
                      {"HTTP_HOST":"app.example.invalid.evil"}, {"SCRIPT_NAME":"/prefix"},
                      {"HTTP_ORIGIN":"null"}, {"HTTP_ORIGIN":ORIGIN+".evil"},
                      {"HTTP_SEC_FETCH_SITE":"cross-site"}, {"HTTP_SEC_FETCH_SITE":"same-site"},
                      {"wsgi.url_scheme":"http", "HTTP_X_FORWARDED_PROTO":"https"}):
            with self.subTest(extra=extra): self.assertEqual(self.request(extra=extra)["status"], 401)

    def test_write_requires_origin_header_and_bound_csrf_cookie_header(self):
        for changes in (dict(origin=None), dict(origin="https://evil.example.invalid"),
                        dict(csrf=None), dict(csrf="x"*43), dict(csrf=self.session.token.value),
                        dict(cookies=cookie_header({_SESSION:self.session.token.value})),
                        dict(cookies=cookie_header({_SESSION:self.session.token.value,_CSRF:"x"*43}))):
            with self.subTest(changes=tuple(changes)):
                self.assertEqual(self.write(**changes)["status"], 401)
        self.assertEqual(self.policy.read_resource("admin", "alpha", "board", "same").revision, 1)
        self.assertFalse(self.policy.audit_events("admin", "alpha"))

    def test_duplicate_malformed_and_oversized_cookies_rejected(self):
        for cookies in (self.cookies+"; "+_SESSION+"=other", self.cookies+"; "+_CSRF+"=other",
                        self.cookies+"; malformed", self.cookies+"; q=\"quoted\"", "x="+"a"*8192,
                        self.cookies+"\n", self.cookies+"; x=space here"):
            with self.subTest(length=len(cookies)):
                self.assertEqual(self.request(cookies=cookies)["status"], 401)

    def test_session_response_and_logout_clear_cookies(self):
        info = self.request(path="/auth/session")
        self.assertEqual(info["body"], {"data":{"principal_id":"admin"}})
        self.assertEqual(self.request(path="/auth/logout")["status"], 404)
        self.assertEqual(self.request(path="/auth/logout", method="POST", body={}, origin=ORIGIN)["status"], 401)
        done = self.request(path="/auth/logout", method="POST", body={}, origin=ORIGIN, csrf=self.session.csrf.value)
        self.assertEqual(done["status"], 200)
        self.assertEqual(cookie_values(done), {_SESSION:"",_CSRF:"",_BROWSER:""})
        for key,value in done["headers"]:
            if key == "Set-Cookie": self.assertIn("Max-Age=0", value)
        self.assertEqual(self.request()["status"], 401)

    def test_login_start_rejects_csrf_and_nonempty_body(self):
        for origin, extra, body in ((None,{"HTTP_X_MS_COLLAB_LOGIN":"1"},{}),
                                    (ORIGIN,{},{}), (ORIGIN,{"HTTP_X_MS_COLLAB_LOGIN":"1"},{"next":"//evil"})):
            result = call(self.app,"/auth/start",method="POST",origin=origin,body=body,extra=extra)
            self.assertIn(result["status"], (400,401))
        # Existing authenticated cookie additionally requires its CSRF proof.
        result = self.request(path="/auth/start",method="POST",origin=ORIGIN,body={},extra={"HTTP_X_MS_COLLAB_LOGIN":"1"})
        self.assertEqual(result["status"],401)

    def test_callback_duplicate_unknown_bad_encoding_and_browser_denied(self):
        start = self.begin_http(); p = parse_qs(urlsplit(start["body"]["data"]["authorization_url"]).query)
        code = self.exchange.authorize(p["code_challenge"][0])
        valid = urlencode({"state":p["state"][0],"code":code})
        cookies = cookie_header(cookie_values(start))
        for query in (valid+"&code=other",valid+"&extra=x",valid+"&error=x","state=%GG&code=x", "state=%FF&code=x"):
            self.assertEqual(call(self.app,"/auth/callback",cookies=cookies,query=query)["status"],401)
        self.assertEqual(call(self.app,"/auth/callback",cookies=cookie_header({_BROWSER:"x"*43}),query=valid)["status"],401)
        self.assertEqual(call(self.app,"/auth/callback",cookies=cookies,query=valid)["status"],303)

    def test_cross_workspace_member_revoked_disabled_and_unknown_binding_denied(self):
        self.assertEqual(self.request(path=BASE.replace("alpha", "beta"))["status"],404)
        for subject in ("disabled-sub", "no-local-binding"):
            session = self.make_session(subject)
            self.assertEqual(self.request(cookies=cookie_header({_SESSION:session.token.value}))["status"],401)
        viewer = self.make_session("viewer-sub")
        cookies = cookie_header({_SESSION:viewer.token.value})
        self.assertEqual(self.request(cookies=cookies)["status"],200)
        self.policy.set_membership("admin","alpha","viewer",role="viewer",state="revoked",expected_revision=1)
        self.assertEqual(self.request(cookies=cookies)["status"],404)

    def test_expiry_after_persistence_rolls_back_resource_and_audit_and_keeps_clock_floor(self):
        original = self.policy._persist
        def slow(db, policy, *, audit_start):
            original(db,policy,audit_start=audit_start)
            self.clock.now = self.session.expires_at
        with patch.object(self.policy, "_persist", side_effect=slow):
            self.assertEqual(self.write()["status"],401)
        self.assertEqual(self.policy.read_resource("admin","alpha","board","same").revision,1)
        self.assertFalse(self.policy.audit_events("admin","alpha"))
        self.clock.now = NOW + timedelta(minutes=29)
        self.assertEqual(self.request()["status"],503)

    def test_adapter_commit_guard_is_not_last_security_check(self):
        req = SessionRequest(self.session.token,self.session.csrf)
        def change(policy,actor): return policy.update_resource(actor,"alpha","board","same",text="deny",expected_revision=1)
        with self.assertRaises(AuthenticationError):
            self.sessions.transaction(req,change,write=True,before_commit=lambda:setattr(self.clock,"now",self.session.expires_at))
        self.assertEqual(self.policy.read_resource("admin","alpha","board","same").text,"Alpha")

    def test_session_deleted_inside_shared_transaction_rolls_back_data_and_session(self):
        req = SessionRequest(self.session.token,self.session.csrf)
        def operation(policy,actor,db):
            policy.update_resource(actor,"alpha","board","same",text="deny",expected_revision=1)
            db.execute("DELETE FROM login.sessions")
        with self.assertRaises(AuthenticationError): self.sessions.transaction(req,operation,write=True,with_connection=True)
        self.assertEqual(self.policy.read_resource("admin","alpha","board","same").text,"Alpha")
        self.login.inspect_session(self.session.token)

    def test_raw_transaction_write_also_requires_csrf(self):
        for csrf in (None,Credential("x"*43)):
            with self.assertRaises(AuthenticationError):
                self.sessions.transaction(SessionRequest(self.session.token,csrf),lambda p,a:p.update_resource(a,"alpha","board","same",text="deny",expected_revision=1),write=True)
        self.assertEqual(self.policy.read_resource("admin","alpha","board","same").revision,1)

    def test_sql_failure_rolls_back_main_and_session_side_effects(self):
        req = SessionRequest(self.session.token,self.session.csrf)
        def operation(policy,actor,db):
            policy.update_resource(actor,"alpha","board","same",text="deny",expected_revision=1)
            db.execute("DELETE FROM login.sessions")
            db.execute("SELECT * FROM absent_table")
        with self.assertRaises(AuthenticationUnavailable): self.sessions.transaction(req,operation,write=True,with_connection=True)
        self.assertEqual(self.policy.read_resource("admin","alpha","board","same").revision,1)
        self.login.inspect_session(self.session.token)
        self.assertEqual(self.write()["status"],200)

    def test_wal_rejected_without_changing_mode(self):
        with sqlite3.connect(self.login_path) as db: self.assertEqual(db.execute("PRAGMA journal_mode=WAL").fetchone()[0],"wal")
        self.assertEqual(self.request()["status"],503)
        with sqlite3.connect(self.login_path) as db: self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0],"wal")

    def test_journal_mode_change_between_connect_and_transaction_denied(self):
        connect = self.sessions._connect
        def changed():
            db = connect()
            with sqlite3.connect(self.login_path) as other:
                other.execute("PRAGMA journal_mode=WAL")
            return db
        with patch.object(self.sessions, "_connect", side_effect=changed):
            self.assertEqual(self.write()["status"],503)
        self.assertEqual(self.policy.read_resource("admin","alpha","board","same").revision,1)

    def test_main_wal_rejected(self):
        with sqlite3.connect(self.policy_path) as db: db.execute("PRAGMA journal_mode=WAL")
        self.assertEqual(self.write()["status"],503)

    def test_storage_busy_and_missing_are_generic(self):
        self.policy._timeout=0
        with sqlite3.connect(self.login_path) as db:
            db.execute("BEGIN IMMEDIATE")
            self.assertEqual(self.request()["status"],503)
        self.login_path.unlink()
        self.assertEqual(self.request()["status"],503)
        self.assertFalse(self.login_path.exists())

    def test_clock_rollback_and_unrelated_store_rejected(self):
        self.clock.now = NOW-timedelta(seconds=1)
        self.assertEqual(self.request()["status"],503)
        self.clock.now=NOW
        with sqlite3.connect(self.login_path) as db: db.execute("PRAGMA application_id=0")
        self.assertEqual(self.request()["status"],503)

    def test_response_security_headers_present_on_failures(self):
        result=self.request(cookies=None)
        headers=dict(result["headers"])
        self.assertEqual(headers["Cache-Control"],"no-store")
        self.assertEqual(headers["Referrer-Policy"],"no-referrer")
        self.assertEqual(headers["Vary"],"Cookie, Origin")
        self.assertNotIn("Access-Control-Allow-Origin",headers)
        self.assertNotIn("WWW-Authenticate",headers)

    def test_logout_linearizes_before_next_mutation(self):
        self.login.logout(self.session.token,self.session.csrf)
        self.assertEqual(self.write()["status"],401)
        self.assertEqual(self.policy.read_resource("admin","alpha","board","same").revision,1)

    def test_multiprocess_logout_waits_for_inflight_authorized_commit(self):
        ctx=multiprocessing.get_context("spawn")
        entered,release,ready,done=ctx.Event(),ctx.Event(),ctx.Event(),ctx.Event(); queue=ctx.Queue()
        writer=ctx.Process(target=protected_worker,args=(str(self.policy_path),str(self.login_path),self.session.token.value,self.session.csrf.value,entered,release,queue))
        writer.start()
        logout=None
        try:
            self.assertTrue(entered.wait(10))
            logout=ctx.Process(target=logout_worker,args=(str(self.login_path),self.session.token.value,self.session.csrf.value,ready,done,queue)); logout.start()
            # Opening the login store itself waits for the shared write reservation.
            self.assertFalse(done.wait(.15))
            release.set()
            self.assertEqual({queue.get(timeout=10),queue.get(timeout=10)}, {"committed-before-logout","logout"})
            writer.join(10); logout.join(10)
            self.assertEqual((writer.exitcode,logout.exitcode),(0,0))
            self.assertEqual(self.request()["status"],401)
            self.assertEqual(self.policy.read_resource("admin","alpha","board","same").revision,2)
        finally:
            release.set()
            for process in (writer,logout):
                if process is not None and process.is_alive(): process.terminate();process.join()
            queue.close();queue.join_thread()

    def test_explicit_provider_and_origin_configuration(self):
        for changes in (dict(origin="http://app.example.invalid"),dict(origin=ORIGIN+"/"),
                        dict(authorization_endpoint=BINDING.issuer+"/authorize"),
                        dict(exchange=self.exchange),dict(exchange=self.exchange,authorization_endpoint="http://id.example.invalid/authorize"),
                        dict(exchange=self.exchange,authorization_endpoint=BINDING.issuer+"/authorize",authorization_scopes=())):
            values=dict(sessions=self.sessions,origin=ORIGIN);values.update(changes)
            with self.subTest(keys=tuple(changes)),self.assertRaises(ValueError): SessionWorkspaceApplication(**values)

if __name__=="__main__":unittest.main()
