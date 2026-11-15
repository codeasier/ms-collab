# Responsibility UI verification

- `node --test frontend/responsibility/tests/*.test.mjs`: exact scope/version/view,
  schema and non-execution claims, field/path validation, memory-only same-origin
  client, bounded bodies, no conflict retry and generation fencing.
- `node frontend/responsibility/tests/browser.cjs`: locked Playwright Chromium,
  real loopback HTTP and configured synthetic verifier. Exercises reader/editor
  permissions, actual CAS saves, duplicate submission, cancel/Escape, concurrent
  configuration, a lost successful reply, pagination/filtering, XSS, late replies
  after scope/identity changes, 401, pagehide and Back/Forward.

The fixture is isolated test code, never a production entrypoint. No real provider
or credential is used. Four screenshots contain synthetic data only; hosted CI
runs the browser because restricted local executors may not support Chromium.

[Client](../README.md) · [Service composition](../../../packages/responsibility_ui/README.md)
