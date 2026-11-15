# Transactional API tests

[Test suite](test_application.py) contains the only synthetic verifier for this
adapter. Its `test-*` credential strings are test data, not real credentials or a
supported demo login. Never register this verifier in an application factory.

Tests initialize a fresh temporary SQLite store, execute WSGI requests and one
loopback HTTP flow, and clean up the server/database afterward. Concurrency tests
use explicit barriers and separate SQLite connections to force lock waiting.

Run from the repository root:

```bash
python3 -m unittest discover -s packages/workspace_api/tests -v
```

[Adapter scope](../README.md)
