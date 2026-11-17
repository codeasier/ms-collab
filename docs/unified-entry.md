# Unified entry: capability map and limits

Audit baseline: `6bfd419` (2026-11-15), followed by the GUI observation entry in
this change. This describes reviewed code and synthetic acceptance, not a live
service or completed production migration. The root demo and configured product
entry are different compositions; running the demo does not start these services.

## Information architecture

The fixed navigation has the same order on every product page: Overview,
Workspaces, Responsibility, Community, Pipelines, Artifacts, Artifact approval,
Saved history, Research assets, GUI observations. Optional links appear only for
explicitly configured host capabilities. The public bootstrap describes process
configuration, not current user grants; every API request independently checks
current identity, workspace membership, module permission and its content fences.
Public/shared workspaces are not anonymous feeds.

| Entry | Implemented capability | Important limit |
| --- | --- | --- |
| `/` | Current accessible workspace overview | No inferred repository/connection discovery |
| `/manage/` | Workspace lifecycle, members, invitations, modules | Operator metadata access is not business-data access; invitations require acceptance |
| `/responsibility/` | Scoped mappings, CAS edits, observed candidates | Path matches are not review approval; Issue assignment remains unknown |
| `/community/` | Approved content, review, complete-set explicit approval and separately configured explicit refresh | Refresh requires trusted host opt-in and current grants; provider visibility is not publication |
| `/pipelines/` | Published runs, details and duration observations | Not a runner, upstream trigger, live logs or independent execution proof |
| `/artifacts/` | Published artifact index and named views | No unapproved-candidate fallback; byte handoff is a separate explicit opt-in |
| `/artifact-admin/` | Optional private refresh, staged metadata review and explicit approval | Refresh requires a separate trusted configuration and current grant; never automatic publication |
| `/assistant/` | Current-owner saved display history, archive/restore | Not ACP execution, resume/cancel, prompts, MCP or agent dispatch |
| `/research-assets/` | Four-type scoped knowledge catalog and literal details | Not binary artifacts, incident diagnosis or command execution |
| `/gui-smoke/` | Saved task/state/stage observations and fixed-version details | No submit/retry/cancel/refresh/log/download controls; reported success is not a release gate |

The seven business modules supported by `ApplicationHost` now all have an exact
optional HTML entry. Workspace administration is always part of the composition;
artifact approval and byte download are additional separately configured
capabilities, not extra implicit modules. Static imports may be public even when
their page is disabled; source availability never grants data access.

## Audit findings addressed

- GUI smoke had a scoped API and separate cookie UI, but no `ApplicationUI` page,
  bootstrap entry or shared navigation. It now uses the same inspected session
  transport and existing strict observation validators/state machine. Its
  standalone entry remains available separately, unchanged.
- Repeated static menus had acquired inconsistent ordering across pages. Their
  order is normalized; regression checks require exactly one link per supported
  page, identical navigation, unique element IDs, and a page for each host module.
- The root README described only the original synthetic demo. It now distinguishes
  the implemented, configured service/UI composition from that demo without
  declaring real-provider or deployment acceptance.

## Still outside this boundary

No real identity provider is enabled by page registration. Without a separately
reviewed bound-code exchange/provider configuration, login stays disabled/503;
no default identity, synthetic production user, PAT or cookie-to-bearer bridge is
introduced. Provider protocol evidence and production TLS/hosting remain separate
acceptance gates. No original operations records, runtime data, credentials or
production migration occur here.

The GUI page reads exact scopes supplied by an administrator. Filters and scope
edits clear old content; Close, session changes and pagehide invalidate requests.
The next read rechecks revocation, but already delivered observations are not
remotely retractable. Missing counts or commits remain unknown, and deliberately
empty versus unpublished snapshots remain distinct. No user input becomes HTML,
Markdown, an external link or an executable action.

See [configured entry](../packages/application_ui/README.md),
[host boundary](../packages/application_host/README.md),
[GUI presentation](../frontend/gui-smoke/README.md), and
[documentation index](README.md).

## Connection self-service

With an explicitly installed community template tuple, the unified navigation
adds **Connections**. Current workspace admins with an explicit platform manager
delegation can replace reader/refresher/approver sets only within approved bounds.
Any actual change clears the old snapshot and all approvals in the same audited
transaction. Current readable-scope discovery also feeds the existing community
form, without placing private scopes in page URLs or browser storage.

With the separate [Community refresh opt-in](../packages/community_refresh/README.md),
this continues through explicit bounded refresh, private candidates, complete-set
exact-content approval and the ordinary-reader board. Refresh alone publishes no
new or changed content; only still-current approvals for unchanged content may be
retained. Default refresh remains disabled, and Connections never enables it.

This is one community vertical slice, not generic provider provisioning. No
credentials, URLs or secret references are accepted or returned. Artifact and
pipeline configuration remains separate, and no real provider is enabled by this
page. See [the contract](../packages/workspace_connections/README.md).
