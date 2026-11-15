# Unified host verification

- `fixtures.py`: temporary synthetic users, policy/module records and bound-code
  exchange; imported only by tests, never the application factory.
- `test_host.py`: cross-module WSGI/SQLite positive and negative integration tests.
- `browser_server.py`: explicitly opted-in, temporary HTTPS fixture and synthetic
  cross-site provider. Its fixture-only root/revocation routes are not host APIs.
- `browser-check.cjs`: actual browser login, all six modules, approved content and
  shared-space fences, CSRF mutation, subject revocation, recovery and logout.

```bash
python3 -m unittest discover -s packages/application_host/tests -v
node packages/application_host/tests/browser-check.cjs
```

Browser tooling is locked under `scripts/browser`; see the dedicated
[workflow](../../../.github/workflows/application-host.yml). Its temporary
self-signed certificate is accepted only by the disposable test browser context,
not the OS trust store. No live GitCode or production acceptance is claimed.

Return to [host contract](../README.md).
