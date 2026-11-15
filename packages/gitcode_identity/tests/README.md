# GitCode protocol tests

Offline, synthetic tests for the [protocol client](../README.md). Fixtures are
not credentials or proof of a live provider login. No network calls are made.

Coverage includes exact wire fields, minimized identity evidence, bad state
format, callback/config validation, absent/reduced scopes, malformed JSON,
duplicate keys, missing/invalid identifiers, expiry and clock failures, transport
errors, fixed endpoints, TLS construction, response size and redirect refusal.
