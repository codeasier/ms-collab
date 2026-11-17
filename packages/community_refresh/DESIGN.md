# Community acquisition lifecycle and boundaries

## Composition and authority

The default unified host has no refresh credential loader. Enabling refresh is a
strict immutable boolean/configuration pair; it grants no roles, Community
permissions, connection membership, provider access or authentication. An exact
`CommunityRefreshConfiguration` supplies a trusted scoped loader and optional
trusted `get(path, credential, connection, *, remaining)` transport. Only the
transport is synthetic in acceptance tests. The controller never accepts an
HTTP actor or a foreign authenticated service through the host.

Before admission, current session/CSRF, identity, workspace admin status,
Community module write permission, bound refresher grants and the exact revision
are checked under the session/policy writer lock. Admission captures full
principal binding plus membership/workspace/module revisions and config digest.
The original Community logic still checks the current identity/config/CAS before
and after every provider page, after the loader, and inside the final writer
transaction. The lease adds deadline and ownership fences rather than replacing
these checks. Independent approval, grant change, logout, expiry, identity ABA,
revocation or module/workspace change prevents stale publication.

Grant replacement through Connections already clears the prior snapshot and
approvals atomically. Refresh collects a private observation. Only unchanged
fingerprints with still-current approval provenance survive; altered/new content
requires a separate complete-set approval. A stale approval is not a reason to
block an otherwise authorized refresh, and raw persisted state is distinguished
from the business view that suppresses invalid approval provenance.

## Transactions and schema

`community_refresh_leases_v1` and its one defined state index are explicitly
installed in the policy DB, never created/migrated by a request/host constructor.
Exact Community/lease table and index SQL is checked, rejecting added triggers
and indexes on either dedicated table. Each admission uses `BEGIN IMMEDIATE`
and sees all active owners, including other processes. It checks global=2,
actor=1, scope=1; no expired reservation is automatically removed.

All lease writes capture the active rows before their own change. After core
policy and audit persistence, a before-commit guard reads back the exact expected
lease, active-owner set, scope record and complete persisted policy/audit state.
Thus cross-table side effects cannot quietly change publication, identity,
audit, or release another worker's slot. Admission failures roll back before any
secret load. Completion failures roll back snapshot, approval, audit and the
completion together. Confirmed failed cleanup marks only the owned lease failed,
with the same post-persist readback. If that verification fails, the transaction
rolls back and the lease remains active. These checks are transaction integrity,
not protection against a hostile database administrator or filesystem.

## Time and resource ownership

A monotonic clock guards the 30-second operation budget; stored aware wall time
also fences active reservations. Checks include after waiting for admission and
final writer locks, before/after the credential loader, every page boundary,
normalization and after policy/audit persistence. The session store retains its
last current-session check immediately before commit.

The default transport performs one page in one daemon worker. All sockets and
responses belong to that worker. It registers TCP ownership before connect and
TLS ownership before handshake; no competing thread detaches/closes them. It
pins the first validated public DNS address, verifies TLS for `api.gitcode.com`,
sets bounded per-operation socket timeouts, and checks the whole-page deadline
around I/O and parsing. It requires a single bounded ASCII Content-Length,
application/json, identity encoding, no transfer/content-range framing, status
200, exact body length and duplicate-free finite JSON. The owner preserves the
socket file lifetime when HTTPResponse handles `Connection: close`.

Completion is signalled only after response and socket cleanup have returned.
The calling thread waits no longer than the remaining operation budget. If DNS,
I/O, close or other worker work has not confirmed cleanup by then, it raises
`RefreshCleanupError`; the controller retains the durable active lease, even if
the worker later finishes. A known close failure likewise retains the lease.
There is no automatic retry, timer-based eviction, worker reaper or recovery API.
Explicit trusted recovery must first prove the owner and every resource have
terminated, then fence the old lease so a late worker cannot publish.

This is **not hard cancellation** of DNS, Python, trusted loader code, SQLite lock
waits, fsync or COMMIT. The loader must be bounded and resource-free on return or
exception; custom trusted transports must honor remaining time and either finish
all owned cleanup or raise `RefreshCleanupError`. Python cannot enforce those
contracts on hostile trusted callbacks. An independent worker may outlive the
response, but its reservation remains occupied, bounding owned page workers to
the admitted population. Serving-thread count, queued HTTP requests, durable
history retention, operational cost/rate budgets and process supervision need
separate deployment limits. The existing Connection max-pages means an observed
subset, not proof of repository completeness.

No environment discovery, real provider call, startup/runtime auto-registration,
new login or identity transition, background sync, deployment, tag, network or
security setting change is included. Synthetic CI is not real GitCode framing,
OAuth, production scalability or service-readiness evidence.
