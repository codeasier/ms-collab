# Generation-bound artifact and byte authority

This increment builds on the [closed principal foundation](../../docs/principal-authority.md)
and [community grant contract](../community_api/GRANTS.md). It does **not** enable
identity administration, real GitCode login, live cloud accounts or production
migration. All new validation uses disposable synthetic stores and providers.

## Configuration, persistent authority and explicit regrant

`IndexPolicy` and `DownloadPolicy` remain configuration DTOs. Their original
`policy_text` serializers are **not** current persistent grants. Dedicated tables
retain their schemas, while each stored policy now requires an exact canonical
version-2 envelope. It binds the policy realm and every recipient's local ID,
issuer, subject and monotonic principal generation. Exact role-ID lists and all
current workspace/member/module/role checks are still required.

Each envelope has a bounded, independent `grant_revision`. It is not the index
row/content revision. Every explicit regrant advances it, even with identical
configuration and recipients. It cannot reset through initializer retries.
A policy A→B→A change therefore cannot restore an old request/stream pin.

Initializers capture revision 1 only on a first insert. An existing bound row must
have the same DTO, current identity pins and, for bytes, current parent binding;
an identical retry leaves its stored bytes/revision unchanged. Missing, legacy,
partial or stale pins are not recaptured. There is no startup migration or HTTP
identity/grant endpoint. A configured local ID alone cannot authorize a new
identity that later occupies that ID.

Trusted host integration can deliberately replace authority:

- `regrant_index(store, config, actor_id=..., expected_revision=...,
  expected_grant_revision=...)` requires a current workspace admin and exact row
  and grant CAS. It captures current recipient bindings, increments both counters,
  clears candidate and publication, and writes one minimal core audit in the same
  policy transaction. An unchanged DTO is still destructive explicit regrant.
- `regrant_downloads(store, config, actor_id=..., expected_grant_revision=...,
  expected_policy_digest=..., expected_parent_grant_revision=...,
  expected_parent_policy_digest=...)` checks **both** the previously inspected byte
  envelope and the caller-reviewed **current parent index**. It then captures new
  byte recipients and parent binding, increments the byte grant revision, and
  writes its minimal audit in that transaction. A concurrent parent configuration
  or credential-route change is rejected, not silently adopted.

These are trusted functions, not request-selected callbacks or new browser routes.
Callers must actually review the proposed scope, recipients, content bindings and
parent configuration before supplying preconditions. Do not automatically retry a
failed CAS with newly read values. Errors or audit failures roll back all changes.
Audit stores the local actor, hashed scope, action and new revision, not external
subjects, credential references, raw envelopes or content.

## Parent index and independent byte permission

A byte grant pins its parent index's independent `grant_revision` and complete
bound-policy digest. It does not pin the changing content row revision. A normal
private refresh does not by itself revoke the configured byte grant; admission
still requires the exact approved publication, requested publication revision,
object metadata, trusted ETag and independently approved SHA-256.

Regranting the index invalidates old byte grants, including when the same key,
size, timestamp and bytes are subsequently published again. Only a new explicit
byte regrant against the reviewed current parent can restore byte access. Metadata
readership and possession of a hash never grant download authority.

Regrant never deletes, resets or reclaims admission leases. Existing acquisition
and output checks compare full envelopes and original flow pins. Old streams stop
at the next check; already delivered bytes cannot be recalled. Cleanup retains
its fixed store and unique owned lease ID even when the user's authority expires.

## Publication provenance and review recovery

Every non-NULL publication, **including an empty directory**, requires an exact
version-2 envelope containing the canonical snapshot and actual approver proof.
The proof binds current principal authority, workspace admin membership revision,
complete bound index-policy digest and exact snapshot digest. Only `published=None`
means no publication; an empty snapshot is not an approval exemption.

Readers and downloaders require the approver's current principal/grant and active
admin membership to match that proof. Identity ABA or membership demote→promote
cannot revive an old approval. Module/workspace pause and restore intentionally
can restore an otherwise valid historical approval: those state revisions are
not part of persistent approval provenance. Archived scopes retain authorized
local metadata reads, not new refresh, approval or download.

