# GitCode protocol tests

Offline, synthetic tests for the [protocol client](../README.md). Fixtures are
not credentials or proof of a live provider login. No network calls are made.

Coverage includes exact wire fields, minimized identity evidence, bad state
format, callback/config validation, absent/reduced scopes, malformed JSON,
duplicate keys, missing/invalid identifiers, expiry and clock failures, transport
errors, fixed endpoints, TLS construction, response size and redirect refusal.

`test_login_gate.py` exercises the coordinator, session API, configured host and
product entry with **no provider**. It asserts zero attempted connections and no
session/cookie creation, including legacy wire-client injection, partial trusted
configuration, synthetic environment values, callback material, PAT/claim headers
and forged cookies. It does not prove server PKCE conformance or create a default
identity. See the [staged provider acceptance plan](../../../docs/gitcode-login-acceptance.md).
