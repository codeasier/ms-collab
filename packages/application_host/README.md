# Configured unified service host

`ApplicationHost` composes the existing workspace administration/invitation API
and seven module APIs behind **one** HTTPS cookie/session and SQLite policy
boundary. It is a WSGI factory, not a listener, deployment, production bootstrap,
full UI migration or working real GitCode login. No synthetic identity, default
account, database creation or fixture import exists in the factory.

## Explicit composition

Trusted host code supplies already initialized `SessionPolicyStore` and immutable
`HostConfig`. No environment file, plugin import path, request claim, tenant name
or provider URL is discovered from client input. For example, using objects that
an operator has separately provisioned and reviewed:

```python
from packages.application_host import ApplicationHost, HostConfig

app = ApplicationHost(
    config=HostConfig(origin="https://collab.example.invalid",
                      modules=("community", "pipelines")),
    sessions=initialized_session_policy_store,
)
```

This example deliberately supplies **no login exchange adapter**. `/auth/start`
and `/auth/callback` return 503 without minting attempts or sessions. Existing
validated sessions, if present in the explicitly supplied store, are still
subject to current identity/policy checks. There is no `enable_gitcode=True`,
PAT-to-session shortcut or test identity mode. A separately reviewed bound-code
adapter, fixed authorization endpoint and explicit scopes must be supplied
together when their real protocol guarantees have been verified. GitCode's
unverified PKCE/code-injection guarantees remain a blocker to real login, not a
reason to weaken the contract. See [login state](../login_state/README.md) and
[session boundary](../session_api/README.md).

## Mounted routes and isolation

- `/auth/*`: the original one-use state/browser binding, cookie rotation,
  session inspection, POST logout and stale-cookie recovery flow.
- `/api/control/*`, `/api/workspaces`, workspace members/module administration,
  `/api/invitations` and workspace invitations: existing current-authority,
  CAS/idempotency/expiry rules, with cookie rather than bearer authority.
- Explicit module keys mount their fixed routes only:
  - `community`: approved community items, capabilities, preview and approval.
  - `pipelines`: stored pipeline runs/details/durations.
  - `artifacts`: **published index** quarters, query and views, not raw catalogs.
  - `research-assets`: stored asset types/entities/assets. Its policy/catalog
    module key remains `research_assets`, not the URL spelling.
  - `responsibility`: existing config/CAS and approved candidate queries. Requires
    `community` also configured, plus both current per-space policy grants.
  - `assistant`: owner-scoped saved history and reversible archive metadata.
  - `gui-smoke`: saved task briefs/details/state counts only. Its policy/catalog
    key is `gui_smoke`; an explicit `HostConfig.gui_smoke_connections` tuple is
    required for any scope to be readable. No panel proxy or execution route.
- `GET /` returns only fixed, non-sensitive service metadata. It is not a product
  frontend or an authorized module/workspace discovery response. An optional
  [GUI smoke shell](../gui_smoke_ui/README.md) replaces this root page and serves
  only fixed static assets while forwarding auth/API requests unchanged.

The configured module list is **not** a user's authorization. Workspace/module
lifecycle, current membership, explicit scope reader/approver grants, content
approval and history ownership are still checked inside each original handler's
transaction. A shared workspace is not anonymous or implicitly readable.
Configuration cannot bypass disabled module state. A module catalog is a subset
of the explicitly hosted policy keys and never grants connector or execution
rights.

The host never forwards to the local demo or generic raw resource API. In
particular `/modules/*/resources/*` is not mounted: it must not become a shortcut
around a module's finer publication and ownership fences. Unknown/disabled routes
return 404. Legacy module browser wrappers,
asset synchronization, responsibility evidence ingestion and pipeline collection
are **not mounted**. `HostConfig.artifact_administration=True` optionally mounts
existing artifact status/review/approve routes, requiring the artifacts module.
It uses the same session transaction boundary; refresh stays disabled by default. Current complete candidate,
reader set, permission and revision are rechecked on approval; configuration is
not a grant. By default, Community refresh has no credential loader and returns
503; its capability reports false. Community acquisition requires the explicit
opt-in described below. Background scheduling remains unconfigured.

`HostConfig.artifact_downloads=True` is a separate opt-in requiring artifacts and
an exact `ArtifactDownloadConfiguration` with a trusted scoped credential loader.
The host internally constructs the [byte downloader](../artifact_download/README.md)
on this same session/policy boundary. It mounts only exact POST availability and
confirmed download routes; no external URL, bearer conversion, anonymous link or
metadata-to-download grant exists. Availability has no provider/secret/lease
side effects. Download requires explicit downloader/content binding and expected
SHA-256 in the admission transaction, then current session/policy checks throughout
bounded acquisition and output. Missing configuration fails closed. This does not
enable index refresh, create grants or assert real-cloud/deployment acceptance.

## Authentication and transaction seam

`WorkspaceApplication(session_boundary=...)` is an exclusive trusted constructor
mode. It accepts the exact configured `SessionWorkspaceApplication` and rejects
mixed bearer store/providers/key settings. It delegates request credential
parsing and **every** transactional guard to that boundary. The host calls the
fixed module dispatchers through the session response envelope, not their legacy
bearer WSGI handlers. There is no session-to-bearer conversion, cached principal
ID, HTTP actor field or per-module login store.

