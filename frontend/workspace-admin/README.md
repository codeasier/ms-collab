# Workspace administration client

Independent same-origin management page for an explicitly configured workspace
API. No credentials, identity provider or server are bundled. See the
[composition and security boundary](../../packages/workspace_ui/README.md).

- `admin.mjs`: page lifecycle, edit dialog and stale-request guards.
- `client.mjs`: strict response validation and memory-only request client.
- [Tests](tests/README.md): unit and hosted browser acceptance.

Optional lifecycle/module controls require the explicitly configured [administration extension](../../packages/workspace_admin_api/README.md); the UI never supplies operator authority or module capabilities.

New workspace creation has separate Review selected ID and Create actions. Its
identity precondition travels in `If-Initial-Admin-Match`, not the unchanged
three-field body. An unresolved request retains its original key and precondition
even if the dialog is reopened or the ID reviewed again. Only a confirmed 412
allows a later explicit review to replace the value with the same key; 409 never
rotates it. This is not a credential, timed proposal or automatic retry. See the
[selection and compatibility contract](../../packages/workspace_admin_api/README.md#initial-admin-identity-selection).

Invitation controls use the [identity-addressed adapter](../../packages/workspace_invitations/README.md):
explicit existing principal, bounded role, 24-hour validity, signed-in inbox and
confirm/cancel Join/revoke. No secret link or external message is generated.
Uncertain invite creation retains a scope/body retry key in page memory only.

The business controller now exports an explicit mount function. `entry.mjs` is
the standalone bearer bootstrap; the [unified session entry](../application/README.md)
injects cookie-only requests without importing that legacy entry.
