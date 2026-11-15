# Browser and client acceptance

`client.test.mjs` covers canonical whole-snapshot hashing (including Unicode),
missing rows, exact reader sets, malformed/cross-scope envelopes, current revisions,
value-free failures and generation fencing when cancellation is ignored.

`browser.cjs` starts the isolated synthetic HTTP fixture and uses Chromium to:

- Refresh as a permitted admin without exposing candidate names to that identity.
- Review as a separate approver, display every bounded row and literal SVG-like text.
- Reject a concurrent refresh with `412`, clear stale approval and require re-review.
- Publish the complete set; verify a later private candidate stays out of reader APIs.
- Clear metadata on scope edits, denied scope, expired identity, actual Back navigation and page exit.
- Ignore cancellation for delayed review responses and prove scope changes/disconnect still prevent old rendering.
- Cancel/Escape confirmation without writes and submit duplicate DOM events only once.
- Lose a response after a successful write without automatically retrying it.
- Check desktop/mobile layout and retain three synthetic screenshots in hosted CI.

Run `node --test frontend/artifact-admin/tests/*.test.mjs` from the root. With the
repository's pinned Playwright tooling installed, run
`node frontend/artifact-admin/tests/browser.cjs`.

No real cloud account, provider calls, production records or downloads are used.
[Browser overview](../README.md) · [Server contract](../../../packages/artifact_admin/README.md).
