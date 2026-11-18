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

- `/` shows the current authenticated user's accessible workspaces, plus an
  independent explicit single-module stored-status check. It consumes the existing
  four-field [member GET](../workspace_api/README.md#minimal-member-module-status-w08-http-slice),
  not an installed-module directory, effective capability or host/provider status.
  Enabled does not enable links, reads or writes. No module request runs until the
  person selects a workspace, enters an exact key and chooses Check status.
- `/manage/` reuses the reviewed membership, invitation, module and platform
  lifecycle controller with a cookie-only request client. New workspace creation
  uses separate initial-admin Review and Create actions, preserving the
  [identity precondition and unresolved-retry contract](../workspace_admin_api/README.md#initial-admin-identity-selection).
  New invitations separately require [recipient Review](../workspace_invitations/README.md#explicit-recipient-identity-review)
  for one workspace/role/identity, not a directory search or enrollment. Historical
  invitation replay remains distinct from administrative creation retries.
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

Current 401 responses clear module/account state. Invitation Review/POST requests
add a narrow current-request guard before this transport side effect, so a delayed
response after dialog/scope invalidation cannot sign out the newer interaction.
The separate Overview status method likewise has an exact-route private guard,
not a general request-option override. These guards do not suppress a genuinely
current 401 or change unrelated requests. The status view uses fixed safe messages
instead of displaying raw response/parser/network errors.
Pagehide invalidates both controller and
session epochs, aborting/dropping old replies even when AbortSignal is ineffective.
A restored page requires an explicit session recheck. Cancellation never promises
that an already committed mutation was undone. No automatic login or write retry
is added. Client-generated retry keys for uncertain workspace/invitation creation
remain page-memory-only and clear with the lifecycle. An unresolved workspace
creation also retains its original selection value; reviewing again alone cannot
replace it. Only an explicit 412 permits a later user review to update that value
while keeping the key, and a 409 never rotates the pair.
Unresolved invitation requests retain their original key/body/recipient-proof
pair. Identical inputs offer an explicit original-request retry without a new
Review; only the exact no-write recipient-selection 412 permits a fresh explicit
Review to update its proof. Dialog cancellation, scope changes and the mounted
page's Reload control preserve that pair; full document reload, sign-out or
pagehide loses it. No durable recovery or cross-tab retry-key sharing is claimed.

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

By default, the unified host has no community credential loader: `refresh=false`,
no refresh button and no upstream request. The separate
[explicit Community refresh opt-in](../community_refresh/README.md) enables a
bounded, currently authorized acquisition into private review candidates. It
preserves the existing complete-set exact-content approval and ordinary-reader
boundary; source visibility alone is never a sharing grant. Connections grant
changes clear old content before a fresh observation can be reviewed and approved.

The dedicated community session workflow uses only synthetic local HTTPS, S256
identity, 101-row content, cookie/CSRF/CAS and lifecycle probes, with four
desktop/mobile list/confirmation screenshots. The connection workflow additionally
checks the opt-in refresh-to-reader loop with a synthetic GitCode transport and
five confirmation/private-review/reader screenshots. Neither is real GitCode or
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

### Artifact scope selection

Both Artifacts and Artifact approval now offer an explicit current-workspace
scope directory while preserving manual exact IDs. The reader lists only valid
published scopes (including empty catalogs); approval lists only currently usable
management capabilities. Neither listing grants access, contacts a provider nor
selects credentials. Selecting or changing scope clears earlier catalog/review/
download state. See the [bounded directory contract](../artifact_sync/DISCOVERY.md).

### Explicit scope-directory receipt configuration

The Pipeline and two Artifact scope pickers require the host's explicit
same-policy-DB `directory_cursors` handle and initialized auxiliary schema.
Unconfigured factories now return authorized 503 for these three directories;
existing exact/manual-ID paths remain available. The opaque protocol has no
old-tuple fallback or automatic retry. Current identity and scoped permissions
are checked on every page; tokens are only transient in-page pagination state.
Selecting/Open within the same loaded directory preserves its Next position
while clearing old exact results. Directory resets clear both token and local
last-tuple state. See the [support matrix and authority/data distinction](../directory_cursors/README.md).
Strict service_runtime is not integrated or migrated by this increment.

### Overview status lifecycle

The Overview shell still owns its one accessible-workspace read per inspection.
It synchronously supplies that result to the separate status controller and clears
its inputs/results on Recheck, sign-out and page lifecycle events. No additional
member/list/catalog request is a prerequisite. The administrator page and its
existing gates are unchanged. Unknown/nonmember status is unavailable, not proof
that a module is uninstalled; archived status remains readable and suspended
workspace errors are not converted into a module state. An old full-instance
response is rejected rather than decoded as a minimal observation.

Queries/results remain local to each document, with no storage or deep-link
restoration. A second tab's prior displayed observation is not promised to clear
immediately after logout elsewhere; its next authenticated request rechecks the
shared server session. This is a UI consumer of the W08 HTTP slice, not completion
of MCP, task cancellation, real identity/provider or production acceptance.
