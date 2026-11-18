# Multi-user web review: design and release gates

**Status: proposed, not mounted.** The Pipeline web page reloads an already
stored snapshot. It does not fetch a remote pipeline, show a pending
replacement or publish one. The [private terminal workflow](REVIEW.md) runs
under a trusted integration with a single, explicitly named operator. Do not
adapt it by calling the terminal or the direct publisher from an HTTP handler,
converting a session Cookie into a bearer credential, or treating a digest as
permission.

In the earlier pipeline interface, refreshing a selected run or its history
was a module read: the service could answer from cache or refresh its own
observation. That behavior is not evidence of authority to show a private
candidate to another person or publish it for a new reader audience. See
[provider protocol provenance](MIGRATION.md) for the narrower evidence behind
the list/detail adapter.

This design concerns a bounded observation of **existing runs only**, for one
workspace + connection + pipeline. It does not trigger a build, read logs,
alter the provider, send notifications, infer artifact lineage, or change an
installed service, login or historic data. No publication implies a release
decision or proof of a complete history.

## The comparison available today

`describe_replacement(scope, expected_revision, old_text, new_text)` compares
two snapshots **already supplied by an authorized caller**. It validates both
envelopes against the exact scope, refuses an empty or noncanonical proposed
snapshot, and keeps the SHA-256 of each input's original UTF-8 bytes. The frozen
result has before/after counts and sorted removed run IDs, not the raw
snapshots. Even counts, IDs and hashes may reveal private information: do not
return the result to a requester who cannot inspect the source.

The terminal already uses this function. It checks the operator's answer
outside the database transaction. The writer separately rechecks freshness,
current authority, the confirmation window and the target revision and bytes. Someone who can construct a comparison result
has proved none of those things.

## Who can do what?

| Action | Additional authority, beyond a currently valid session |
| --- | --- |
| Read a published snapshot | Today's workspace membership and enabled pipeline-module read rights. Existing list/detail/task/duration and scope discovery stay unchanged. |
| Acquire a candidate | An independently provisioned source grant for this principal, exact workspace/connection/pipeline, origin, projection version and scan limits. It permits bounded upstream reads and private staging, not publication. |
| Inspect the candidate | A separate grant for this precise private source content, plus current permission to see the target baseline and removed IDs. Do not disclose the candidate, its digest, counts, titles, removals or the assignee to a requester who lacks this grant. |
| Publish the reviewed replacement | Current source-inspection rights, a source-owner release policy for these fields and this destination/audience, and destination-publication authority for this exact replacement; any additional separation rule below must also hold. The candidate ID, digest or scope URL is not a grant. |

The trust root is reviewed provisioning by the source and destination owners.
Being allowed to see the source and write the destination does not by itself
authorize onward disclosure. The source must expressly allow that projection
from that origin/scope to this destination and audience. This can be a clause
in a jointly approved policy; it need not add another role. The final decision
still binds the actual bytes. A local transaction can verify its current policy
generation, not prove the instantaneous state of a remote provider's ACL.
Role, access to a token, the ability to read a stored run or being a platform
operator do not establish those decisions. Bind approved actors to the policy
realm, local principal and verified issuer/subject/generation with authority
and membership revisions. Pin the source binding, policy version and intended
audience as well. Do not capture a new grant from the current request or
silently rebind a revoked identity. A shared workspace is not an exception.

**There are two different products to choose between, not a single checkbox
that weakens an existing approval.**

- **Independent self-review:** several operators can use the service, but each
  may fetch, see and approve only their own pending candidate, in the session
  that started it. The operator must hold all three grants. There is no shared
  queue, reassignment or discovery by another administrator. This prevents acquisition alone
  from publishing; it does not provide independent approval.
- **Separated collection and approval:** an explicitly granted collector receives
  at most an opaque, content-free acknowledgment. A separately granted reviewer
  is selected by trusted policy, never by a request parameter. Only that
  reviewer sees the private candidate and may approve the complete contents for
  the destination. Require distinct verified principal bindings under an
  authoritative identity scheme; different accounts alone do not establish
  different humans. A reviewer who cannot see the underlying contents cannot
  approve a digest on someone else's word.

