# Session boundary tests

Synthetic tests for the [session/policy HTTP adapter](../README.md), not a real
GitCode login or deployment. No production callback, identity or credentials.

`test_application.py` covers cookie/Origin/CSRF guards, callback parsing,
closed-default provider, policy isolation, expiry after persistence, audit/data
rollback, clock high-water, WAL refusal, faults and cross-process logout ordering.

`browser-check.cjs` starts `browser_server.py` only with explicit synthetic-test
opt-in. Its authorization-server double uses localhost, while the app uses
127.0.0.1, exercising a cross-site top-level callback and actual browser cookies.
OpenSSL creates a disposable local test key/certificate outside the repository.
The disposable browser context ignores only that test certificate's trust errors;
no OS/browser profile trust setting is modified. This is not live TLS/provider
acceptance. It verifies Secure/HttpOnly/SameSite behavior, CSRF rejection, protected
mutation, cookie deletion and post-logout denial. No browser screenshots contain
real users or data, and the fixture suppresses request logs.
