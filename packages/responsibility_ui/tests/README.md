# Composition tests

`python3 -m unittest discover -s packages/responsibility_ui/tests -v` checks the
static allowlist/security headers, trusted origin, API auth delegation and no
cookie/claimed-identity fallback against real temporary SQLite services.
`browser_server.py` configures synthetic identities and observations solely for
[hosted browser acceptance](../../../frontend/responsibility/tests/README.md).

[Composition](../README.md) · [API tests](../../responsibility_api/tests/README.md)

Both responsibility browser fixtures use the shared test-only `serve_synthetic`
shutdown helper. `test_fixture_shutdown.py` launches the real bearer HTTP and
cookie HTTPS entrypoints, injects repeated SIGTERM inside an actual page request,
and checks idle shutdown separately. All four cases must complete the response
and exit cleanly within the deadline, without a swallowed `SystemExit` or an
orphaned child. The subprocess patch is not a product route or identity hook.