Structure and current approval authority are separate. A structurally valid but
semantically expired proof can remain stored. Authorized current reviewers and
refreshers may derive a private candidate from retained data, but only explicit
whole-candidate approval with current recipients, digest and CAS publishes it.
The reader catalog remains unavailable until then. Admin status reports no current
publication revision while proof is stale; it does not expose proof fields.

Refresh carries the old `published_raw` envelope **byte-for-byte**. It never
reconstructs a proof from the current refresher or decoded snapshot. Missing,
legacy or malformed publication structure is rejected by ordinary read, review
and refresh; it is not treated as merely expired proof.

New candidates reserve 16 KiB within the 1 MiB record budget for the largest
bounded proof and envelope. Approval also validates the exact final serialized
size before writing. Oversized records fail atomically, without truncation or a
success response. A retained older candidate that does not fit requires explicit
recovery/regrant, not hidden loss of its contents.

## Known legacy data

Ordinary reads and initializer retries retain old local-ID-only rows unchanged
but unavailable. The explicit destructive regrant functions may recognize the
strict original policy/snapshot format solely to clear old authority/content and
capture a new grant. Index legacy regrant uses expected grant revision 0; byte
legacy regrant additionally needs its old digest and the reviewed current bound
parent. Mixed, unknown or malformed formats are not arbitrary repair inputs.

No DDL, table-wide backfill, production migration or permission to republish old
private observations is supplied. Cold backups retain exact bytes. Restoring an
old backup is not an approved grant rollback; external anti-rollback remains a
separate requirement.

## Long-running acquisition and output

Refresh and downloads pin current principal realm/ID/issuer/subject/generation,
relevant membership/workspace/module revisions, scope configuration and existing
content/CAS preconditions. Both bearer and cookie refresh paths reauthorize before
and after every ListObjects page. Credential loading and publication have their
own checks. A role or module disable/restore in the middle of an operation cancels
it even if current access later looks identical.

Configured cookie refresh captures the complete flow pin in the **admission
transaction**, before its lease commits. The later synchronizer inspection cannot
choose a new pin after admission. The original pin lives in the owned lifecycle
object; there is no API to resume an old lease under newly captured authority.
Persistent leases provide shared capacity, not transferable execution permission.

Candidate, index event and lease completion remain in the same transaction.
Post-persist session expiry or deadline rejection rolls them all back. Only after
resource release is known can owned cleanup mark the lease failed. Uncertain
socket cleanup retains the active reservation, including when concurrent ABA or
regrant also occurred. Neither elapsed time nor regrant recycles that capacity.
Row revision exhaustion rejects before refresh admission, secret loading or page
dispatch. Grant revision exhaustion prevents regrant, not otherwise valid reads.

No database lock is held across provider I/O. A request already dispatched cannot
be retroactively canceled; a failed guard prevents later pages, publication or
output. Synchronous transport limits and tested deadline/cleanup behavior remain
those of the [configured refresh](../artifact_refresh/DESIGN.md) and
[download service](../artifact_download/README.md).

## Verification and remaining gates

- [Index authority tests](tests/test_authority.py): persistent ABA, explicit CAS,
  legacy non-adoption, approval recovery/raw preservation, exhaustion, audit
  rollback, two-process regrant competition and page-boundary role ABA.
- [Byte authority tests](../artifact_download/tests/test_authority.py): independent
  byte revisions, reviewed-parent races, same-content parent re-publication,
  stream cancellation, legacy denial, cleanup and audit rollback.
- [Admission tests](../artifact_refresh/tests/test_authority.py): the committed
  admission-to-inspection barrier, role/module ABA, row exhaustion and uncertain
  cleanup combined with regrant.

Public catalog, review, status, download, error and audit DTOs do not return
identity proof maps, external subjects, secret references or raw envelopes.
Remaining pipeline/assets/publisher/responsibility/identity-transfer gates are
listed in the [consumer inventory](../../docs/principal-authority.md). No success
in this slice enables identity mutation or claims real provider acceptance.

[Artifact index](README.md) · [Byte downloads](../artifact_download/README.md)
