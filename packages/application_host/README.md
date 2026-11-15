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
It uses the same session transaction boundary and explicitly disables refresh:
the administration adapter has no external artifact credential loader. Current complete candidate,
reader set, permission and revision are rechecked on approval; configuration is
not a grant. Community refresh has no credential loader and returns 503;
its capability reports false. There is no index/community acquisition or background worker configuration.

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
would revalidate at each transaction, but external refresh is not enabled here.

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
