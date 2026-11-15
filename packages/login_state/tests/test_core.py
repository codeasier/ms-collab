"""Synthetic, offline authorization-server double. No live GitCode or secrets."""
from datetime import datetime, timedelta, timezone
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from packages.identity import AuthenticationError, AuthenticationUnavailable, Credential, VerifiedIdentity
from packages.gitcode_identity import GitCodeConfig, GitCodeOAuthClient
from packages.login_state import LoginBinding, LoginCoordinator, SQLiteLoginStore, s256_challenge

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
BINDING = LoginBinding("https://identity.example.invalid", "synthetic-client", "https://app.example.invalid/callback")


class Clock:
    def __init__(self): self.now = NOW
    def __call__(self): return self.now


class BoundServer:
    """Test AS binds each code to S256 and callback; not a production adapter."""
    def __init__(self, clock=lambda: NOW):
        self.clock, self.codes, self.calls = clock, {}, 0
        self.failure = None
        self.change = None

    def authorize(self, attempt, subject="synthetic-subject"):
        code = "synthetic-code-" + str(len(self.codes))
        self.codes[code] = (attempt.code_challenge, subject)
        return code

    def exchange_bound_code(self, *, code, verifier, callback_uri):
        self.calls += 1
        if self.failure: raise self.failure
        record = self.codes.pop(code.value, None)
        if record is None or record[0] != s256_challenge(verifier) or callback_uri != BINDING.callback_uri:
            raise AuthenticationError()
        if self.change: self.change()
        now = self.clock()
        return VerifiedIdentity(BINDING.issuer, record[1], BINDING.audience, now, now + timedelta(hours=1))


