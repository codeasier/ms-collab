# Unified frontend checks

- `client.test.mjs`: cookie-only transport, strict CSRF, bounded payloads, current
  session gating, stale replies, pinned login navigation and responsibility CAS.
- `shell.test.mjs`: actual shell/client state-machine regressions for delayed
  bootstrap, explicit setup retry and Sign out during pending module activation.
- `artifacts.test.mjs`: shared session catalog/approval adapters, exact scope,
  current CSRF/CAS, no bearer, malformed calls and stale replies after clear.
- `artifacts.cjs`: actual HTTPS catalog and complete-candidate approval, conflict,
  Cancel/Escape/double submit, delayed accepted write after pagehide/recheck,
  ineffective abort, delayed review across explicit logout, workspace/session revoke and computed contrast. Five images
  are uploaded by the [artifact session workflow](../../../.github/workflows/artifact-session-ui.yml).
- `artifact-downloads.test.mjs`: bounded bytes/headers/hash, same-session CSRF,
  late response cleanup, ignored abort, 401 epoch and digest/read timeouts.
- `artifact-downloads.cjs`: real synthetic HTTPS and native browser save with full
  byte/hash verification, explicit confirmation, binding TOCTOU, duplicate clicks,
  late response/digest suppression, real logout during acquisition, current revoke,
  responsive layouts and five screenshots. [Dedicated workflow](../../../.github/workflows/artifact-download-session.yml).
- `browser.cjs`: actual product pages over temporary HTTPS, synthetic provider and
  SQLite policy, explicit sign-in/out, management/invitation writes, responsibility
  save/conflict, no bearer, XSS text, cross-space/session revocation, interrupted
  requests/pagehide, restored pages and nine synthetic screenshots, including
  desktop/mobile initial-admin selection views. The maximum-length mobile ID is
  an explicitly intercepted layout-only fixture, not a real directory entry;
  actual selection and creation use the synthetic backend in separate cases.

Run Node tests normally. Browser tooling is locked under `scripts/browser` and
run by the [workflow](../../../.github/workflows/application-ui.yml). The temporary
certificate is accepted only in a disposable browser context, never system-wide.

Return to [frontend](../README.md).

The initial-admin selection increment also requires the shared management
controller's [two-step selection and unresolved-retry plan](../../workspace-admin/tests/README.md#initial-admin-selection-acceptance-plan)
on the actual synthetic HTTPS cookie path. Verify the selection header, unchanged
body/key, current session/CSRF, delayed responses after cleanup and 412 versus 409
behavior without weakening existing browser assertions or process cleanup.
This is an acceptance requirement, not a local execution or passing-CI claim;
real login and identity transitions remain unavailable.

- `pipeline-browser.cjs`: real synthetic HTTPS login into the shared pipeline
  controller; runs/details/durations, scope edits, malformed revisions,
  ineffective abort, delayed detail, pagehide/recheck, logout, real Back,
  membership/session revocation and XSS-as-text. Writes exactly desktop/mobile/
  detail screenshots. No live provider compatibility or production deployment.
- Run `node frontend/application/tests/pipeline-browser.cjs` with the locked
  browser tooling, via [CI](../../../.github/workflows/pipeline-cookie-ui.yml).

- `assistant-browser.cjs`: synthetic HTTPS saved-owner history, 105 events, archive/restore and interrupted lifecycle coverage; four screenshots, no execution.

- `pipeline-tasks-browser.cjs`: synthetic projected stage/task metadata through
  current cookies, fixed-revision pages, duplicate-ID notices, rejected extra
  fields, close/scope races and revocation. No raw logs or real provider calls.
- `gui-smoke.test.mjs`, `gui-smoke.cjs`: same-session adapter and real synthetic
  HTTPS navigation, pagination/CAS, empty/unpublished distinction, literal markup,
  scope/filter clear, ineffective abort, logout, pagehide and current revocation.
  Four screenshots are uploaded by [GUI entry CI](../../../.github/workflows/gui-entry.yml).

- `connections.test.mjs`, `connections.cjs`: mounted grant edits/confirmation,
  current readable scope picker, strict responses, conflict, ignored-abort and
  lifecycle cleanup. [Connection CI](../../../.github/workflows/workspace-connections.yml)
  runs the real synthetic HTTPS flow and uploads four responsive screenshots.

- `fixture-process.cjs`, `fixture-process.test.mjs`: owned synthetic-process
  cleanup for unified, community, connections and assistant acceptance. A bounded SIGTERM wait is
  followed by forced reaping only on failure; that path still fails the test.
  Batch cleanup attempts every owned fixture even if a sibling fails. The unified
  browser preserves the original scenario error alongside cleanup errors and
  labels each barrier; bounded fixture stderr tails remain available on failure.
  Its success marker is printed only after cleanup succeeds. Cleanup cannot leave
  an ignored-SIGTERM child keeping the CI process alive.

- `community-refresh.cjs`: same actual Connections/Community pages and cookie
  host, real grant invalidation → explicit bounded refresh → private 101-row
  candidates → complete approval across filters → ordinary reader board. Includes
  changed-content reapproval, Cancel/Escape, repeat submit, missing CSRF, CAS,
  accepted-but-lost response without retries, true concurrent logout/revocation
  and five additional responsive screenshots in the existing Connection CI.
