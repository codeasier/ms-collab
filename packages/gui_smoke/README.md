# Authorized GUI smoke observations

A configured, read-only view of **explicitly published saved task observations**.
This is not a task runner, live panel proxy or a complete migration of the OBS
GUI smoke product. It starts no process, launches no browser/job, contacts no
provider, reads no environment credentials and exposes no production endpoint.
The only HTTP integration test uses a temporary local WSGI server and a synthetic
verifier. See [migration assessment](MIGRATION.md) for the inspected source and the
external panel boundary that must not be copied into a public multi-user service.

## Explicit configuration and storage

Call `initialize(store)` on an existing `SQLiteWorkspaceService`, then configure
`GuiSmokeApplication(store=..., providers=..., provider_key=..., connections=(...))`.
There is no default login, listener, user, module installation or connection.
The trusted module catalog/setup must separately approve and enable `gui_smoke`.
Each frozen `SmokeConnection` names one exact `SmokeScope(workspace_id,
connection_id, repository_id)` and explicit nonempty `readers` and `publishers`
sets of local principal IDs. IDs are logical identifiers, not provider addresses,
repository URLs, credentials or guessed ownership. The factory accepts at most
100 configured scopes and 100 principals per set.

`publish_snapshot(store, configured, actor=..., tasks=(...), observed_at=...,
expected_revision=...)` is a **trusted Python ingress**, not an HTTP route or an
external adapter. The actor must already be a verified local principal, explicitly
listed as publisher, and currently authorized for module read and write. Every
selected reader must currently have module read permission. A future adapter
must prove the source repository/scope, select content approved for that audience,
and sanitize it before publication. A credential's visibility alone is not a
publication grant. Nothing in this increment loads data from the old service.

Publication atomically replaces one bounded snapshot with a strictly newer
observation time and expected revision (initially 0). State and audit commit or
roll back together. Concurrent publishers cannot silently overwrite one another.
Audit stores only a scope hash and revision, not task labels or content. Dedicated
SQLite tables are separate from generic resource CRUD, in the **same transaction**
as identity, workspace, membership and module policy. Degraded schemas fail
closed rather than being silently upgraded or imported.

## Current authorization and visibility

Every read requires a trusted bearer credential in standalone mode, or the
[unified host](../application_host/README.md)'s exclusive cookie/session boundary,
plus a current active principal,
explicit workspace membership, module read permission and the current connection
reader allowlist. Workspace administrators and platform operators receive no
implicit connection access. Shared workspaces are not public or cross-space
aggregates. Responses are `no-store`. Standalone mode does not accept cookies;
the configured unified host instead rejects all bearer headers. Actor/role headers
and caller URL parameters are never credentials or configuration. See the
[session UI](../gui_smoke_ui/README.md) for the optional read-only browser.

The stored envelope binds the exact workspace/connection/repository coordinates,
revision and observation time. It also captures workspace/module revisions and,
for each explicitly approved reader, issuer/subject and membership revision.
Read access requires those fences to match current policy. Removing a configured
reader applies only to a request-serving instance that has loaded the new frozen
configuration; an old process retains its old allowlist during a partial rollout.
Shared SQLite membership/module revocation does not depend on that rollout. A
republished narrower audience also gates old readers of that snapshot, but old
publishers must be drained/reconfigured for a lasting configuration-only removal:
a still-authorized publisher with old configuration and the current revision can
explicitly republish its original audience. Adding a configured reader
does **not** expose older snapshots until explicit republication. Membership changes or module/workspace revision
changes likewise need an explicit new publication, not automatic resurrection.

V2 snapshots capture policy realm and each reader's principal generation, as well
as issuer/subject and membership revision. Binding A→B→A or disable/reenable no
longer restores an older snapshot in synthetic transition tests. V1 snapshots are
retained but unreadable until an explicit newer publication recaptures authority.
The publisher/configured local-ID grants are not yet fully generation-aware;
actual identity transitions remain code-level closed. See the [authority consumer
matrix](../../docs/principal-authority.md). Policy reads linearize at one SQLite
snapshot; later revocation cannot retract already authorized/read bytes. This is
not instantaneous cancellation or a tamper-evident database.
HTTP assertion expiry is checked after the read and before transaction completion.

