# Workspace acceptance matrix

**No rows are claimed to pass.** This is an integration-test specification, not
an executed test report. Every implementation PR must map case IDs to executable
tests and record exact commit, environment, result and limitations. JSON fixture
syntax checks or documentation tests do not establish isolation.

Use fictitious spaces `ws_alpha`, `ws_beta`, `ws_shared` and independent admins.
Use a multi-space member with different roles, a nonmember, a disabled principal,
and two resources with the same external ID/path but different workspace owners.
See [examples](examples.json). No real user directory or production data is used.

| ID | Setup/action | Required observation |
| --- | --- | --- |
| W01 | Same module enabled with distinct settings in A, B and shared | Config and resources remain independent; shared has no extra privilege. |
| W02 | A admin requests B resource/members/config by guessed ID | Indistinguishable 404; no mutation or existence leak. |
| W03 | A viewer is B admin; write A with B role/token/context | Denied; effective A role is used. |
| W04 | Anonymous/nonmember reads shared space | Denied; repository public visibility is irrelevant. |
| W05 | Forge body/header workspace ID different from route | Rejected; never silently rerouted. |
| W06 | Same external ID/key/path in A and B; list/get/update/delete | No collision, cache crossover or cross-owner foreign reference. |
| W07 | Authorized search/count/facet/cursor over mixed data | Only authorized space contributes; foreign/revoked cursor rejected. |
| W08 | Direct API/MCP request to disabled module bypassing menu | Denied; member sees precise visible lifecycle state without data. |
| W09 | User switches A to B while A response/event is delayed | A response discarded by UI; B query never inherits A state. |
| W10 | Two tabs in different spaces; Back/Forward, refresh, deep link | Each request uses its canonical scope; no global active-space bleed. |
| W11 | Member revoked during SSE, export/download and query pagination | New serving denied after revoke barrier; subscriptions invalidated. |
| W12 | Member/module revoked with queued and running jobs | Queued denied; running rechecks before effects/results; residual side effects audited. |
| W13 | Idempotent retry after revoke; reuse key across spaces/users | No cached-result disclosure; keys do not alias across scope. |
| W14 | API/MCP key requests another space or capability | Denied even if interactive user has wider rights elsewhere. |
| W15 | A admin submits raw log/token/session/connection as a share | Rejected unless a separately reviewed projection policy permits exact fields. |
| W16 | Offer to shared workspace, no target acceptance | Target cannot read projection, counts or source private fields. |
| W17 | Target accepts exact source revision; target reader has no A membership | Only allowed projection readable; no source connector/execute permission. |
| W18 | Source changes revision/fields after acceptance | Existing snapshot unchanged; new content requires new offer/acceptance. |
| W19 | Revoke accepted share, fail cache/index purge midway | Serving fails closed in revoking; success only after purge confirmed. |
| W20 | Target tries transitive sharing or writes through projection | Denied; source ownership preserved. |
| W21 | Concurrent demotion/deletion of last two admins | Transaction invariant retains at least one active eligible admin. |
| W22 | Expired/replayed invitation or identity-name impersonation | Rejected; issuer+subject match required, invitation not membership. |
| W23 | Platform operator without data membership reads evidence or self-enrolls | Denied; governance is not implicit business-data authority. |
| W24 | Disable module or suspend/archive space, then recover | No implicit task replay or restored shares; retained data follows lifecycle policy. |
| W25 | Duplicate POST, stale ETag and concurrent permission change | Digest conflict/412/428 as specified; no stale authorized mutation commit. |
| W26 | Assistant export/worktree with traversal/symlink or reused session | No foreign space filesystem, evidence, cache or tool credential exposure. |
| W27 | Connector uses approved A binding to request B upstream IDs | Provider scope AND A binding/resource constraints enforced. |
| W28 | Audit sink unavailable during sensitive mutation | Durable intent retained or operation fails; no silent unaudited success. |

Each module must extend this matrix for its own resource operations. In particular,
community projections, responsibility config, artifacts/URLs, pipeline logs,
device tests and assistant evidence need resource-specific negative cases. A
pass in an in-memory demo does not prove a database, queue, connector or filesystem
integration. Required real integration tests run with isolated synthetic resources
and explicit credentials, never implicit production access.

## Release evidence

- Unit policy tests, API integration tests and cross-space storage/worker tests.
- Browser evidence for A/B/shared navigation, repeated clicks, cancellation,
  stale responses and role removal; screenshots use only synthetic data.
- Revocation race tests with barriers, not timing-only sleeps.
- Deployment configuration review for per-space scope and secret boundaries.
- Migration dry-run/reconciliation/rollback evidence, if migration is included.

Until all relevant gates pass, the release description must enumerate which
boundaries are implemented, simulated, unsupported or unverified.

## Evidence snapshots

[2026-11-17 fixed-version migration evidence](migration-evidence-2026-11-17.md)
maps executable evidence and remaining gaps to this specification. A snapshot
does not change the requirements or mark any acceptance row as passed.
