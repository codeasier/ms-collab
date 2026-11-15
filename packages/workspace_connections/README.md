# Workspace connection self-service

A configured, cookie-authenticated community connection slice. It manages the
current database policy within **explicit platform bounds** and discovers only
registered scopes readable by the current member. It does not create provider
connections, search remote repositories, accept URLs or credentials, migrate
production data, or enable a real identity/refresh provider.

## Trusted composition

1. Explicitly initialize the existing workspace and community stores first.
2. Construct `CommunityConnectionTemplate(id, maximum, manager_ids)`. `maximum`
   is a server-side `ConnectionPolicy`: the full exact `Connection` (scope and
   bounded acquisition settings), credential reference and maximum independent
   reader/refresher/approver sets. These are trusted configuration, not form data.
3. Call `initialize_connections(store, templates)` once for those entries. It
   records immutable template and identity pins in the same policy database.
   Existing entries cannot be silently repinned or inferred from current records.
4. Pass the exact tuple as `HostConfig.community_connections`. An empty tuple is
   the default: API and `/connections/` are not mounted. No HTTP request creates
   tables, loads credentials, provisions identities or initializes connections.

Template fingerprints bind the **entire** configuration, including maxima,
manager list, credential routing and provider limits. Host/template or database
immutable-binding drift fails closed. A template is an editable-policy ceiling,
not the underlying provider authority. Editing a host tuple is not a global
revocation or rolling-deployment migration: old database policies and any old
processes must be explicitly reconciled by an operator. This slice deliberately
provides no template replacement/migration endpoint. A database policy outside
its installed ceiling requires platform repair, not automatic widening.

## Routes and authority

All routes reuse the host's exact HTTPS, host, current session, CSRF and final
identity commit guard. No bearer fallback or browser-stored credentials exist.

- `GET /api/workspaces/{w}/connections/community`: registered scopes that pass
  the current community `items` authorization. Filtering happens before results
  are returned; no hidden template names, grant bounds or unfiltered totals.
- `GET .../community/manage`: configuration for the current active workspace
  administrator who is also an explicit platform manager of that template.
- `PATCH .../community/manage/{template}`: replace the exact three grant lists
  with `If-Match` and current write permission. Each selected principal must be
  an active member in this workspace and inside its capability's platform ceiling.
  Refreshers/approvers must also currently be administrators. A later demotion,
  membership revocation, module/workspace suspension or session expiry is still
  checked by the underlying community API; stored allowlists are not capabilities.

Reader, refresher and approver grants are independent. A non-reader approver is
not added to ordinary discovery and gains no read grant by managing a template.
Shared workspaces use exactly the same membership and explicit-grant rules.
Options and responses contain local principal IDs, scope, revision and counts,
never issuer/subject, credential references, tokens, provider addresses or bodies.

Every real grant change is a compare-and-swap in the **same** SQLite transaction
as policy authorization, snapshot/approval removal and metadata-only audit. Old
refresh/approve requests cannot restore prior content after a new revision wins.
Even adding one reader removes the old snapshot and all approvals; fresh content
must be observed and explicitly approved before sharing. Reordering unchanged
sets is a no-op: no revision, approval or audit mutation. Safe JSON revision bounds
fail closed. Audit failure rolls back all changes. No external I/O occurs inside
an authenticated transaction.

## User interface

`/connections/` uses the existing cookie session and current workspace list,
shows readable scopes separately from manageable connections, provides bounded
principal checkboxes and explicit destructive-content confirmation. Conflicts,
logout, pagehide and new scope selections clear private state; interrupted writes
are never automatically retried or represented as cancelled.

When this slice is configured, the existing community page also gets a scoped
picker. Selecting a directory entry fills its existing form and calls the real
community API, which rechecks current access. No private scope is placed in page
URLs or browser storage. Manual authorized scopes still work; this is not an
all-provider search. Artifacts, pipelines and GUI retain their independent,
explicit configuration and authority; this slice does not claim their management.

## Verification

- [Tests](tests/README.md): authorization, CAS, invalidation, atomic audit,
  template drift, schema failure, safe responses and lifecycle.
- Unified UI tests and `frontend/application/tests/connections.test.mjs` exercise
  real cookie/CSRF dispatch and mounted DOM state, including ignored aborts.
- `.github/workflows/workspace-connections.yml` runs actual Chromium against the
  synthetic HTTPS/provider fixture and saves four non-production screenshots.

Synthetic tests do not demonstrate live GitCode protocol support, production
credentials, deployment readiness, or operational template migrations.