def consume_process(path, state, browser, queue):
    try:
        store = SQLiteLoginStore(path, binding=BINDING, clock=lambda: NOW)
        store._consume(Credential(state), Credential(browser), BINDING.callback_uri)
        queue.put("won")
    except AuthenticationError:
        queue.put("denied")
    except Exception:
        queue.put("unexpected")


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic.sqlite"
        self.clock = Clock()
        self.store = SQLiteLoginStore.initialize(self.path, binding=BINDING, clock=self.clock)
        self.server = BoundServer(self.clock)
        self.coordinator = LoginCoordinator(self.store, exchange=self.server)

    def begin(self):
        attempt = self.coordinator.begin()
        code = self.server.authorize(attempt)
        return attempt, code

    def complete(self, attempt, code, **changes):
        args = dict(parameters=(("state", attempt.state.value), ("code", code)),
                    browser_binding=attempt.browser_binding, callback_uri=BINDING.callback_uri)
        args.update(changes)
        return self.coordinator.complete(**args)

    def session(self):
        attempt, code = self.begin()
        return self.complete(attempt, code)

    def count(self, table):
        with sqlite3.connect(self.path) as db:
            return db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]

    def test_s256_known_rfc7636_vector(self):
        self.assertEqual(s256_challenge(Credential("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")),
                         "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")
        for bad in ("short", "x" * 129, "x" * 43 + "/", "汉" * 43):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                s256_challenge(Credential(bad))

    def test_random_attempt_is_minimal_and_unique(self):
        one, two = self.store.begin(), self.store.begin()
        for value in (one.state, one.browser_binding):
            self.assertRegex(value.value, r"^[A-Za-z0-9_-]{43}$")
        self.assertNotEqual(one.state, two.state)
        self.assertNotEqual(one.browser_binding, two.browser_binding)
        self.assertNotEqual(one.state, one.browser_binding)
        self.assertEqual(one.code_challenge_method, "S256")
        self.assertFalse(hasattr(one, "code_verifier"))
        self.assertNotIn(one.state.value, repr(one))
        self.assertNotIn(one.browser_binding.value, repr(one))

    def test_default_coordinator_closed_without_writes(self):
        coordinator = LoginCoordinator(self.store)
        with self.assertRaises(AuthenticationUnavailable): coordinator.begin()
        with self.assertRaises(AuthenticationUnavailable):
            coordinator.complete(parameters=(), browser_binding=Credential("x" * 43), callback_uri=BINDING.callback_uri)
        self.assertEqual(self.count("attempts"), 0)
        self.assertEqual(self.count("sessions"), 0)

    def test_gitcode_protocol_client_cannot_enable_login(self):
        config = GitCodeConfig("synthetic", "synthetic-secret", BINDING.callback_uri, ("synthetic_scope",))
        with self.assertRaises(ValueError):
            LoginCoordinator(self.store, exchange=GitCodeOAuthClient(config))

    def test_complete_creates_application_bound_session(self):
        session = self.session()
        self.assertEqual(session.expires_at, NOW + timedelta(minutes=30))
        snapshot = self.store.inspect_session(session.token)
        self.assertEqual((snapshot.issuer, snapshot.subject, snapshot.audience),
                         (BINDING.issuer, "synthetic-subject", BINDING.audience))
        self.assertEqual(self.store.check_csrf(session.token, session.csrf), snapshot)
        self.assertFalse(hasattr(snapshot, "role"))
        self.assertFalse(hasattr(snapshot, "verify"))
        self.assertNotIn(session.token.value, repr(session))
        self.assertNotIn(session.csrf.value, repr(session))

    def test_raw_state_browser_session_csrf_not_persisted(self):
        attempt, code = self.begin()
        with sqlite3.connect(self.path) as db:
            dump = "\n".join(db.iterdump())
        self.assertNotIn(attempt.state.value, dump)
        self.assertNotIn(attempt.browser_binding.value, dump)
        session = self.complete(attempt, code)
        with sqlite3.connect(self.path) as db:
            dump = "\n".join(db.iterdump())
        for secret in (session.token.value, session.csrf.value, code):
            self.assertNotIn(secret, dump)
        self.assertEqual(self.count("attempts"), 0)

    def test_state_and_browser_both_required(self):
        attempt, code = self.begin()
        for changes in (dict(browser_binding=Credential("x" * 43)),
                        dict(parameters=(("state", "x" * 43), ("code", code))),
                        dict(browser_binding=attempt.browser_binding.value)):
            with self.subTest(changes=tuple(changes)), self.assertRaises(AuthenticationError):
                self.complete(attempt, code, **changes)
        self.assertEqual(self.server.calls, 0)
        self.assertEqual(self.count("attempts"), 1)
        self.complete(attempt, code)

    def test_wrong_callback_never_exchanges_or_consumes(self):
        attempt, code = self.begin()
        for uri in ("https://evil.example.invalid/callback", BINDING.callback_uri + "?x=y", "http://app.example.invalid/callback"):
            with self.subTest(uri=uri), self.assertRaises(AuthenticationError):
                self.complete(attempt, code, callback_uri=uri)
        self.assertEqual(self.server.calls, 0)
        self.complete(attempt, code)

    def test_replay_denied(self):
        attempt, code = self.begin()
        self.complete(attempt, code)
        with self.assertRaises(AuthenticationError): self.complete(attempt, code)
        self.assertEqual(self.server.calls, 1)
        self.assertEqual(self.count("sessions"), 1)

    def test_duplicate_and_unknown_callback_parameters_rejected(self):
        attempt, code = self.begin()
        base = (("state", attempt.state.value), ("code", code))
        bad = (dict(base), list(base), base + (("code", "other"),), base + (("state", "other"),),
               base + (("extra", "x"),), base + (("error", "denied"),), (("code", code),),
               (("state", attempt.state.value), ("code", "")), (("state", attempt.state.value), ("code", "x" * 8193)))
        for params in bad:
            with self.subTest(type=type(params).__name__), self.assertRaises(AuthenticationError):
                self.complete(attempt, code, parameters=params)
        self.assertEqual(self.server.calls, 0)
        self.complete(attempt, code)

    def test_provider_error_consumes_and_suppresses_details(self):
        attempt, code = self.begin()
        params = (("state", attempt.state.value), ("error", "access_denied"),
                  ("error_description", "sensitive-synthetic-detail"), ("error_uri", "https://evil.example.invalid"))
        with self.assertRaisesRegex(AuthenticationError, "^authentication_required$"):
            self.complete(attempt, code, parameters=params)
        self.assertEqual(self.server.calls, 0)
        with self.assertRaises(AuthenticationError): self.complete(attempt, code)

    def test_failed_exchange_consumed_before_network(self):
        for error, expected in ((RuntimeError("secret-locals"), AuthenticationUnavailable),
                                (AuthenticationError(), AuthenticationError)):
            attempt, code = self.begin()
            self.server.failure = error
            with self.assertRaises(expected) as cm:
                self.complete(attempt, code)
            self.assertNotIn("secret-locals", str(cm.exception))
            self.assertTrue(cm.exception.__suppress_context__)
            self.server.failure = None
            with self.assertRaises(AuthenticationError): self.complete(attempt, code)
        self.assertEqual(self.count("sessions"), 0)

    def test_injected_code_from_other_browser_rejected_by_s256(self):
        victim, victim_code = self.begin()
        attacker, _ = self.begin()
        with self.assertRaises(AuthenticationError): self.complete(attacker, victim_code)
        self.assertEqual(self.count("sessions"), 0)
        with self.assertRaises(AuthenticationError): self.complete(attacker, victim_code)

    def test_unbound_code_downgrade_rejected_by_adapter(self):
        attempt, _ = self.begin()
        self.server.codes["unbound"] = (None, "attacker")
        with self.assertRaises(AuthenticationError): self.complete(attempt, "unbound")
        self.assertEqual(self.count("sessions"), 0)

    def test_attempt_expiry_and_clock_rollback_fail_closed(self):
        attempt, code = self.begin()
        self.clock.now += timedelta(minutes=5)
        with self.assertRaises(AuthenticationError): self.complete(attempt, code)
        self.assertEqual(self.server.calls, 0)
        self.clock.now = NOW - timedelta(microseconds=1)
        with self.assertRaises(AuthenticationUnavailable): self.store.begin()

    def test_slow_exchange_cannot_outlive_callback_transaction(self):
        attempt, code = self.begin()
        self.server.change = lambda: setattr(self.clock, "now", NOW + timedelta(minutes=5))
        with self.assertRaises(AuthenticationError): self.complete(attempt, code)
        self.assertEqual(self.count("sessions"), 0)

    def test_wrong_issuer_audience_and_stale_assertion_rejected(self):
        variants = (("https://wrong.example.invalid", BINDING.audience, NOW),
                    (BINDING.issuer, "wrong", NOW), (BINDING.issuer, BINDING.audience, NOW - timedelta(seconds=1)),
                    (BINDING.issuer, BINDING.audience, NOW + timedelta(seconds=1)))
        for issuer, audience, issued in variants:
            attempt, code = self.begin()
            class Bad:
                def exchange_bound_code(self, **kwargs):
                    return VerifiedIdentity(issuer, "synthetic-subject", audience, issued, NOW + timedelta(hours=1))
            coordinator = LoginCoordinator(self.store, exchange=Bad())
            with self.subTest(issuer=issuer, audience=audience, issued=issued), self.assertRaises(AuthenticationError):
                coordinator.complete(parameters=(("state", attempt.state.value), ("code", code)),
                                     browser_binding=attempt.browser_binding, callback_uri=BINDING.callback_uri)
        self.assertEqual(self.count("sessions"), 0)

    def test_session_never_outlives_upstream_assertion(self):
        attempt, code = self.begin()
        class Short:
            def exchange_bound_code(self, **kwargs):
                return VerifiedIdentity(BINDING.issuer, "u", BINDING.audience, NOW, NOW + timedelta(seconds=5))
        c = LoginCoordinator(self.store, exchange=Short())
        s = c.complete(parameters=(("state", attempt.state.value), ("code", code)),
                       browser_binding=attempt.browser_binding, callback_uri=BINDING.callback_uri)
        self.assertEqual(s.expires_at, NOW + timedelta(seconds=5))
        self.clock.now = s.expires_at
        with self.assertRaises(AuthenticationError): self.store.inspect_session(s.token)

    def test_invalid_provider_result_denied(self):
        attempt, code = self.begin()
        class Bad:
            def exchange_bound_code(self, **kwargs): return {"subject": "u"}
        c = LoginCoordinator(self.store, exchange=Bad())
        with self.assertRaises(AuthenticationError):
            c.complete(parameters=(("state", attempt.state.value), ("code", code)),
                       browser_binding=attempt.browser_binding, callback_uri=BINDING.callback_uri)

    def test_rotation_revokes_prior_session_and_rotates_csrf(self):
        old = self.session()
        attempt, code = self.begin()
        new = self.complete(attempt, code, previous_session=old.token)
        self.assertNotEqual(new.token, old.token)
        self.assertNotEqual(new.csrf, old.csrf)
        with self.assertRaises(AuthenticationError): self.store.inspect_session(old.token)
        with self.assertRaises(AuthenticationError): self.store.check_csrf(new.token, old.csrf)
        self.store.inspect_session(new.token)

    def test_logout_requires_csrf_and_is_immediate_on_reopen(self):
        session = self.session()
        other = SQLiteLoginStore(self.path, binding=BINDING, clock=self.clock)
        for csrf in (None, Credential("x" * 43), session.token):
            with self.subTest(csrf_type=type(csrf)), self.assertRaises(AuthenticationError):
                self.store.logout(session.token, csrf)
        other.inspect_session(session.token)
        self.store.logout(session.token, session.csrf)
        with self.assertRaises(AuthenticationError): other.inspect_session(session.token)

    def test_revoke_subject_all_sessions_not_other_subject(self):
        one, two = self.session(), self.session()
        attempt = self.coordinator.begin()
        code = self.server.authorize(attempt, "other-subject")
        other = self.complete(attempt, code)
        self.store.revoke_subject("synthetic-subject")
        for token in (one.token, two.token):
            with self.assertRaises(AuthenticationError): self.store.inspect_session(token)
        self.store.inspect_session(other.token)

    def test_reopened_store_keeps_pending_and_sessions(self):
        attempt, code = self.begin()
        reopened = SQLiteLoginStore(self.path, binding=BINDING, clock=self.clock)
        c = LoginCoordinator(reopened, exchange=self.server)
        session = c.complete(parameters=(("state", attempt.state.value), ("code", code)),
                             browser_binding=attempt.browser_binding, callback_uri=BINDING.callback_uri)
        self.store.inspect_session(session.token)

    def test_expiry_during_session_read_denied(self):
        session = self.session()
        times = iter((NOW, session.expires_at))
        self.store._clock = lambda: next(times)
        with self.assertRaises(AuthenticationError): self.store.inspect_session(session.token)

    def test_reading_session_does_not_slide_expiry(self):
        session = self.session()
        self.clock.now += timedelta(minutes=29)
        self.assertEqual(self.store.inspect_session(session.token).expires_at, session.expires_at)
        self.clock.now += timedelta(minutes=1)
        with self.assertRaises(AuthenticationError): self.store.check_csrf(session.token, session.csrf)

    def test_existing_file_missing_file_wrong_schema_not_recreated(self):
        before = self.path.read_bytes()
        with self.assertRaises(AuthenticationUnavailable):
            SQLiteLoginStore.initialize(self.path, binding=BINDING, clock=self.clock)
        self.assertEqual(self.path.read_bytes(), before)
        missing = Path(self.temp.name) / "missing.sqlite"
        with self.assertRaises(AuthenticationUnavailable): SQLiteLoginStore(missing, binding=BINDING, clock=self.clock)
        self.assertFalse(missing.exists())
        wrong = Path(self.temp.name) / "wrong.sqlite"
        with sqlite3.connect(wrong) as db: db.execute("CREATE TABLE x (id INTEGER)")
        with self.assertRaises(AuthenticationUnavailable): SQLiteLoginStore(wrong, binding=BINDING, clock=self.clock)

    def test_binding_and_config_changes_fail_closed(self):
        for changes in (dict(audience="other"), dict(issuer="https://other.example.invalid"),
                        dict(callback_uri="https://other.example.invalid/cb"), dict(session_ttl=timedelta(minutes=5))):
            values = vars(BINDING) | changes
            with self.subTest(changes=changes), self.assertRaises(AuthenticationUnavailable):
                SQLiteLoginStore(self.path, binding=LoginBinding(**values), clock=self.clock)

    def test_malformed_stored_session_denied(self):
        session = self.session()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE sessions SET expires=created+7200000000")
        with self.assertRaises(AuthenticationError): self.store.inspect_session(session.token)

    def test_sqlite_busy_is_generic_and_never_returns_session(self):
        session = self.session()
        other = SQLiteLoginStore(self.path, binding=BINDING, clock=self.clock, timeout=0)
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(AuthenticationUnavailable, "^authentication_unavailable$"):
                other.inspect_session(session.token)

    def test_clock_error_sanitized(self):
        for value in (datetime(2026, 1, 1), None):
            self.store._clock = lambda: value
            with self.assertRaisesRegex(AuthenticationUnavailable, "^authentication_unavailable$"):
                self.store.begin()

    def test_capacity_and_expired_cleanup(self):
        path = Path(self.temp.name) / "bounded.sqlite"
        b = LoginBinding(**(vars(BINDING) | dict(max_pending=1, max_sessions=1)))
        store = SQLiteLoginStore.initialize(path, binding=b, clock=self.clock)
        c = LoginCoordinator(store, exchange=self.server)
        a = c.begin()
        with self.assertRaises(AuthenticationUnavailable): c.begin()
        code = self.server.authorize(a)
        s = c.complete(parameters=(("state", a.state.value), ("code", code)), browser_binding=a.browser_binding, callback_uri=b.callback_uri)
        a = c.begin(); code = self.server.authorize(a)
        with self.assertRaises(AuthenticationUnavailable):
            c.complete(parameters=(("state", a.state.value), ("code", code)), browser_binding=a.browser_binding, callback_uri=b.callback_uri)
        self.clock.now = s.expires_at
        a = c.begin(); code = self.server.authorize(a)
        c.complete(parameters=(("state", a.state.value), ("code", code)), browser_binding=a.browser_binding, callback_uri=b.callback_uri)

    def test_multiprocess_single_consumer(self):
        attempt, _ = self.begin()
        ctx = multiprocessing.get_context("spawn")
        queue = ctx.Queue()
        ps = [ctx.Process(target=consume_process, args=(str(self.path), attempt.state.value,
              attempt.browser_binding.value, queue)) for _ in range(2)]
        for p in ps: p.start()
        try:
            results = [queue.get(timeout=10) for _ in ps]
            for p in ps:
                p.join(10)
                self.assertEqual(p.exitcode, 0)
            self.assertEqual(sorted(results), ["denied", "won"])
        finally:
            for p in ps:
                if p.is_alive(): p.terminate(); p.join()
            queue.close(); queue.join_thread()

    def test_configuration_rejects_insecure_and_unbounded_values(self):
        for changes in (dict(callback_uri="http://app.example.invalid/callback"),
                        dict(callback_uri="https://user@app.example.invalid/callback"),
                        dict(callback_uri="https://app.example.invalid/callback?next=x"),
                        dict(transaction_ttl=timedelta(minutes=11)), dict(session_ttl=timedelta(0)),
                        dict(max_pending=True), dict(max_sessions=0)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                LoginBinding(**(vars(BINDING) | changes))
        for timeout in (True, -1, 31, float("nan"), "5"):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                SQLiteLoginStore(self.path, binding=BINDING, timeout=timeout)

    def test_session_and_csrf_tokens_not_interchangeable(self):
        session = self.session()
        for token in (session.csrf, Credential("x" * 43), session.token.value, None):
            with self.subTest(type=type(token)), self.assertRaises(AuthenticationError):
                self.store.inspect_session(token)
        with self.assertRaises(AuthenticationError): self.store.check_csrf(session.token, None)

    def test_expired_at_begin_commit_returns_no_attempt(self):
        times = iter((NOW, NOW + timedelta(minutes=5)))
        self.store._clock = lambda: next(times)
        with self.assertRaises(AuthenticationError): self.store.begin()
        self.assertEqual(self.count("attempts"), 0)

    def test_clock_rolls_back_during_read_fails_closed(self):
        session = self.session()
        times = iter((NOW, NOW - timedelta(microseconds=1)))
        self.store._clock = lambda: next(times)
        with self.assertRaises(AuthenticationUnavailable): self.store.inspect_session(session.token)

    def test_corrupt_pkce_verifier_never_exchanges(self):
        attempt, code = self.begin()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE attempts SET verifier='short'")
        with self.assertRaises(AuthenticationError): self.complete(attempt, code)
        self.assertEqual(self.server.calls, 0)

    def test_denied_expiry_advances_clock_floor_and_cannot_resurrect(self):
        session = self.session()
        self.clock.now = session.expires_at
        with self.assertRaises(AuthenticationError): self.store.inspect_session(session.token)
        self.clock.now = NOW + timedelta(minutes=29)
        with self.assertRaises(AuthenticationUnavailable): self.store.inspect_session(session.token)
        with self.assertRaises(AuthenticationUnavailable):
            SQLiteLoginStore(self.path, binding=BINDING, clock=self.clock)

    def test_failed_commit_deadline_advances_floor_without_issuing_session(self):
        attempt, code = self.begin()
        times = iter((NOW, NOW, NOW + timedelta(minutes=4, seconds=59), NOW + timedelta(minutes=5, seconds=1)))
        self.store._clock = lambda: next(times)
        with self.assertRaises(AuthenticationError): self.complete(attempt, code)
        self.assertEqual(self.count("attempts"), 0)
        self.assertEqual(self.count("sessions"), 0)
        self.store._clock = lambda: NOW + timedelta(minutes=4, seconds=59)
        with self.assertRaises(AuthenticationUnavailable): self.store.begin()

    def test_expired_attempt_does_not_revive_after_clock_rollback(self):
        attempt, code = self.begin()
        self.clock.now = attempt.expires_at
        with self.assertRaises(AuthenticationError): self.complete(attempt, code)
        self.clock.now = NOW + timedelta(minutes=4)
        with self.assertRaises(AuthenticationUnavailable): self.complete(attempt, code)
        self.assertEqual(self.server.calls, 0)

    def test_revoke_subject_is_not_an_account_disable_gate(self):
        self.session()
        attempt, code = self.begin()
        self.server.change = lambda: self.store.revoke_subject("synthetic-subject")
        session = self.complete(attempt, code)
        self.assertEqual(self.count("sessions"), 1)
        self.store.inspect_session(session.token)

    def test_initialize_permissions_private(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__": unittest.main()
