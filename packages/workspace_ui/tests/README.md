# Synthetic administration checks

`test_ui.py` verifies the static allowlist, fixed-origin boundary and preservation
of real API authentication. `browser_server.py` is solely an acceptance harness
with a test verifier and temporary synthetic SQLite state. Never import it into
an application factory or use it with real credentials/data.

Hosted Chromium runs `frontend/workspace-admin/tests/browser.cjs` through actual
HTTP, checks interrupted/repeated/error flows and emits only two synthetic PNGs.
It does not prove real login or production deployment. [Parent](../README.md).

The synthetic operator has no workspace memberships. Lifecycle/module tests use the explicitly configured administration extension and initialized retry schema, with catalog templates limited to test data.

The initial-admin selection increment must additionally satisfy the
[two-step browser acceptance plan](../../../frontend/workspace-admin/tests/README.md#initial-admin-selection-acceptance-plan).
It covers reviewed-target state and retained unresolved key/precondition pairs,
not a new provider or identity-transition fixture. Existing CI on the reviewed
head is the acceptance gate; this document does not claim a local UT/build run.
