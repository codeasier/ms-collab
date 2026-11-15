# Administration client checks

`node --test frontend/workspace-admin/tests/*.test.mjs` runs pure client checks.
`browser.cjs` launches only the isolated test harness and requires the repository's
locked Playwright tooling. It writes two synthetic screenshots under the runner
temporary directory, never inside the repository. [Parent](../README.md).

Creation retries simulate an accepted request with a lost response; identical retry keys must return one workspace. Additional interactions cover operator isolation, create/archive cancellation, lifecycle/module stale revisions, approved disabled installation, archive immutability, mixed module scope rejection and lifecycle cleanup.
