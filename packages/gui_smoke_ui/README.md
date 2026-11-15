# GUI smoke session UI

A small, read-only browser for explicitly published GUI smoke observations.
It uses the [unified host](../application_host/README.md)'s existing HTTPS
cookie/session and policy transaction. It is **not** a working production login,
external panel proxy, live queue, test runner or OBS deployment.

## Explicit composition

The initialized policy database must already have the dedicated GUI schema,
current `gui_smoke` module permissions, exact connection readers/publishers and
any explicitly approved snapshots. Nothing is provisioned by a request.

```python
from packages.application_host import ApplicationHost, HostConfig
from packages.gui_smoke_ui import GuiSmokeUI

host = ApplicationHost(
    config=HostConfig(
        origin="https://collab.example.invalid",
        modules=("gui-smoke",),
        gui_smoke_connections=reviewed_connections,
    ),
    sessions=initialized_session_policy_store,
)
app = GuiSmokeUI(host=host)
```

This factory starts no listener and loads no environment secrets. The example
has **no login exchange adapter**, so sign-in fails closed with 503. A separately
reviewed bound-code adapter, fixed authorization endpoint and explicit scopes are
required for real identity-provider integration. Existing valid sessions remain
subject to current policy. GitCode PKCE/code-injection guarantees remain unverified;
this UI does not substitute a PAT, test actor or cookie-to-bearer bridge.

Only `/`, `/gui-smoke/` and three exact asset paths serve the non-sensitive shell.
The root path receives the existing OAuth callback's fixed redirect. `/auth/*` and
`/api/*` pass to the same host unchanged, preserving its deliberate cross-site
callback exception, Secure/HttpOnly session cookie, readable Secure CSRF cookie,
rotation, stale-cookie recovery, expiry and logout. Host configuration still
determines all mounted APIs; the UI is not a separate authority or route gateway.
Static and API responses require the configured HTTPS origin and are no-store;
static CSP restricts scripts/styles/connect to self. Authorization headers are
rejected even if a valid session cookie is also present. No CORS is added.

## What the interface means

Sign in using the explicitly configured provider, then enter an exact workspace,
connection and repository ID supplied by an administrator. IDs grant no access;
there is no scope enumeration or inferred admin access. Shared workspaces still
require explicit membership, module read, configured reader and saved audience.

The page shows snapshot state counts, filtered/paged task briefs and individual
saved task details, reported summary and stage observations. Requested and actual
commits are visibly separate. Missing summary/times remain unknown, never zero.
Unpublished and deliberately empty snapshots differ. No submit, retry, cancel,
reproduce, refresh, download, logs, schedule or fake execution controls exist.

List and summary must have the same exact scope, revision and observation time
before either is displayed. Detail and subsequent pages pin that revision; a 412
clears the view and requires reloading page one. The client bounds and validates
response schemas and capability flags. Literal content uses DOM text nodes only,
not HTML, markdown, external links or provider error strings.

Every new scope/read clears the old view. Request generation and cancellation
prevent late reads from replacing a newer scope, Back action, clear, session check
or logout. Credentials and task content are not stored in local/session storage,
URLs or browser history. `pagehide` clears memory/DOM/forms; BFCache restoration
checks the session again. Logout clears the local view immediately but only says
"Signed out" after the host confirms. Failed logout is explicitly unconfirmed.

This is not real-time revocation push: already-authorized bytes on screen cannot
be retracted remotely. Policy and session are checked at each read; the next
failed read clears the display. The saved-observation fences, per-instance config
rollout limits, principal same-value ABA limitation and SQLite snapshot semantics
remain those of the [GUI read model](../gui_smoke/README.md).

## Synthetic acceptance and remaining boundary

The dedicated workflow runs Python host/wrapper/GUI tests and Node schema/race
contracts, then a disposable real Chromium session over temporary HTTPS. Its
one-use S256 provider, identities, task data, certificate and databases are
synthetic, confined to test files and never imported by application code. It
checks cookie flags, explicit login/logout, pagination conflict, scope isolation,
empty/missing data, literal markup, navigation cleanup, revocation and desktop /
mobile screenshots. Browser tooling is locked and installed only in hosted CI.

No production identity/provider compatibility, TLS deployment, hosting, live
polling, upstream acquisition, isolated runner, queue, task mutation, report/log
rendering or download is implemented. A reported successful task is not independent
execution evidence or a release gate. The synthetic acceptance does not prove
real GitCode login or external panel execution is safe.
