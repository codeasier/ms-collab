# Identity-bound responsibility references

Ownership is a configured observation, not an authorization grant or an upstream
assignment. This increment does **not** enable directory mutation, account
provisioning, automatic identity transfer, review/merge actions or production use.
The [closed platform identity gate](../../docs/principal-authority.md) remains in
place in both the service and SQLite schema.

## Independent authority and ownership

A responsibility connection's reader/editor/publisher grants bind the exact
policy realm, local principal ID, issuer, subject and generation. Every operation
still needs current workspace membership, responsibility/community module gates
and the underlying community reader grant. Edit/publication additionally require
the separate configured grant, current administrator role and write capability.
A matching owner ID, shared space or public repository grants none of these.

Developer and committer owner IDs, including cross-field backups, share one
persistent reference history. Each nominated identity records its realm/binding/
generation and same-space membership revision. An ordinary config save captures
only never-before-nominated IDs. Previously seen IDs retain their original pins,
even after removal, reordering, moving to another field, or reuse as a backup.
Retired entries are not candidates or permissions and are never silently pruned.

All current nominations must be active members. If any retained pin is stale,
ordinary PUT fails with `409 reference_reconfirmation_required`, without a write
or audit. Changing a name/path is not authority to transfer ownership to a new
identity. An editor may deliberately replace every use of a stale ID with a
different eligible ID; the retired ID's old pin remains. Membership revisions
conservatively require confirmation after same-record role/lifecycle changes,
including revoke/restore. Unrelated people/spaces do not invalidate references.

## Historical display is not current attribution

An already-authorized current reader can inspect retained **bound** fields.
`GET /config` and successful save/reconfirm responses add:

```json
{
  "references": {
    "state": "reconfirmation_required",
    "stale_ids": ["synthetic-owner"],
    "confirmation_token": null
  }
}
```

`state` is `current` or `reconfirmation_required`. A current config has no stale
IDs. The frontend marks stale labels as historical/unconfirmed local IDs, not a
claim about today's person. Issuer/subject/generation and private directory
profiles are not returned. There is no profile lookup or identity conversion.

Any stale active reference blocks **all** candidate views with 409: Mine, All,
Field and the Issue-only Unassigned view. No stale ownership labels, matching
counts or paths enter projection. A read-only user cannot reconfirm by possessing
a displayed configuration or a token.

## Explicit ownership reconfirmation

A current eligible editor's config response can contain a 64-hex
`confirmation_token`, including for an empty/current configuration. It is a
precondition, not authentication, a permission token or a transferable capability.
It is absent for non-editors or if a nominated ID is not an active member.

`POST /reconfirm` accepts exactly `{fields, confirmation_token}` and the current
record `If-Match`. It does not accept actor, realm or generation claims.

The proposal binds exact scope and realm, record/config/grant revisions, complete
canonical fields and reference history, all proposed current identity/membership
pins, and the proposing editor's identity/membership. It also pins workspace and
both module revisions plus the underlying community configuration digest.
Consequently target identity ABA, editor demote/promote, module pause/restore,
workspace suspension/restoration or community regrant between reading and
confirming invalidates the proposal, even after current permissions return.
A global policy epoch is not used to reject unrelated changes.

In one `BEGIN IMMEDIATE` transaction the service reauthorizes, checks If-Match,
requires the exact reviewed fields, and recomputes the proposal. Only a match
replaces pins of currently nominated IDs. Retired pins, field order, paths and
original publisher provenance remain unchanged. Config/record versions and a
metadata audit advance atomically; expiry or persistence/audit failure rolls back
the complete change. Stale proposals are not automatically refreshed or retried.

The UI presents a separate confirmation action. It never reconfirms on ordinary
Save, reload, reconnect, scope changes or failure. Cookie/session requests use the
shared current-session/CSRF boundary; they do not manufacture bearer credentials.
Cancellation cannot undo an already accepted write, but a late reply cannot
restore old scope/identity state. No implicit member reactivation is provided.

## Path publication provenance

Prepared evidence stores its original publisher's identity/generation, membership
revision and responsibility grant revision, scoped to its observation metadata.
A stale publisher, lost current reader/publisher grant, lost administrator role or
changed membership makes retained paths unavailable. The current community
revision and exact-content approvals remain independently required.

Invalidated evidence is **incomplete**, never complete-empty. No old paths are
passed to projection, and the old observation timestamp is not shown as current.
Ordinary config edits and owner reconfirmation do not repin the publisher.
Only a new explicitly authorized `publish_prepared` operation can publish fresh
provenance. Stored evidence is preserved when it becomes unavailable; it is not
automatically erased or republished. There is still no live path acquisition or
HTTP evidence-upload route.

## Legacy and grant recovery boundary

The SQLite table shape is unchanged. Policy, fields and evidence use exact
versioned canonical JSON envelopes. Valid persisted combinations are only:

- **All bound:** normal service operations may authorize against the stored pins.
- **All legacy:** inaccessible to ordinary reads/writes; only the explicit trusted
  destructive recovery helper may strictly parse it.

All six mixed legacy/bound combinations fail closed, including in recovery.
There is no readable legacy-fields mode, partial auto-migration, default generation
or decoder that captures current identity. Unknown/malformed records return safe
storage failures. Normal reads and failed recovery leave the old data untouched.

`replace_policy(store, new_policy, actor_id=..., expected_revision=...)` is an
explicit **destructive grant reset**, not an ordinary field edit or HTTP endpoint.
It requires a current workspace administrator, both module gates, responsibility
write and a current underlying community reader grant, without relying on the
stale responsibility ACL being recovered. New recipients must be active members.
`actor_id` is a trusted server integration input, **not authentication**; a host
must authenticate its administrator independently and never forward client actor
claims to this helper.

After exact CAS, reset captures fresh grants, clears old fields/evidence and
observation metadata, creates bound empty records, increments versions and writes
one metadata audit. Identical recipient lists still constitute explicit regrant.
Bound grant revisions increase by one; strictly legacy grants begin from logical
zero. No old audience data is released to newly captured identities. A real-data
reset requires explicit authorization for that data loss and an independently
protected recovery copy. This code change performs no such migration or reset.
Non-destructive legacy recovery is not implemented.

Audits retain the existing `resource.update` action vocabulary and distinguish
reconfirmation/reset with fixed `responsibility.references.reconfirm:` or
`responsibility.grants.reset:` object prefixes plus a scoped hash. They contain
no fields, external identity attributes, paths, tokens or raw requests. No platform
membership, principal, last-administrator or operator transition is performed.

## Bounds and verification limits

Exact integer versions/counters reject booleans and implicit defaults. Record,
config, grant, generation and membership revisions are bounded by
`9007199254740991`; write exhaustion returns 409 before SQL mutation/audit.
Grant/config versions cannot exceed the enclosing record revision. JSON remains
bounded to 1 MiB, incoming config/reconfirm bodies to 64 KiB, and reference history
to 3000 distinct IDs. Capacity failure never drops retired pins to make room.

Tests use synthetic identities, temporary SQLite and explicit test-only future
transitions. The production mutation guards remain closed. Hashes provide binding
and CAS preconditions, not tamper evidence or a sandbox against a database owner.
Restoring a complete old policy backup can restore historical authority state;
this design has no external anti-rollback ledger. Real identity-provider and
production acceptance remain separate gates.

[Service](README.md) · [Tests](tests/README.md) · [Frontend](../../frontend/responsibility/README.md)
