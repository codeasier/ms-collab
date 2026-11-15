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

Routing uses the [single-owner test dispatcher](../../workspace-admin/tests/scenario-router.cjs)
from the invitation lifecycle fix: scenario replacement never changes ownership
of an in-flight request, and teardown drains handlers without ignoring errors.

## Generation-reference acceptance

- `references-browser.cjs bearer`: real loopback HTTP + synthetic verifier.
- `references-browser.cjs cookie`: independent temporary HTTPS fixture + actual
  shared sign-in/session, current cookies and fresh CSRF. No fake bearer adapter.
- `session.test.mjs`: exact POST body/CAS, fresh CSRF, session-epoch invalidation.
- `controller.test.mjs`: mounted controller with a minimal test DOM, covering
  stale-config retention, no candidate reads, Cancel/Escape/native Close, repeat
  submit, scope/identity/pagehide cleanup and failed proposals. This is not a
  substitute for real browser layout, navigation or screenshot acceptance.
- `packages/responsibility_ui/tests/test_reference_session.py` and
  `test_reference_transport.py`: real WSGI policy/CSRF and temporary TLS login +
  cookie protocol probes. The TLS probe trusts only its generated certificate in
  its own SSL context; it does not relax system trust or claim browser acceptance.

Run each browser script with the mode argument shown above and the same locked
Playwright setup. Each mode exercises real identity ABA through the disposable
`workspaces.tests.identity_fixture` helper, stale candidate denial in all four
views, retained historical labels, ordinary-save refusal, expired proposal CAS,
Cancel/Escape/double submission, lost/late accepted replies, logout/identity and
scope fencing, pagehide/Back, revoked membership replacement and retained pins
on re-addition. No production mutation API or provider is enabled. The cookie
fixture requires `MS_COLLAB_SYNTHETIC_RESPONSIBILITY_UI=1` and temporary TLS files;
only its disposable browser context accepts that certificate. The script handles
this setup, removes its TLS files and awaits clean fixture shutdown.

Four additional synthetic-only screenshots per mode show historical desktop,
mobile, explicit confirmation and reconfirmed state. The responsibility workflow
runs both modes and uploads only those PNGs, never certificates or credentials.

The legacy bearer browser and reference browser use `stopSyntheticFixture` for
bounded, verified child cleanup. A forced reap remains a test failure, not a
retry. Both fixture entrypoints share `serve_synthetic`; real HTTP/HTTPS active
request and idle SIGTERM regressions run in the Python composition suite.
