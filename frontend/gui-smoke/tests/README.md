# GUI observation checks

- `fixture.mjs`: synthetic saved-observation response builders.
- `client.test.mjs`: strict schemas, nonzero unavailable revisions, revision
  binding and lifecycle/race tests.
- `browser.cjs`: standalone HTTPS cookie UI acceptance, including explicit
  regrant clearing and later publication without counter reset.
- [Unified GUI acceptance](../../application/tests/gui-smoke.cjs): same-session
  navigation, current permissions, stale snapshots and read-only presentation.

No real provider, identity, task runner or production data is used.
