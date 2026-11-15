# Administration client checks

`node --test frontend/workspace-admin/tests/*.test.mjs` runs pure client checks.
`browser.cjs` launches only the isolated test harness and requires the repository's
locked Playwright tooling. It writes two synthetic screenshots under the runner
temporary directory, never inside the repository. [Parent](../README.md).

Creation retries simulate an accepted request with a lost response; identical retry keys must return one workspace. Additional interactions cover operator isolation, create/archive cancellation, lifecycle/module stale revisions, approved disabled installation, archive immutability, mixed module scope rejection and lifecycle cleanup.

An intervening successful creation must not discard another unresolved creation's
retry key. Transport abort is disabled in browser acceptance so generation guards
must independently stop stale rendering and multi-stage follow-up requests.

## Invitations

`node frontend/workspace-admin/tests/invitations.cjs` starts a separate temporary
store and synthetic verifier. It covers cancel/no-write, lost create-response
replay, addressed identity Join, revoked membership/old invitation denial,
stale-revision revoke race, late inbox after disconnect with ineffective abort,
mixed-scope rejection and pagehide/storage cleanup. It produces
`invitations-desktop.png` and `invitations-mobile.png` beside the lifecycle images.
