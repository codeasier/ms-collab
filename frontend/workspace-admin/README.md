# Workspace administration client

Independent same-origin management page for an explicitly configured workspace
API. No credentials, identity provider or server are bundled. See the
[composition and security boundary](../../packages/workspace_ui/README.md).

- `admin.mjs`: page lifecycle, edit dialog and stale-request guards.
- `client.mjs`: strict response validation and memory-only request client.
- [Tests](tests/README.md): unit and hosted browser acceptance.

Optional lifecycle/module controls require the explicitly configured [administration extension](../../packages/workspace_admin_api/README.md); the UI never supplies operator authority or module capabilities.

Invitation controls use the [identity-addressed adapter](../../packages/workspace_invitations/README.md):
explicit existing principal, bounded role, 24-hour validity, signed-in inbox and
confirm/cancel Join/revoke. No secret link or external message is generated.
Uncertain invite creation retains a scope/body retry key in page memory only.

The business controller now exports an explicit mount function. `entry.mjs` is
the standalone bearer bootstrap; the [unified session entry](../application/README.md)
injects cookie-only requests without importing that legacy entry.