Neither model is selected or configured by this change. In either model, the
existing snapshot is visible to **all current and future members with pipeline
read access** in that workspace. A copy committed there does not disappear
when the original source grant later expires. If the owner needs a fixed
recipient list, per-run readers, retractable history or a distinct third
publisher, do not use this destination or either proposed flow unchanged.

## Proposed private candidate lifecycle

1. **Prepare.** Admit one fixed-scope, same-origin Cookie request with CSRF and
   a required expected target revision. Reject browser-supplied actors,
   audience, content, destinations, provider URL or credentials. In the
   session/policy database, check current grants, exact immutable source
   configuration, healthy baseline, safe revision and capacity **before**
   secret loading. Pin the baseline's exact bytes, not revision alone. Reserve
   and commit a nonce-owned durable lease in the admission transaction; a
   capacity read is not a reservation. Only then load the scope/origin-bound
   credential via an explicit loader outside the transaction. Reauthorize
   before/after loading, before each fixed GET and before staging. Recheck
   lease ownership and generation for dispatch, staging and failure/release. No retries, scheduling, background trigger or fallback
   to implicit HTTPS behavior.
2. **Stage.** Freeze the entire canonical candidate once. A repeat read obtains
   new observation times, so refetching at confirmation is not confirmation of
   the reviewed bytes. Reject empty, invalid, out-of-scope, noncanonical or
   stale observations. Keep the bounded candidate **out of ordinary resource
   tables**, discoverable snapshots, generic CRUD, logs and browser storage.
   Staging/releases of provider capacity do not update the published snapshot.
   Any ID returned to either party is opaque and never sufficient for access.
3. **Review.** Reauthorize private access on every request and use the frozen
   bytes. Show the exact destination, origin/limits, baseline revision, audience,
   complete escaped content, counts and every removed run ID. A smaller
   complete replacement removes items from the local subset only. No default
   affirmative choice; cancellation is not confirmation. A short-lived,
   server-issued decision challenge binds this review session, candidate
   generation, source/destination policy, baseline bytes, new bytes and audience.
   A read-only GET must not issue or consume a decision with side effects.
4. **Publish once.** A separate same-origin, CSRF-protected action carries a
   reference/decision, **not** replacement bytes. Under the same database
   writer lock, verify current session and grants, actor/role and configuration
   pins, both participants' still-current policy under the separated model, the source
   release-policy generation and permitted destination/audience,
   expiry, candidate and challenge state, and original revision **and bytes**.
   Recheck snapshot identity/time rules. Replace the resource, append
   metadata-only audit and consume the candidate/challenge in one transaction.
   After persistence, recheck the stored result/receipt and all applicable
   grant, candidate and challenge deadlines against the trusted clock floor.
   Reject time rollback. Keep the session's own current-actor check last
   before commit. Failed checks, competing requests, revoked/regranted
   scope or timeouts cannot partially publish. An uncertain response prompts
   an authorized read, not an automatic POST; a higher revision does not by
   itself identify the winning publisher.
5. **Discard/expire.** Allow an authorized explicit discard and bounded
   expiry; invalidate outstanding challenges together. Reauthorization does
   not revive an old generation. Logout/session expiry invalidates that
   reviewer's challenge. Do not retain another person's session token to
   impersonate them; whether an independent collector must remain logged in
   after staging is a separate decision. No hidden retry or implicit transfer
   to a new session, user, approver or workspace. A tab/navigation generation
   helps discard stale UI; it does not establish a separate principal or make
   a Cookie session uncopyable between tabs.

## Storage and failure boundaries

- Use a dedicated, reviewed layout in the same policy database. It must
  distinguish the provider lease, immutable private candidate and single-use
  review decision. A database write transaction must never wait for network
  I/O or human input. No per-process dictionary, browser-supplied snapshot,
  portable signed blob, or second non-atomic database as the source of truth.
  The [comparison helper](replacement.py) verifies the structural inputs to
  a comparison. It is not an admission check, identity proof, chronology
  check, receipt, source grant or publisher.
