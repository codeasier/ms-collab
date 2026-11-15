"""Synthetic verifier doubles exist only in tests; they never ship as a provider."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import traceback
import unittest

from packages.identity import (AuthenticationError, AuthenticationUnavailable, Authenticator,
    Credential, Provider, StaticPrincipalDirectory, VerifiedIdentity)
from packages.workspaces import Membership, ModuleInstance, PolicyError, Principal, Workspace, WorkspaceService

NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
ISSUER = "https://identity.example.invalid/tenant-a"
AUDIENCE = "collaboration-test"
SUBJECT = "external-42"
PRINCIPAL = Principal("local-7", ISSUER, SUBJECT)


def assertion(**changes):
    return replace(VerifiedIdentity(ISSUER, SUBJECT, AUDIENCE, NOW - timedelta(seconds=10),
                                   NOW + timedelta(seconds=60)), **changes)


class TestVerifier:
    def __init__(self, result=None, error=None):
        self.result = assertion() if result is None else result
        self.error = error
        self.calls = []

    def verify(self, credential):
        self.calls.append(credential)
        if self.error:
            raise self.error
        return self.result


class ChangingDirectory:
    def __init__(self):
        self.current = PRINCIPAL
        self.calls = 0

    def resolve(self, issuer, subject):
        self.calls += 1
        return self.current


class BoundaryTests(unittest.TestCase):
    def auth(self, verifier=None, directory=None, clock=None, providers=None):
        return Authenticator(providers=providers if providers is not None else
            (Provider("test", ISSUER, AUDIENCE, verifier or TestVerifier()),),
            directory=directory or StaticPrincipalDirectory((PRINCIPAL,)), clock=clock or (lambda: NOW))

    def run_auth(self, auth):
        return auth.authenticate("test", Credential("synthetic-input"))

    def test_explicit_binding_returns_local_principal(self):
        self.assertEqual(self.run_auth(self.auth()), PRINCIPAL)

    def test_verifier_receives_opaque_credential(self):
        verifier = TestVerifier()
        self.run_auth(self.auth(verifier=verifier))
        self.assertEqual(verifier.calls, [Credential("synthetic-input")])

    def test_no_configured_provider_denies(self):
        with self.assertRaises(AuthenticationError):
            self.run_auth(self.auth(providers=()))

    def test_unknown_provider_never_calls_verifier(self):
        verifier = TestVerifier()
        with self.assertRaises(AuthenticationError):
            self.auth(verifier).authenticate("https://untrusted.invalid", Credential("synthetic-input"))
        self.assertEqual(verifier.calls, [])

    def test_plain_claim_dictionary_cannot_authenticate(self):
        with self.assertRaises(AuthenticationError):
            self.auth().authenticate("test", {"subject": SUBJECT, "role": "admin"})

    def test_principal_id_is_not_a_credential(self):
        with self.assertRaises(AuthenticationError):
            self.auth().authenticate("test", "local-7")

    def test_untyped_verifier_claims_denied(self):
        with self.assertRaises(AuthenticationError):
            self.run_auth(self.auth(TestVerifier({"issuer": ISSUER, "sub": SUBJECT})))

    def test_issuer_mismatch_denied(self):
        with self.assertRaises(AuthenticationError):
            self.run_auth(self.auth(TestVerifier(assertion(issuer="https://other.example.invalid"))))

    def test_issuer_is_exact_not_normalized(self):
        with self.assertRaises(AuthenticationError):
            self.run_auth(self.auth(TestVerifier(assertion(issuer=ISSUER + "/"))))

    def test_audience_mismatch_denied(self):
        with self.assertRaises(AuthenticationError):
            self.run_auth(self.auth(TestVerifier(assertion(audience="another-app"))))

    def test_unknown_subject_is_not_auto_provisioned(self):
        with self.assertRaises(AuthenticationError):
            self.run_auth(self.auth(TestVerifier(assertion(subject="unknown"))))

    def test_disabled_principal_denied(self):
        with self.assertRaises(AuthenticationError):
            self.run_auth(self.auth(directory=StaticPrincipalDirectory((replace(PRINCIPAL, active=False),))))

    def test_directory_wrong_identity_denied(self):
        directory = ChangingDirectory()
        directory.current = replace(PRINCIPAL, subject="another-person")
        with self.assertRaises(AuthenticationError):
            self.run_auth(self.auth(directory=directory))

    def test_directory_invalid_local_id_denied(self):
        directory = ChangingDirectory()
        directory.current = replace(PRINCIPAL, id="local\x00id")
        with self.assertRaises(AuthenticationError):
            self.run_auth(self.auth(directory=directory))

    def test_directory_untyped_result_denied(self):
        directory = ChangingDirectory()
        directory.current = {"id": PRINCIPAL.id, "active": True}
        with self.assertRaises(AuthenticationError):
            self.run_auth(self.auth(directory=directory))

    def test_current_binding_reloaded_on_every_request(self):
        directory = ChangingDirectory()
        auth = self.auth(directory=directory)
        self.assertEqual(self.run_auth(auth), PRINCIPAL)
        directory.current = replace(PRINCIPAL, active=False)
        with self.assertRaises(AuthenticationError):
            self.run_auth(auth)
        self.assertEqual(directory.calls, 2)

    def test_deleted_binding_not_cached(self):
        directory = ChangingDirectory()
        auth = self.auth(directory=directory)
        self.run_auth(auth)
        directory.current = None
        with self.assertRaises(AuthenticationError):
            self.run_auth(auth)

    def test_expiry_at_exact_boundary_denied(self):
        with self.assertRaises(AuthenticationError):
            self.run_auth(self.auth(TestVerifier(assertion(expires_at=NOW))))

    def test_future_assertion_denied(self):
        with self.assertRaises(AuthenticationError):
            self.run_auth(self.auth(TestVerifier(assertion(issued_at=NOW + timedelta(seconds=1)))))

    def test_excessive_assertion_lifetime_denied(self):
        with self.assertRaises(AuthenticationError):
            self.run_auth(self.auth(TestVerifier(assertion(expires_at=NOW + timedelta(hours=1)))))

    def test_expired_during_verifier_denied(self):
        with self.assertRaises(AuthenticationError):
            self.run_auth(self.auth(clock=lambda: NOW + timedelta(minutes=2)))

    def test_expired_during_directory_denied(self):
        times = iter((NOW, NOW + timedelta(minutes=2)))
        with self.assertRaises(AuthenticationError):
            self.run_auth(self.auth(clock=lambda: next(times)))

    def test_timezone_aware_non_utc_timestamps_supported(self):
        zone = timezone(timedelta(hours=8))
        result = assertion(issued_at=(NOW - timedelta(seconds=10)).astimezone(zone),
                           expires_at=(NOW + timedelta(seconds=60)).astimezone(zone))
        self.assertEqual(self.run_auth(self.auth(TestVerifier(result))), PRINCIPAL)

    def test_naive_clock_is_unavailable(self):
        with self.assertRaises(AuthenticationUnavailable):
            self.run_auth(self.auth(clock=lambda: NOW.replace(tzinfo=None)))

    def test_invalid_clock_type_is_unavailable(self):
        with self.assertRaises(AuthenticationUnavailable):
            self.run_auth(self.auth(clock=lambda: 123))

    def test_provider_denial_is_sanitized(self):
        verifier = TestVerifier(error=AuthenticationError())
        with self.assertRaisesRegex(AuthenticationError, "^authentication_required$"):
            self.run_auth(self.auth(verifier))

    def test_provider_error_is_sanitized_and_unchained(self):
        verifier = TestVerifier(error=RuntimeError("raw-private-provider-response"))
        try:
            self.run_auth(self.auth(verifier))
        except AuthenticationUnavailable as error:
            rendered = "".join(traceback.format_exception(error))
            self.assertNotIn("raw-private-provider-response", rendered)
            self.assertEqual(error.status, 503)
        else:
            self.fail("unexpected authentication success")

    def test_directory_outage_fails_closed(self):
        class BrokenDirectory:
            def resolve(self, issuer, subject):
                raise RuntimeError("private-database-detail")
        with self.assertRaisesRegex(AuthenticationUnavailable, "^authentication_unavailable$"):
            self.run_auth(self.auth(directory=BrokenDirectory()))

    def test_credential_repr_does_not_include_value(self):
        value = Credential("synthetic-sensitive-input")
        self.assertNotIn(value.value, repr(value))
        self.assertNotIn(value.value, str(value))

    def test_credentials_empty_whitespace_control_or_oversized_rejected(self):
        for value in ("", "x y", "x\n", "x\x00", "x\x7f", "x" * 8193, None, b"x"):
            with self.subTest(value_type=type(value)), self.assertRaises(ValueError):
                Credential(value)

    def test_assertion_invalid_identity_fields_rejected(self):
        for changes in ({"subject": ""}, {"subject": "x\x00"}, {"audience": "x y"},
                        {"issuer": "http://identity.example.invalid"},
                        {"issuer": "https://user@identity.example.invalid"},
                        {"issuer": ISSUER + "?key=value"}, {"issuer": ISSUER + "#fragment"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                assertion(**changes)

    def test_assertion_invalid_times_rejected(self):
        for changes in ({"issued_at": NOW.replace(tzinfo=None)},
                        {"expires_at": NOW - timedelta(seconds=20)}, {"issued_at": 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                assertion(**changes)

    def test_duplicate_provider_rejected(self):
        provider = Provider("test", ISSUER, AUDIENCE, TestVerifier())
        with self.assertRaises(ValueError):
            self.auth(providers=(provider, provider))

    def test_provider_needs_actual_verifier(self):
        with self.assertRaises(ValueError):
            Provider("test", ISSUER, AUDIENCE, None)

    def test_invalid_assertion_age_configuration_rejected(self):
        for value in (0, timedelta(0), timedelta(hours=2)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Provider("test", ISSUER, AUDIENCE, TestVerifier(), value)

    def test_duplicate_external_pair_rejected(self):
        with self.assertRaises(ValueError):
            StaticPrincipalDirectory((PRINCIPAL, replace(PRINCIPAL, id="other-local")))

    def test_duplicate_local_id_rejected(self):
        with self.assertRaises(ValueError):
            StaticPrincipalDirectory((PRINCIPAL, replace(PRINCIPAL, subject="other-external")))

    def test_same_subject_different_issuer_is_distinct(self):
        other = Principal("local-8", "https://another.example.invalid", SUBJECT)
        directory = StaticPrincipalDirectory((PRINCIPAL, other))
        self.assertEqual(directory.resolve(ISSUER, SUBJECT), PRINCIPAL)
        self.assertEqual(directory.resolve(other.issuer, SUBJECT), other)

    def test_identity_does_not_create_workspace_access(self):
        other = Principal("local-admin", ISSUER, "owner")
        policy = WorkspaceService(principals=(PRINCIPAL, other),
            workspaces=(Workspace("private", "Private"),),
            memberships=(Membership("private", other.id, "admin"),),
            modules=(ModuleInstance("private", "board"),))
        principal = self.run_auth(self.auth())
        with self.assertRaises(PolicyError) as error:
            policy.authorize(principal.id, "private", "board", "read")
        self.assertEqual(error.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
