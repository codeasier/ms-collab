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

## Invitation recipient review

Review recipient and Send are separate actions. Review reads only the exact
workspace/local-principal/bounded-role tuple; it grants no access, reserves
nothing and does not establish invitation feasibility. A new invitation sends
`If-Invitation-Recipient-Match: irs1-…` with the unchanged two-field body and UUID
retry key. The non-secret equality value is never shown or browser-persisted.
The server separately checks current authority and invitation eligibility.

An unresolved request keeps its original key/body/selection in page memory.
Returning to the same inputs after Cancel, a workspace switch or the in-page
Reload offers **Retry original invitation**, without requiring another Review.
Review is disabled for that unresolved signature. Only HTTP 412 with exactly
`{"error":"invitation_recipient_selection_changed"}` proves the selection was
rejected before any write. A subsequent explicit Review and Send may then replace
only the selection, keeping the key/body. Other errors, malformed responses and
unknown 412 outcomes cannot release that original pair. A matching historical
response can describe an accepted/invalidated invitation; it never grants again.

Input edits (including change-back), Cancel/Escape and in-page navigation/reload
invalidate the current review, not retained unresolved requests. Request ownership
is checked before response handling, after decoding and before changing UI or
busy controls, even if abort is ignored. Disconnect, sign-out, pagehide and a full
page reload lose all retained memory. Cancellation is not server rollback;
inspect current state before creating again after leaving the page. There is no
automatic Review, retry, cross-tab recovery or durable browser retry storage.
