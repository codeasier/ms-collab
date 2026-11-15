# Composition tests

`python3 -m unittest discover -s packages/responsibility_ui/tests -v` checks the
static allowlist/security headers, trusted origin, API auth delegation and no
cookie/claimed-identity fallback against real temporary SQLite services.
`browser_server.py` configures synthetic identities and observations solely for
[hosted browser acceptance](../../../frontend/responsibility/tests/README.md).

[Composition](../README.md) · [API tests](../../responsibility_api/tests/README.md)