- Reserve bounded provider capacity globally, per actor and per exact scope
  across processes. Keep a private owner nonce and generation for each active
  acquisition. Bound pages/bytes and wall/monotonic time across loader,
  dispatch, body/parse and final write eligibility. Require an explicitly bounded,
  supervised loader and transport. Reject late observations and do not commit
  on a failed final deadline check; this is not hard cancellation of DNS, a
  blocking loader, SQLite COMMIT or already dispatched I/O. If cleanup is
  uncertain, a timeout does **not** free the lease; recovery must first establish worker termination
  and fence that generation. Confirmed completion may release the provider
  slot once the candidate is safely staged; human review uses a separate
  bounded count, byte and retention budget. A second request cannot evict a
  different user's candidate to make room.
- The existing per-snapshot limit is 10,000 bytes; also budget old content,
  metadata, total ready candidates, challenges and consumed receipts. Keep a
  trusted clock floor for expiries across requests; process-monotonic
  timestamps do not compare across restarts. Do not use today's admin-transfer
  receipts or directory cursors as general-purpose candidate storage.
- Isolate pending data from ordinary readers, search, discovery, caches,
  telemetry, error bodies, referrers and shareable URLs. Use no-store,
  nonenumerating errors and server-side checks even after the browser clears
  state. Browser controls must clear candidate data on scope/session change,
  logout, back/forward restoration and stale responses; aborting a tab cannot
  unsend a provider GET. Server revocation cannot erase contents already
  displayed, copied or retained by a client outside its control.
- If persistent storage is proposed, independently review exact table/index
  compatibility with the [admin handover store](../workspace_admin_api/transfer_store.py).
  Use trusted explicit initialization, never request-time DDL. Leave existing
  installed [runtime layouts and backups](../service_runtime/README.md)
  unchanged. Design private backup access, retention/destruction, cold restore
  and invalidation of old candidates/leases/challenges before any installed
  profile is supported. Restoring an older database and an older nonce together
  cannot by itself prove a decision has not been consumed; a stronger guarantee
  needs an independently trusted recovery fence.
- Other trusted ingest and standalone writers still exist. Baseline CAS protects
  this decision from a conflicting write, not every subsequent publication.
  Never describe the proposed path as the sole publisher, a source-wide
  revocation mechanism, provider-atomic snapshot or production cutover.

## Decisions and acceptance before enabling anything

Owners must specify the actual source/version and safe field projection; named
collector, candidate reviewer and publisher; approval model, any required separation of people
and rules for invalidating pending decisions; explicit current/future-reader audience; handling
of nonempty shrinking results, source revocation, private retention and
recovery; and the status of other writers/older consumers. Until these
decisions and technical gates are satisfied, leave acquire, inspect and
publish unavailable via the web. Constructing example grants is not a decision
for any real product.

With fictional data, test two simultaneous principals and sessions in two
workspaces with the same external IDs, plus an independently authorized shared
workspace. Exercise each role independently (including an ordinary reader
and a nonmember operator), interrupted/switching tabs, source/binding changes
and identity/membership regrant. Inspect an entire candidate while confirming
a separate ordinary reader still sees only the old snapshot. Race two
approvals, one stale candidate and an existing trusted writer; require at
most one matching update/audit. Test expiry/response loss, inability to
enumerate unapproved candidates, cross-process quotas, storage/cleanup faults
and restart/restore non-resurrection before claiming the relevant guarantees.
Distinguish test-only identities, lower-layer checks, an actual browser run,
a real provider contract and owner-approved rollout.

**What can be verified now:** structural comparison of two bounded inputs,
compatibility with the existing terminal review, the existing reader path,
and denial of unconfigured Cookie acquisition/candidate/publication paths.
No private web candidate, multi-user approval, production isolation or live
migration is delivered or certified by those checks.
