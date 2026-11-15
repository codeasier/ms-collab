# Unified cookie-only frontend

- `shell.mjs`: explicit session inspection/login/logout, fixed navigation and
  pagehide/401 generation fencing; no bearer or default identity.
- `client.mjs`: same-origin cookie requests, fresh readable CSRF proof and pinned
  provider navigation; shared responsibility adapter preserves exact scope/CAS.
- `index.html`, `manage.html`, `responsibility.html`: fixed public shells, no
  credential input or user data embedded in HTML.
- `artifacts-session.mjs`: exact-scope published catalog and complete-candidate
  approval adapters, using the same cookie client and current CSRF proof.
- `artifacts.html`, `artifact-admin.html`, `artifacts.css`: optional, light-theme
  module pages. Refresh is separately configured and disabled by default; it
  creates only a private candidate and never auto-approves.
- `artifact-downloads.mjs`, `artifact-download-ui.mjs`: separately configured
  exact-byte confirmation, bounded streaming/hash verification and ephemeral native
  browser handoff using the same cookie client; no URL or bearer inputs.
- `style.css`: responsive shared navigation/account boundary.
- [Tests](tests/README.md): contract and actual synthetic HTTPS acceptance.

Business controllers are reused from [workspace administration](../workspace-admin/README.md),
[responsibility](../responsibility/README.md), [artifact catalog](../artifacts/README.md)
and [artifact administration](../artifact-admin/README.md), with an explicit supplied client.
Only their separate legacy `entry.mjs` scripts create bearer clients. New session
pages never import those entry scripts and never copy or fabricate a credential.
The server is [ApplicationUI](../../packages/application_ui/README.md).

The `/community/` page mounts the reviewed community board with `createCommunitySessionClient`, preserving full-snapshot explicit approval and current session CSRF/CAS. No legacy bearer entry is mounted; upstream refresh remains unconfigured. See the [sharing boundary](../../packages/application_ui/README.md#community-sharing-boundary).
- `pipelines.html`: credential-free pipeline scope, runs, details and durations.
  Its adapter uses the same session client, never a bearer or upstream token.
- [Pipeline cookie acceptance](tests/README.md): independent synthetic HTTPS flow.

- `research-assets.html`: read-only catalog via the shared cookie client and
  strict [research asset controller](../research-assets/README.md).
- `/assistant/`: owner-scoped saved display history, exact-revision event pagination and reversible archive/restore. No execution or MCP. See [boundary](../assistant/README.md).

- `gui-smoke.html`, `gui-smoke-session.mjs`: optional shared-session read-only GUI
  observations. Existing strict scope/snapshot validators and view generation are
  reused; authentication remains exclusively in the common shell.
- [Capability/navigation audit](../../docs/unified-entry.md): identical fixed
  navigation is checked against every supported host module.
