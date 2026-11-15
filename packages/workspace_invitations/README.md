# Identity-addressed workspace invitations

An optional authenticated service and management-UI slice. Administrators invite
**existing verified local principals**; only that principal can sign in, inspect
its in-app inbox and explicitly join. Invitation IDs are references, **not bearer
secrets**. There are no public invitation URLs, emails, delivery integrations,
login defaults or client-supplied identity claims.

## Explicit composition

```python
from packages.workspace_invitations import (
    WorkspaceInvitationApplication, initialize_invitations,
)

# Trusted one-time setup on the same existing policy store, never HTTP migration.
initialize_invitations(configured_policy_store)
invitation_api = WorkspaceInvitationApplication(api=configured_workspace_api)
# api may alternatively be the configured WorkspaceAdministrationApplication.
# Pass invitation_api to WorkspaceManagementUI with an explicit trusted origin.
```

This wraps the [base API](../workspace_api/README.md) or the exact
[administration adapter](../workspace_admin_api/README.md) and preserves its
verifier, current directory binding and SQLite store. All other routes delegate
unchanged. Unknown/absent invitation schema fails closed; setup validates the
versioned STRICT table, checks, foreign keys and uniqueness. It does not import
old databases or create identities. Synthetic verifiers remain test-only.

## HTTP and authority

All successful responses use HTTP 200 and the existing `{"data": ...}` envelope.
No sensitive fields or implementation exceptions are returned.

| Method | Path | Authority / exact body |
| --- | --- | --- |
| GET | `/api/invitations` | Current principal's actionable inbox, maximum 100 |
| PATCH | `/api/invitations/{id}` | Addressed principal; `state: accepted` or `declined`, `If-Match` |
| GET | `/api/workspaces/{space}/invitations` | Current space admin; up to 100 recent records, actionable first |
| POST | `/api/workspaces/{space}/invitations` | Active space admin; `principal_id`, `role`; UUIDv4 `Idempotency-Key` |
| PATCH | `/api/workspaces/{space}/invitations/{id}` | Current space admin; `state: revoked`, `If-Match` |

- Exact local ID lookup in the authoritative verified directory. No global
  directory search, email/display-name matching or upstream account auto-linking.
  An admin can test a nominated ID's eligibility, not enumerate directory records.
- Roles are restricted to **viewer/developer**. Administrator promotion is a
  separate existing admin action after joining. Operators gain no special invite
  permission and cannot self-enroll in other spaces.
- Invitation records grant only visibility of their workspace name/ID and proposed
  role to the addressed principal. They confer no membership or business access
  before acceptance. Shared spaces use the identical explicit membership rule.
- Fixed lifetime: 24 hours, using the server's configured aware clock. Strict
  `now >= expires` rejection applies at acceptance and again before commit, after
  persistence. A slow operation cannot grant membership from an expired invite.
- Accept/decline/revoke are single terminal transitions. Stale revisions fail;
  accepted invitations cannot be replayed to restore later revoked membership.
  Revocation does not revoke already accepted membership; use member management.
- A new invitation may explicitly re-enroll a revoked member, using its next
  revision. The ordinary member PATCH remains unable to reactivate revocations.
- Acceptance rechecks current identity activity, exact target and inviter identity
  bindings, inviter's active admin membership **and its captured revision**,
  workspace lifecycle **and its captured revision**, and the prior target-member
  revision. Demote/restore or suspend/reactivate invalidates the old invitation;
  administrators must issue a fresh grant. No implicit resurrection.
- Current admins can revoke outstanding records in suspended/archived spaces.
  Creation and joining require active spaces. Expired/invalidated are derived
  views, not background database writes; revoking either records a terminal state.

## Atomicity, retries and presentation

Creation, acceptance, membership and metadata audit share one SQLite transaction.
Failure, revocation races and credential/invitation expiry roll back all of them.
The API's additional trusted commit guard can only add to the existing credential
expiry guard, never replace it. No external calls execute under the database lock.

Creation retry is scoped to inviter, workspace and a canonical UUIDv4 key. Same
key/body returns the current invitation without extending expiry or re-granting;
a different body conflicts. Current admin authorization still applies to replay.
Only one actionable invitation per workspace/target and at most 100 actionable
invitations per space are allowed. The UI keeps uncertain-creation keys by scope
and body only in connected-page memory. After disconnect/reload, inspect the
current list before creating again. Aborting a request does not undo an already
accepted write. Acceptance retries must refresh; they never create another grant.

The management page has separate in-app inbox and workspace-admin invitation
controls, explicit confirm/cancel, safe text rendering, same-origin requests,
revision errors, no automatic write retries and disconnect/pagehide cleanup.
No credential or invitation secret is placed in URLs, cookies or browser storage;
API route IDs are non-secret references and never authorize a request.
No notification or third-party message is sent by this slice.

Successful invitation changes and `membership.join` add metadata audit records.
The database stores local IDs, role/state/revisions, timestamps and identity
binding digests, not invitation secrets or bearer credentials. Existing policy
storage contains its authoritative directory as before. This is not a complete
compliance audit: failed access/read attempts and externally delivered messages
are not audited. Retention, bounded DB size, abuse controls/rate limiting, scalable
queries, trusted clock operation and actual identity enrollment remain deployment
work. The reference adapter still loads local policy into memory per transaction.

## Verification and limits

```sh
python3 -m unittest discover -s packages/workspace_invitations/tests -v
node --test frontend/workspace-admin/tests/*.test.mjs
# Locked Playwright tooling from scripts/browser is required:
node frontend/workspace-admin/tests/invitations.cjs
```

Tests use only synthetic principals and the real temporary SQLite/HTTP service:
permissions, no pre-accept access, single-use/racing acceptance/revocation,
re-enrollment, current identity/binding/revision checks, schema rejection,
idempotent restart/replay, expiry and audit-failure atomicity. Browser acceptance
covers cancel, lost creation response/retry, Join, stale revoked invitation,
late response after disconnect, mixed workspace responses and cleanup.

This is **not** a production login, provider directory sync, email invite flow,
external policy transaction or production migration. Current activity and current
binding checks cannot infer an upstream disable/re-enable event not recorded in
the authoritative directory. Local IDs must never be reused for other identities.
All [base deployment requirements](../workspace_api/README.md#required-before-exposing-this-service)
and the [identity trust boundary](../identity/README.md) remain applicable.
