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
unchanged. Invitation-record operations fail closed on unknown/absent schema; setup validates the
versioned STRICT table, checks, foreign keys and uniqueness. It does not import
old databases or create identities. Synthetic verifiers remain test-only.
The separate identity Review below does not query the invitation table itself.

## HTTP and authority

All successful responses use HTTP 200 and the existing `{"data": ...}` envelope.
No sensitive fields or implementation exceptions are returned.

| Method | Path | Authority / exact body |
| --- | --- | --- |
| GET | `/api/invitations` | Current principal's actionable inbox, maximum 100 |
| PATCH | `/api/invitations/{id}` | Addressed principal; `state: accepted` or `declined`, `If-Match` |
| GET | `/api/workspaces/{space}/invitations` | Current space admin; up to 100 recent records, actionable first |
| GET | `/api/workspaces/{space}/invitations/selections/{principal}/{role}` | Active space admin; one exact active local target, role `viewer` or `developer`; no query/list/search |
| POST | `/api/workspaces/{space}/invitations` | Active space admin; `principal_id`, `role`; UUIDv4 `Idempotency-Key`; new requests require `If-Invitation-Recipient-Match` |
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

## Explicit recipient identity review

Review returns exactly `workspace_id`, `principal_id`, `role`, and `precondition`
inside the normal data envelope. It checks current route membership, admin role,
active workspace and the exact active target; operator status is not a bypass.
Missing/inactive targets share `404 invitation_recipient_unavailable`. No issuer,
subject, realm, generation or membership information is returned. The UI shows
the chosen local ID, workspace and role, not the hash. Review grants no access,
creates no invitation and does not establish that the human chose the right person.

The `irs1-` + 64 lowercase hexadecimal value is SHA-256 of a purpose/version-
separated canonical JSON object containing the workspace, role, policy realm and
both current actor/target `(realm, local ID, issuer, subject, generation)` pins.
It is a **non-secret equality precondition**, not a credential, MAC/signature,
unforgeable proof of human review, permission, timed proposal or reservation.
There is no new key, schema, TTL or one-time consumption. An unchanged selection
survives ordinary restart; full database rollback to identical pins is not solved.
It deliberately excludes global policy epochs, mutable membership/workspace
revisions and invitation counts. Current authority/lifecycle is rechecked at write;
ordinary unrelated changes do not become identity changes.

The same writer transaction first checks current creation authority and the
original key namespace/body. An authorized exact historical replay returns its
original record **without interpreting the new header**, including accepted or
effectively invalidated history. It never repins old identity, extends expiry or
re-grants membership. A different body still yields `409 idempotency_conflict`.
This is not the administration API's old-provenance retry-conflict contract.

Only an unused key requires the reviewed value. Existing invalid-target/role
checks remain first, then the new exact-header checks, then the unchanged
membership, pending-target uniqueness/quota and insertion gates:

- Missing: `428 invitation_recipient_selection_required`.
- Non-string, empty, noncanonical or wrong format: `400 invalid_invitation_recipient_selection`.
- Well-formed but different current identity/scope/role: `412 invitation_recipient_selection_changed`.

Only that exact 412 is a definitive no-write selection rejection. It permits a
later **explicit** Review to replace the retained proof with the same key/body,
never an automatic retry. The target becoming inactive keeps the pre-existing
`400 invalid_invitee` rather than necessarily producing 412. Review does not test
already_member/already_invited, reserve quota or promise a successful fresh write.

Old clients can recover current-authority exact historical requests without the
header but cannot make new otherwise-valid invitations. New clients connected
to an old server cannot Review and must not fall back to headerless creation.
Cookie POST retains exact Origin and fresh CSRF requirements; Bearer and Cookie
remain distinct authentication boundaries. The new GET alone completes normal
final-authentication guards even for captured scope/target/W05 policy denials;
existing malformed-route and failed-POST error-finalization semantics are not
globally changed. Successful creates/replays keep their original final guards.

Review has no dedicated invitation/admin-retry/receipt query or business DML.
The ordinary policy loader still runs its global foreign-key integrity check,
which may inspect auxiliary references and fail on corruption. A valid database
without the optional invitation table can support Review, not POST; this is not
a cross-corrupt-database availability promise. Cookie login-clock maintenance
remains permitted. No implicit DDL, `_persist`, business audit or receipt is added.
The supported runtime already rejects duplicate raw headers and underscore
aliases before WSGI; other hosts owe the existing normalization/limits contract.
A merged comma-list is not a valid selection. A WSGI mapping alone cannot prove
that no duplicate raw header was present.

## Atomicity, retries and presentation

Creation, acceptance, membership and metadata audit share one SQLite transaction.
Failure, revocation races and credential/invitation expiry roll back all of them.
The API's additional trusted commit guard can only add to the existing credential
expiry guard, never replace it. No external calls execute under the database lock.

Creation retry is scoped to inviter, workspace and a canonical UUIDv4 key. Same
key/body returns the current invitation without extending expiry or re-granting;
a different body conflicts. Current admin authorization still applies to replay.
Only one actionable invitation per workspace/target and at most 100 actionable
invitations per space are allowed. The UI keeps uncertain-creation key/body/proof
pairs by scope and body only in connected-page memory. Cancelling a dialog,
switching scope or using its Reload control clears the visible review but retains
the unresolved pair. Returning to identical inputs allows an explicit retry of
the original request without a new Review; new Review is disabled for that
unresolved signature. Only the exact no-write 412 allows a later Review to replace
the proof. Other failures, including unknown 412, do not rotate key or proof.
After full document reload, disconnect/sign-out or pagehide, inspect current
state before creating again: this is not durable recovery or cross-tab key sharing.
Aborting a request does not undo an already accepted write. Acceptance retries
must refresh; they never create another grant.

The management page has separate in-app inbox and workspace-admin invitation
controls, explicit confirm/cancel, safe text rendering, same-origin requests,
revision errors, no automatic write retries and disconnect/pagehide cleanup.
Both clients reject stale invitation responses before any 401 invalidation side
effect; genuinely current 401 still disconnects. Request-generation/busy guards
remain necessary when AbortController is ignored, and do not cancel server writes.
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

Invitation binding digests now include policy realm, principal ID and generation
for both inviter and invitee. Legacy digests remain stored but invalidated, never
backfilled into current authority. Synthetic A→B→A/disable-reenable tests do not
mean identity administration is enabled; see the [closed foundation](../../docs/principal-authority.md).

## Counter exhaustion

Invitation revisions and their captured workspace/inviter/member revisions use
the same [bounded-counter contract](../workspaces/README.md#bounded-mutation-counters).
A missing prior membership retains the explicit zero sentinel. Accepting an
invitation preflights both membership/invitation increments, the policy epoch and
two audit events before changing either record. Saturation returns a safe `409`
with the pending invitation and all policy/audit state unchanged. Decline/revoke
do not advance membership or policy counters, but still need an invitation
increment and audit slot. Same-key creation replay leaves invitation, membership
and audit state unchanged; its existing write transaction may still issue policy
persistence SQL, and Cookie authentication maintains its security clock.
Existing schema/layout stays unchanged; persisted over-limit counters fail
closed without echoing, clamping or repairing the record.
