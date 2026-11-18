# Workspace administration integration

A credential-driven management page for the configured [transactional API](../workspace_api/README.md)
and optional [lifecycle/module extension](../workspace_admin_api/README.md).
It **is not a login flow, default identity provider, session or production deployment**.
The original read-only local demo is untouched and has no link to this management
page. All synthetic verification remains under [tests](tests/README.md).

## Composition boundary

A trusted application factory wraps its existing `WorkspaceApplication`:

```python
from packages.workspace_ui import WorkspaceManagementUI
application = WorkspaceManagementUI(api=configured_api, origin="https://collab.example.invalid")
```

`configured_api` must already have a reviewed verifier and initialized SQLite
store. There is no bundled configuration, listener or default credential. The
fixed origin is trusted configuration, not derived from an incoming Host header.
Non-TLS origins are rejected except numeric `127.0.0.1` with a port for isolated
testing. Reverse proxies must normalize Host and WSGI scheme correctly without
trusting arbitrary forwarded headers. The wrapper enforces same-origin requests,
rejects cross-site fetches and serves only four explicit `/manage/` asset paths.
Existing API authentication, policy and transaction checks remain authoritative.

Open `/manage/` on that origin. Enter an opaque access credential accepted by the
configured verifier. The UI sends only same-origin bearer API requests with
cookies omitted and redirects rejected. Credentials are kept only in module
memory, input is cleared on connect, and no token is written to browser storage,
URLs, static files or screenshots. Disconnect/pagehide clears local state and
aborts outstanding requests. This is **not issuer-side token revocation or secure
memory erasure**; an already accepted mutation may complete after disconnection.
Reconnect and reload to verify uncertain results. Do not enter real credentials
in the synthetic acceptance harness.

## Available operations

- List the actor's current workspaces, then fetch admin-only memberships.
- Edit an active member's role/state with the loaded `If-Match` revision.
- Explicit edit/cancel flow, last-administrator conflict, stale-revision reload,
  authentication-expiry disconnect, wrong-scope response rejection and cancelled
  stale-read protection.
- Revoked memberships are not editable; the backend intentionally does not
  re-enroll them. Archived workspaces allow only backend-authorized reductions.

When explicitly configured with `WorkspaceAdministrationApplication`, the same
page discovers the server's control capability and supports:

- Operator-only minimal lifecycle metadata, explicit initial-admin creation,
  suspend/recovery and irreversible archive with a confirmation dialog.
- Creation requires **Review selected ID**, then a separate Create action. The
  operator-only exact-ID lookup supplies a precondition tied to the current actor
  and target identities; Create sends it in `If-Initial-Admin-Match`. Editing the
  target, cancelling or disconnecting clears the current choice, and late or
  wrong-target lookup responses cannot restore it. There is no directory list,
  email/display-name lookup or automatic GET-and-create shortcut.
- Workspace-admin module list, approved catalog installation (initially disabled),
  revision-checked state changes and read-only archived module metadata.
- Unresolved creation requests retain the original body, key and selection value
  in page memory. Ordinary failure, closing or reviewing again cannot substitute
  a new pair for that same body. Only an explicit server 412, which establishes
  a key miss and no creation, permits a later explicit review to update the
  selection while retaining the key. A 409 never rotates the key; there is no
  automatic retry. Pairs clear on disconnect/pagehide, not into browser storage.
  After leaving the page, verify platform control metadata before creating again.
  Closing or aborting cannot cancel an already committed mutation.

The selection value is a deterministic identity comparison, not a secret,
authentication credential, unforgeable proof of human intent, one-time proposal
or reservation. It has no TTL and need not change on a server restart. Current
authorization is checked again on every lookup, creation and replay. The
[HTTP compatibility contract](../workspace_admin_api/README.md#durable-retries-and-transaction-boundary)
preserves valid old three-field-body exact replay without the new header, but
old clients cannot make new creations without it. Invitations use a separate
[recipient Review and retry contract](../workspace_invitations/README.md#explicit-recipient-identity-review):
fresh requests carry the workspace/role/current-identity equality value, while
original historical requests replay without re-granting. Uncertain invitation
key/body/proof pairs remain in page memory and are retried explicitly without
another Review; only a confirmed selection 412 permits replacing the proof.
Other identity selection, real login, legacy grant recovery and identity
transitions stay closed or separately scoped; this UI does not complete those gates.

The base API alone still supports member administration; new controls stay hidden
when the extension is not configured. No UI flag grants backend authority.
Member creation, admin transfer, sharing grants and provider enrollment remain
unimplemented. The module controls only change local policy, not external jobs,
providers, credentials or installed software. Client controls are UX, never authorization. Shared spaces
are not a union of private data. API failures do not display provider/SQL bodies.

## Verification and remaining requirements

```bash
python3 -m unittest discover -s packages/workspace_ui/tests -v
node --test frontend/workspace-admin/tests/*.test.mjs
```

The separate hosted workflow launches a test-only verifier and temporary SQLite
store and performs real browser-to-WSGI requests. It checks edits, cancel, last
administrator protection, stale revisions, scope changes during pending reads,
malformed cross-scope data, 401 clearing and viewer denial. It also covers operator
separation, creation with a lost-response retry, product/shared spaces, lifecycle
conflicts, irreversible archive, module install/disable/enable and wrong-scope
module response rejection. Only two screenshots
of synthetic data are uploaded. Real provider login, TLS/reverse-proxy behavior,
server limits, enrollment/revocation operations and production usability remain
unverified. All [API deployment requirements](../workspace_api/README.md#required-before-exposing-this-service)
continue to apply.

The new [selection acceptance plan](../../frontend/workspace-admin/tests/README.md#initial-admin-selection-acceptance-plan)
must pass existing CI on the reviewed head. Documentation alone is not an executed
test result; no local UT/build run is claimed for this increment.

## Optional identity-addressed invitations

Compose the [invitation adapter](../workspace_invitations/README.md) to enable
the signed-in inbox and administrator invite/revoke controls. These call the
actual configured service; they are not synthetic authentication. The original
base API without this extension still works and returns no fabricated inbox.
