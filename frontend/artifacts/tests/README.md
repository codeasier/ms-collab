# Artifact browser acceptance

- `client.test.mjs`: request authority, exact response scope/pagination, entry
  validation and fail-closed named views; run with `node --test`.
- `browser.cjs`: real browser-to-WSGI synthetic acceptance. The hosted workflow
  uses the repository's pinned Playwright lockfile and ephemeral loopback server.
  Tests cover navigation, sorting/filtering/pagination, stale requests, malformed
  cross-scope data, malicious text, denial, 401 and lifecycle cleanup.

Only synthetic screenshots are retained; no credential, trace, profile, raw log
or database is uploaded. A green browser check still requires visual inspection.
[UI boundary](../../../packages/artifacts_ui/README.md).