All requests keep the session adapter's exact HTTPS/Host/Origin checks, rejection
of mixed Authorization headers, Secure/HttpOnly cookies and no-store/no-referrer
response controls. All unsafe methods, including read-only POST queries, need
CSRF cookie/header/stored-session proof. Each module operation rechecks current
session, identity and policy under the same attached-DB transaction; logout,
expiry and membership changes apply across modules. Multi-phase module handlers
revalidate the original session request at each transaction, including every
artifact refresh page and final candidate commit when explicitly enabled.

Preinitialization of dedicated module schemas is explicit and outside requests.
A missing or malformed schema fails closed without creating or migrating it. The
host inherits the local same-device DELETE/FULL SQLite limitations; it is not a
network/distributed-database or host power-loss guarantee.

## Verification and remaining deployment work

[Tests](tests/README.md) cover six successful module reads in one host, approval
and owner fences, shared/private workspace isolation, invitation acceptance,
CSRF/CAS writes, current revocation/expiry, missing schemas, mixed authentication,
closed provider defaults and no raw-resource fallback. A separate hosted CI job
runs actual Chromium over temporary HTTPS and a synthetic cross-site provider.

No real provider credentials, upstream calls, production data or OS trust-store
changes are part of the tests. TLS termination/raw-header validation, bounded
server reads, operational rate limits/logging, service supervision, backup and
recovery, production filesystem behavior and real identity-provider acceptance
remain deployment prerequisites. Existing bearer APIs remain separately
available for their original explicitly configured hosts, not on this host.

## Optional product entry

[ApplicationUI](../application_ui/README.md) can wrap this configured host with
workspace, responsibility and explicitly configured artifact pages. It does not enable a provider, change the
session authority or mount a legacy bearer wrapper.

## Optional scoped artifact refresh

`HostConfig.artifact_refresh=True` additionally requires administration and an
`ArtifactRefreshConfiguration(credential_loader, transport=None)`. The exact
cookie boundary constructs its own synchronizer and refresh controller; an
externally authenticated service cannot be injected. Trusted provisioning must
first create `IndexPolicy` records and call `initialize_refresh(sessions.policy)`.
Neither flag creates rights, credentials or schemas.

The loader receives only the stored reference, exact scope and fixed computed
OBS origin. Current admin + refresher rights, cookie/CSRF, revision and 2 global /
1 actor / 1 scope admission are checked before it runs. Acquisition uses bounded
metadata pages and repeated current authority. A private candidate, audit event
and completed lease commit together; existing published content is unchanged
until a separate full-set digest/readers/CAS approval. Resource-uncertain workers
retain capacity even after expiry. See [refresh contract](../artifact_refresh/DESIGN.md)
for timer, DNS/loader/storage, recovery and rate/cost boundaries. This is not a
production startup command or proof of real OBS compatibility.

## Optional scoped Community refresh

`HostConfig.community_refresh=True` requires the hosted Community module and an
exact `CommunityRefreshConfiguration(credential_loader, transport=None)` passed
as `ApplicationHost(..., community_refresh=...)`. Trusted provisioning must call
`packages.community_refresh.initialize_refresh(sessions.policy)` separately.
Neither the flag nor host creates schemas, grants, credentials or provider login.
The host constructs its own same-session Community service and bounded controller.
Default capability remains false and POST remains 503 without network.

Connections grant edits discard prior observations/approvals. Explicit refresh
then produces private review candidates, followed by the existing complete-set
exact-content approval and ordinary reader board. Current authority, 2 global /
1 actor / 1 scope admission, a 30-second budget, uncertain-resource retention and
post-persistence readback protect the entire flow. See the
[Community refresh contract](../community_refresh/DESIGN.md). No real GitCode,
production startup/runtime enablement, periodic refresh or deployment is claimed.

## Explicit directory cursor migration

The three Pipeline/Artifact scope directories now require an explicitly supplied
same-policy-DB `directory_cursors` handle. Existing factories without it return
503 after current identity/scope/module checks, even if receipt tables exist.
There is no old-tuple or first-page fallback. Manual exact-scope APIs are unchanged.
Artifact administration still needs its original mount switch; this handle does
not enable administration, refresh, downloads or any grant.

For an already reviewed **explicit factory layout only**, trusted maintenance
may initialize once and then pass a handle:

```python
from packages.directory_cursors import initialize_directory_cursors, ReceiptStore

initialize_directory_cursors(initialized_session_policy_store.policy,
                             layout="explicit-factory-v1")
receipts = ReceiptStore(initialized_session_policy_store.policy)
app = ApplicationHost(config=reviewed_host_config,
                      sessions=initialized_session_policy_store,
                      directory_cursors=receipts)
```

This is not enrollment, runtime migration or permission to change a real store.
Do not use it on `service_runtime`: its installation markers are refused and its
strict schema digest does not accept receipt objects. No constructor/GET creates
schema. See the [full support and authority matrix](../directory_cursors/README.md).

## Exact member module status

`GET /api/workspaces/{workspace}/modules/{key}` is the shared core/API's
[minimal member lifecycle observation](../workspace_api/README.md#minimal-member-module-status-w08-http-slice).
It returns only `workspace_id`, `key`, `state`, and module `revision`, without
requiring directory receipts or enabling any mounted business module. Stored
`enabled` is not evidence that this host mounts the module or a provider is ready.
Admin collection/configuration permissions and resource denials are unchanged.
This adds no page, runtime configuration, identity activation or deployment.
