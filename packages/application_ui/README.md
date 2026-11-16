# Cookie-only product entry

`ApplicationUI(host=...)` wraps the exact, explicitly configured
[ApplicationHost](../application_host/README.md). It adds an overview, workspace
administration, responsibility, pipeline, community and configured artifact pages using one current server session. It is
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
  lifecycle controller with a cookie-only request client. New workspace creation
  uses separate initial-admin Review and Create actions, preserving the
  [identity precondition and unresolved-retry contract](../workspace_admin_api/README.md#initial-admin-identity-selection).
- `/responsibility/` reuses reviewed exact-scope configuration/CAS and candidate
  controls. It exists only when the host enables responsibility/community.
- `/community/` reuses the explicit-content sharing board with exact-scope
  capability discovery, full review pagination and complete-set approval. It
  exists only when community is configured; static navigation is never a grant.

- `/pipelines/` reuses the scoped run/detail/duration controller through the
  cookie-only read client, only when `pipelines` is configured. No bearer input,
  session-to-token conversion, upstream refresh, logs or execution is added.
- `/artifacts/` reuses the catalog controller against **published index** quarters,
  query and named views. No unapproved candidate or raw resource fallback is used.
- `/artifact-admin/` exists only with `HostConfig.artifact_administration=True`.
  It reviews and approves an existing staged candidate, including every metadata
  row and current reader, with independent digest verification and explicit CAS
  confirmation. Refresh is disabled by default; a separate `artifact_refresh`
  host flag, trusted provider configuration and current refresher grant enable
  private acquisition only. Unconfigured refresh fails closed. See
  [configured refresh](../artifact_refresh/README.md).
- `HostConfig.artifact_downloads=True` and explicit trusted download configuration
  optionally add a catalog Check download/Confirm interaction. Availability is
  independently authorized; the browser shows size/hash and verifies all bytes
  before native browser handoff. Default configuration still has no downloads.
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
is added. Client-generated retry keys for uncertain workspace/invitation creation
remain page-memory-only and clear with the lifecycle. An unresolved workspace
creation also retains its original selection value; reviewing again alone cannot
replace it. Only an explicit 412 permits a later user review to update that value
while keeping the key, and a 409 never rotates the pair.

This slice does not add module/repository discovery, real GitCode login evidence,
real-provider compatibility, execution, production TLS setup or deployment acceptance.
Responsibility path observations remain unpinned and unverified for review; Issue
assignment stays unknown. Other module UIs are not silently routed through a
legacy bearer wrapper.

## Verification

- [Tests](tests/README.md): static boundary, fail-closed provider, current cookie
  permissions, CSRF, CAS, logout/expiry/revocation and real HTTPS browser flows.
- Existing standalone UI wrappers remain compatible through explicit legacy entry
  scripts. Legacy module browser workflows remain required, alongside the session workflows.
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

### Pipeline lifecycle

Editing any scope identifier immediately clears old rows/details and cancels the
previous scope. List and duration views still require one exact scope/revision;
details must match the selected run summary. Even if transport abort is
ineffective, cancellation prevents an old list from starting its follow-up
request or rendering after logout/recheck/Back. A new view requires explicit
scope submission; no cached connection discovery or upstream acquisition occurs.
The separate [cookie pipeline workflow](../../.github/workflows/pipeline-cookie-ui.yml)
exercises synthetic HTTPS and writes only three synthetic screenshots.

### Artifact lifecycle verification

- [Artifact session workflow](../../.github/workflows/artifact-session-ui.yml):
  actual HTTPS catalog/approval pages with synthetic index and provider. It checks
  cross-space/session revocation, CSRF, whole-set approval/CAS, Cancel/Escape,
  double submission, ineffective abort and late writes, pagehide/restoration,
  computed text contrast and five desktop/mobile screenshots. These are not live
  OBS, real identity-provider, deployment or download acceptance evidence.

### Artifact byte handoff

The [download session workflow](../../.github/workflows/artifact-download-session.yml)
uses synthetic HTTPS, current cookie/CSRF authorization, explicit byte grants and
in-memory object data. The native browser save is checked for exact length/hash;
five desktop/mobile/denied screenshots are captured. Binding changes fail before
provider access, and scope change, pagehide, cancellation and real logout suppress
late responses, release readers and revoke temporary Blob URLs. Client output is
bounded to 5 MiB/30 seconds and checked independently before handoff. The UI never
claims that a browser click proves durable save, or that delivered bytes can be
recalled. No real cloud, real identity-provider or production deployment is tested.


## Research asset catalog entry

`/research-assets/` is mounted only when `research-assets` is explicitly hosted.
It reads the existing `research_assets` policy/catalog module, not binary
artifacts. Four types, entity/text filters, stable-revision server-side paging
and plain-text details share the current cookie session. Source declarations
never become verified execution or incident diagnosis. No provider acquisition
or production migration is added. See the [UI contract](../../frontend/research-assets/README.md).

## Saved assistant history

The optional `/assistant/` page is a current-owner reader over the already mounted
assistant history API, with revision-fixed event pages and explicit reversible
archive/restore confirmation. It shares only the configured cookie session and
fresh CSRF, not a token or actor override. The existing ownership, dependency
fences and revocation semantics are unchanged. It does not enable ACP transport,
agent execution, prompts, MCP, resume, cancellation, ingestion or export.
See the [UI contract and synthetic acceptance](../../frontend/assistant/README.md).

## GUI observations and navigation audit

The optional `/gui-smoke/` page now uses this same session client and shell, not
the standalone GUI authentication controller. Exact-scope tasks, state summaries
and details reuse the existing strict validators and revision-fenced view state.
Scope/filter edits, clear, pagehide, logout and 401 discard the private display.
No second sign-in, bearer adapter, provider request or execution control is added.
See the [capability map](../../docs/unified-entry.md) for all supported pages and
explicit remaining limits, and the [GUI contract](../../frontend/gui-smoke/README.md).

## Community connection self-service

An explicitly configured `HostConfig.community_connections` tuple enables
`/connections/` and the community scope picker. This uses the same cookie-only
session, not another identity flow. Current workspace admins additionally need a
platform manager delegation; grant edits are bounded by approved principals and
clear old observations/approvals atomically. Ordinary discovery only returns
current readable, registered scopes. No credential or endpoint form exists.
See the [connection contract](../workspace_connections/README.md) for deployment
pins, revision handling, invalidation and deliberately unimplemented providers.
