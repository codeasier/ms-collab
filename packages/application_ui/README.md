# Cookie-only product entry

`ApplicationUI(host=...)` wraps the exact, explicitly configured
[ApplicationHost](../application_host/README.md). It adds an overview, workspace
administration, responsibility and community pages using one current server session. It is
not a listener, deployment bootstrap or real-provider enablement switch.

```python
from packages.application_ui import ApplicationUI

application = ApplicationUI(host=already_configured_application_host)
```

No database, principal, identity provider, callback endpoint, service account or
bearer credential is created or discovered. The default host's missing bound-code
provider still returns 503 for login start/callback. The page disables sign-in and
explains that gate; an existing valid server session can still be explicitly
rechecked. A synthetic fixture is never imported by the production factory.

## One entry and authority

- `/` shows only the current authenticated user's accessible workspaces.
- `/manage/` reuses the reviewed membership, invitation, module and platform
  lifecycle controller with a cookie-only request client.
- `/responsibility/` reuses reviewed exact-scope configuration/CAS and candidate
  controls. It exists only when the host enables responsibility/community.
- `/community/` reuses the explicit-content sharing board with exact-scope
  capability discovery, full review pagination and complete-set approval. It
  exists only when community is configured; static navigation is never a grant.
- `/app/bootstrap` is fixed public process configuration: available UI modules
  and the configured authorization endpoint. It is **not** user/module discovery
  or a grant. No principal, workspace, credential or provider secret is returned.
- All `/api/*` and `/auth/*` requests go unchanged through `ApplicationHost` and
  its current identity, policy, expiry, CSRF and attached-DB transaction checks.
  Module content remains scoped and approved; shared space is not anonymous access.

The wrapper uses the trusted host's fixed transport check for static/bootstrap
requests too. HTTPS, exact Host/Origin, rejected Authorization headers, no proxy
header overrides and no CORS apply. The exact no-query GET HTML entry alone permits a top-level document navigation
from another site (including a provider callback 303), while still enforcing
HTTPS/Host, no Authorization, no script-prefix and no foreign Origin. This public
HTML exception does not apply to bootstrap, assets, API/auth or embedded pages.
Static paths are allowlisted, non-frameable,
no-store, nosniff and use a self-only CSP with no inline script/style. Static
controller source is public even if its page/module is disabled; it grants no API
access. Legacy bearer entry scripts are not served by this wrapper.

## Browser lifecycle and CSRF

The new [client and shell](../../frontend/application/README.md) never read a
session token or construct/convert a bearer. The browser sends its Secure,
HttpOnly session cookie with `credentials: same-origin`. JavaScript reads only
the separately documented readable CSRF cookie, freshly for every unsafe request.
The proof cannot authenticate without the matching stored session. Missing or
malformed proof fails before a mutation is sent. No token lives in localStorage,
sessionStorage, IndexedDB, a URL or a hidden form field.

Sign-in is explicit and navigation accepts only the exact configured HTTPS
provider endpoint, fixed callback, single state/challenge and S256 fields. No
client-selected return URL or fetch redirect is followed. Logout clears private
page state immediately and sends one CSRF-protected server revoke. A failed or
lost logout is reported as unconfirmed, never as a successful logout.

Every 401 clears module/account state. Pagehide invalidates both controller and
session epochs, aborting/dropping old replies even when AbortSignal is ineffective.
A restored page requires an explicit session recheck. Cancellation never promises
that an already committed mutation was undone. No automatic login or write retry
is added. Existing server-issued retry keys for uncertain workspace/invitation
creation remain page-memory-only and clear with the lifecycle.

This slice does not add module/repository discovery, real GitCode login evidence,
external acquisition/execution, production TLS setup or deployment acceptance.
Responsibility path observations remain unpinned and unverified for review; Issue
assignment stays unknown. Other module UIs are not silently routed through a
legacy bearer wrapper.

## Verification

- [Tests](tests/README.md): static boundary, fail-closed provider, current cookie
  permissions, CSRF, CAS, logout/expiry/revocation and real HTTPS browser flows.
- Existing standalone UI wrappers remain compatible through explicit legacy entry
  scripts. Standalone browser workflows remain required, alongside the new one.
- [Workflow](../../.github/workflows/application-ui.yml): locked Chromium and only
  synthetic screenshots. A test-context self-signed certificate exception never
  changes the OS trust store. No live provider/production result is claimed.

## Community sharing boundary

The session entry injects a thin cookie-only adapter into the same reviewed
community controller. The legacy `/community/entry.mjs` is served only by the
separate bearer wrapper, never the unified entry. No token field, cookie-to-token
conversion, new authority claim or second login is introduced.

Current membership, community read/write, explicit reader/approver allowlists
and admin policy remain enforced in each original API transaction. An approver
need not be a reader, and a workspace admin is not an implicit connection reader.
Shared workspaces are not public feeds. The complete review snapshot is validated
across every 100-row server page before editing is enabled. Local filters and
20-row display pagination preserve the approval set outside the visible page.
The confirmation names exact scope/version, additions/removals and total; approval
replaces the entire set of exact content fingerprints (maximum 200), using current
CSRF proof and `If-Match`. It never publishes all provider-visible content.

Changed scope, session loss, recheck, pagehide and logout clear drafts/dialogs and
invalidate late reads or accepted write responses. A stale revision reloads current
state but never automatically resubmits. Already accepted writes may have committed;
clearing a view or logging out is not an undo. Every later action checks current
server authority, including revocation while a confirmation is open.

The unified host has no community credential loader: `refresh=false`, no refresh
button, no upstream request. This increment does not enable acquisition or change
approval/snapshot storage semantics. Existing source visibility alone is never a
sharing grant. The dedicated community session workflow uses only synthetic local
HTTPS, S256 identity, 101-row content, cookie/CSRF/CAS and lifecycle probes, with
four desktop/mobile list/confirmation screenshots. It is not real GitCode or
production-provider acceptance.
