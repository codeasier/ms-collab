# Pipeline browser acceptance

```sh
node --test frontend/pipelines/tests/*.test.mjs
NODE_PATH=/path/to/locked/browser-tools/node_modules node frontend/pipelines/tests/browser.cjs
```

Use the lock in `scripts/browser`. CI uses its matched Chromium; an existing
local browser can be explicitly selected with `MS_COLLAB_BROWSER_EXECUTABLE`.
The harness starts only an ephemeral loopback test service, initialized through
the synthetic provider/acquisition/API chain, and blocks external browser URLs.
No real credentials, provider network, production data, log traces or deployment.

Coverage includes scope and revision checks, read-only requests, failed-run
successful-job samples, missing details, empty versus inaccessible scopes, local
filter/paging, text-only hostile labels, Close/Escape and delayed detail races,
late prior-scope duration responses, malformed data, real Back/Forward lifecycle,
authentication expiry and storage cleanup. Narrow-layout page/button overflow
is asserted. Outputs are only desktop.png, mobile.png and detail.png outside the
checkout under `$RUNNER_TEMP/ms-collab-pipeline-ui` (or the system temp directory).
Human review of those screenshots remains required in addition to a green job.

- `tasks.test.mjs`: exact revision-local task DTO, duplicate identity and global
  coverage, status pagination, malformed/extra field denial, cookie/bearer typed
  read-path equivalence and closed log actions.
- [HTTPS task acceptance](../../application/tests/pipeline-tasks-browser.cjs):
  real cookie-session pages, stale task responses/revisions, scope and membership
  invalidation, empty/missing observations and XSS. A separate keyboard journey
  opens a run from a duration sample while the run list has no matching rows.
  The task archive retains desktop, mobile and detail screenshots and adds
  `duration-sample-mobile.png` showing the focused sample button.
- `controller.test.mjs`: the actual mounted controller reaches detail→task reads;
  close/clear cancel chained or late replies and inconsistent pages clear data.
