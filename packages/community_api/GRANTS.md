# Persistent community authority and explicit regrant

This is the community data-plane increment following the
[closed principal foundation](../../docs/principal-authority.md). Identity
provisioning/rebinding/disable-reenable and real GitCode login remain disabled.
No existing deployment or real data is migrated by installing this code.

## Configuration is not authority

`ConnectionPolicy` remains a trusted configuration DTO: connection, opaque
credential reference and local-ID recipient lists. Serializing it is **not** a
current grant. Persisted `config` now uses an exact version-2 envelope containing
that DTO, a bounded monotonic `grant_revision`, policy realm and each recipient's
issuer/subject/principal generation. Missing, partial and legacy authority never
gets a default or automatic capture. Requests check current active identity,
workspace/module/role, exact role recipient list **and** the persisted fence.

Ordinary edits retain the original pin for every previously seen recipient ID.
Removing an ID keeps its pin in bounded `retired_principals`; re-adding it is not
permission to adopt a replacement identity. Only a never-before-configured ID is
captured as a new grant. Retirement history and current recipients together are
bounded to 3000 IDs. A new role for the same ID does not refresh its identity pin.
A foreign realm is not adopted through an ordinary edit.

`grant_revision` advances when a normal edit actually changes the DTO, and on
every explicit regrant even if its DTO and identities are identical. This is
separate from the row revision that also changes on refresh/approval. Both are
bounded to the safe-integer maximum and cannot reset through these helpers.
A→B→A policy changes cannot make an old in-flight pin equal again.

## Regular edit versus explicit regrant

All functions below are trusted Python integration points, not new HTTP routes:

- `replace_policy_record(policy, connection, actor, config, expected_revision=...)`
  operates on the existing policy transaction. It checks current workspace admin,
  reads a bound record, enforces row CAS and preserves existing/retired pins.
  Actual edits atomically clear the snapshot/approval set, advance both revisions
  and append one minimal scoped audit. A true no-op writes nothing. The caller
  must enforce any tighter template/manager/upper-cap/current-recipient policy;
  it must not bypass those constraints or append a duplicate audit.
- `replace_policy(store, config, expected_revision=..., actor_id=...)` wraps the
  same operation. A current actor is mandatory; no old actor-less fallback or
  invented audit identity exists.
- `regrant_policy(store, config, expected_revision=..., actor_id=...)` is an
  explicit current-admin action to discard prior authority/content and capture
  new pins. It always advances grant and row revisions, clears retirement history,
  snapshot and all approvals, and commits one scoped audit in the same transaction.
  This is not the normal edit/no-op/initializer path.

The internal write helper is the sole audit owner. It updates under exact scope,
old row revision and old bound config; audit/CAS/storage failure rolls everything
back. Audit contains a hashed scope, actor's local ID, action and new row revision,
not external subjects, credential references, private titles or raw JSON.

## Existing local-ID-only records

Normal reads and regular edits reject legacy plain DTO configuration. They neither
rewrite it nor expose its saved content. The one-time initializer still refuses
an existing table and cannot be used as a regrant/upgrade.

An explicitly authorized `regrant_policy` may structurally validate a known
legacy plain configuration with its existing snapshot and fingerprint list,
verify the expected row revision, and replace it with a new bound configuration.
It **discards** old snapshot/approvals instead of blessing them under the new
identity. Unknown/malformed records remain unavailable; this helper is not an
arbitrary repair/import path. Preserve data for separately authorized recovery,
not ad-hoc SQL fixes or automatic copying of old private observations.

No DDL or core schema migration is needed: the dedicated table remains unchanged,
while request-time strict payload validation prevents old grants from working.
Ordinary runtime installation does not infer or upgrade these payloads. A cold
backup retains their exact bytes; backups are not permission to reset grant
revisions or reinstate old identities. External anti-rollback remains unsolved.

## Approval provenance and recovery

A nonempty approval set additionally records the actual approver's realm/ID/
generation/issuer/subject, its current workspace membership revision, and a digest
of the complete bound configuration. The item fingerprints still cover the exact
scope and normalized item. Empty approval sets carry no approving identity.

Approval remains visible only while that approver is an active current admin,
its membership revision and principal fence match, and it retains the configured
approver grant. Removing/regranting the configuration clears old content. An
identity A→B→A or demote→promote does not revive old approvals. A reader with a
valid grant sees no approved items while the provenance is invalid; downstream
responsibility projections receive the same filtered approval set.

This is deliberately **not** permanent cancellation on every workspace/module
revision. Current reader policy blocks suspended/disabled scopes. Re-enabling the
same scope/module can show an otherwise still-valid historical approval again.
Archived spaces retain authorized local reads; the provenance check does not
accidentally require an active workspace. No new approval/refresh is allowed in
an archive. Already-authorized bytes cannot be recalled.

Structural loading is separate from approval authority. Current valid reviewers
can still inspect the private snapshot when an old approval has become invalid,
and explicitly approve a new set under current grants and CAS. This prevents a
stale approver from making review/recovery permanently unavailable. A principal
whose *own grant* is stale needs explicit regrant first, not approval as a bypass.

## Across external I/O

Refresh pins the actor's realm/ID/issuer/subject/generation plus complete bound
configuration, row revision and relevant membership/workspace/module revisions.
Thus a role or scope disable/restore during acquisition also cancels that operation,
without changing the separate historical approval-read semantics above. It reauthorizes before credential loading, after
loading, before provider execution and before publication. The shipped
`GitCodeProvider.observe(..., before_read=...)` calls the trusted guard immediately
before and after **every** issues/pulls page GET. Revocation/ABA during page 1
therefore prevents page 2 dispatch, not only eventual publication.

The callback is trusted internal code, never a request-selected function. A custom
provider adapter must honor the same guard contract. Standalone provider calls
without the callback remain protocol acquisition only and do not claim platform
workspace authorization. No database lock is held over provider I/O. A dispatched
request cannot be retroactively canceled by a later grant change; no subsequent
page/publication is authorized after a failed guard. Synchronous provider/credential
latency still requires host limits, not an invented hard wall-clock guarantee.

## Verification

[Authority tests](tests/test_authority.py) cover legacy non-adoption, retained-ID
pins, explicit same-DTO regrant, stale CAS, identity and membership ABA, approval
recovery/archive semantics, audit rollback, a two-process regrant race and a
barrier-blocked first page with zero second-page requests after ABA. The shared
[fence tests](../workspaces/tests/test_grant_fences.py) cover strict field/recipient
validation and foreign realms. All identities/providers are synthetic; this is
not live GitCode acceptance or permission to enable identity administration.

Artifact/download grants, other long-I/O consumers and remaining identity-transfer
invariants still need their own migrations; see the full foundation matrix.

[Community API](README.md) · [Provider](../community_sync/README.md)
