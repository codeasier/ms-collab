# Synthetic GUI cookie UI tests

- [Application checks](test_application.py): exact scopes, cookie/session policy,
  static resources and denied execution routes.
- [Shared fixture](fixtures.py): explicit persisted grant enrollment and sanitized
  synthetic snapshots; no real provider credentials.
- [HTTPS fixture](browser_server.py) and [startup regression](test_fixture.py):
  both entry points exercise regrant and publication with nonreset counters,
  plus real idle/request-time SIGTERM and verified child cleanup.

[Package navigation](../README.md) · [Standalone browser acceptance](../../../frontend/gui-smoke/tests/browser.cjs)