## HTTP contract

Prefix: `/api/workspaces/{workspace}/gui-smoke/{connection}/{repository}`.

| GET suffix | Meaning |
| --- | --- |
| `/tasks` | Newest-created saved task briefs, then ID descending. Exact `status`, `source`, `branch` filters; `limit` 1–100 (default 20), `offset` 0–500. |
| `/tasks/{id}` | Selected task, reported summary and optional stage observations; unknown ID is 404. |
| `/summary` | Counts of reported task states in this published snapshot only, not live queue positions, worker health or a release gate. |

All three accept optional `revision`. A nonzero offset requires it (428); a
changed revision is 412. Restart pagination rather than combine snapshots. The
response `source` names revision/observation time and always declares
`live_provider_verified=false`, `full_history_complete=false`,
`execution_enabled=false`. No record is `available=false`, revision 0 and no
observation time; a deliberately published empty snapshot is `available=true`.
Missing data never masquerades as a verified successful run or complete history.

All capabilities other than read are false. There are **no** HTTP publication,
refresh, health proxy, queue proxy, branch/test catalog, submit, cancel, retry,
reproduce, schedule, prune, logs, results passthrough, SSE, report HTML, artifact
or bundle download routes. Even read-only-looking external catalog calls may
perform network/git activity, so this package does not proxy them.

## Selected display model

`project_task()` accepts an exact normalized dictionary, **not raw panel JSON**:

- ID, canonical reported status (`pending`, `running`, `success`, `failed`,
  `cancelled`, `unknown`), source (`web`, `scheduled`, `api`, `retry`, `unknown`),
  branch and explicit created/start/finish timestamps.
- Separate actual `commit` and `requested_commit`: null or full lowercase 40-hex.
  A requested revision is never substituted for actual execution evidence.
- Optional summary: nonnegative passed/failed/skipped/total counts that reconcile.
  A reported success with explicit failures is rejected, not silently corrected.
- Optional stages: at most 20 unique IDs, bounded name, reported status and times.
  A stage can additionally be `skipped`. Missing details stay null.

Unknown external status aliases can be explicitly mapped with `reported_state()`;
it does not infer success from completed/finished/positive case counts. Input
schema validation itself accepts only the canonical enum. Task states are
observations, not an execution state machine or independently verified results.
A later publication may correct an earlier report; no runner action is implied.

Times require timezone offsets; future/reversed or pending/running contradictory
timings fail. Duration is derived only when both endpoints exist, otherwise null.
Stage sums are not claimed to equal task duration. Source local-time values need
explicit timezone normalization in a future adapter, not a guessed default.

Task and stage fields reject additional data, including submitter identities,
notification emails, runner addresses, raw errors/logs, paths, URLs and report
HTML. This allowlist is **not a secret scrubber**: branch/stage text still requires
trusted selection and sanitization. Render every string as literal text, never
HTML, executable commands or an instruction to fetch a link. No renderer is
included. Bounds are 500 tasks and 1 MiB canonical JSON per snapshot; identifiers
are 1–128 ASCII characters, branch/stage names at most 400 UTF-8 bytes.

## Verification and remaining work

```sh
python3 -m unittest discover -s packages/gui_smoke/tests -v
```

The hosted synthetic suite covers fixed scope, explicit audiences, no retroactive
sharing, current permission loss, revision-fixed paging, missing/unknown evidence,
strict projections, schema/corruption failures, publication CAS/rollback, expiry,
limits and actual loopback HTTP. No real panel, production runner, test account,
notification recipient or source repository is contacted by these tests.

Not implemented: a network adapter, provider authentication/ownership, resource-
level source provenance/redaction, live synchronization, isolated task execution,
submission/cancellation/retry/reproduction, scheduling/notifications, file/log
access, independent report origin, browser UI, production login/deployment or executable
identity administration. These are separate reviewed prerequisites, not disabled
buttons or implicit promises of compatibility. Do not expose the synthetic
verifier or turn the trusted ingress into a user-controlled HTTP endpoint.
