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
  requests/pagehide, restored pages and six synthetic screenshots.

Run Node tests normally. Browser tooling is locked under `scripts/browser` and
run by the [workflow](../../../.github/workflows/application-ui.yml). The temporary
certificate is accepted only in a disposable browser context, never system-wide.

Return to [frontend](../README.md).

- `pipeline-browser.cjs`: real synthetic HTTPS login into the shared pipeline
  controller; runs/details/durations, scope edits, malformed revisions,
  ineffective abort, delayed detail, pagehide/recheck, logout, real Back,
  membership/session revocation and XSS-as-text. Writes exactly desktop/mobile/
  detail screenshots. No live provider compatibility or production deployment.
- Run `node frontend/application/tests/pipeline-browser.cjs` with the locked
  browser tooling, via [CI](../../../.github/workflows/pipeline-cookie-ui.yml).

- `assistant-browser.cjs`: synthetic HTTPS saved-owner history, 105 events, archive/restore and interrupted lifecycle coverage; four screenshots, no execution.
