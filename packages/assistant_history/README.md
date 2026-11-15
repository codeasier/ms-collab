# Owner-scoped assistant saved history

This is a small **saved-display model and HTTP adapter, not an ACP client or an
agent runtime**. It provides useful owner-bound history reading and reversible
archive/restore without pretending to execute, reconnect or replay a session.
It starts no process, uses no provider SDK, opens no provider/network connection,
and reads no HOME, credentials, raw native locator, production log or old history.
See [migration assessment](MIGRATION.md) for the verified upstream boundary.

## Explicit setup

A caller configures an existing `SQLiteWorkspaceService` and a trusted identity
provider exactly as for [WorkspaceApplication](../workspace_api/README.md), calls
`initialize(store)` once to create/validate the dedicated history tables, and
constructs `AssistantHistoryApplication(store=store, providers=..., provider_key=...)`.
The `assistant` module must already be installed and enabled; there is no default
user, automatic module enrollment, login provider or production launch command.

History stays in dedicated tables in the **same SQLite database and transaction**
as current identity, membership, workspace, dependency modules and audit. It is
not exposed by the generic resource CRUD API. The schema is explicitly validated;
unrecognized/degraded tables fail closed, not silently repaired or imported.

Two trusted Python ingress helpers are intentionally **not HTTP routes**:

- `create_conversation(store, owner=..., workspace=..., conversation=..., title=...,
  required_modules=("assistant", ...))` creates a local saved display record only.
- `append_events(store, owner=..., workspace=..., conversation=..., events=(...),
  expected_revision=...)` appends a bounded batch of `DisplayEvent` values.

They accept a trusted local principal ID, **not an untrusted user credential**.
A future producer must bind it to its verified actor and select all source read
dependencies. There is no real ACP ingestion path in this package. A deployment
must not expose these helpers as user-controlled HTTP calls or use them to ingest
real source data before separately implementing source-specific authorization,
provenance checks, redaction and isolated execution where applicable.

## Authorization and non-resurrection

Every access requires a currently valid trusted credential, active principal,
active workspace, explicit current membership, enabled `assistant` read capability,
and the original owner. Workspace admins and platform operators do not inherit
another owner's history; a shared workspace does not make it public.

The record also captures an authorization fence: fixed workspace/owner/conversation
coordinates, policy realm, current principal generation and issuer/subject,
workspace and membership revisions, and the revisions of its explicit module
read dependencies. Every read, append, archive and restore checks current rights
and that exact fence in the policy transaction. Misplaced, renamed or reowned
conversation rows fail closed even when their revision numbers happen to match. Changes that
advance captured membership, workspace or dependency revisions cannot resurrect
an older privileged history merely by restoring access. This is deliberately
conservative: even a harmless revision change locks the old record. There is no
rebind/recovery/migration route.

Saved fences now include the policy realm and principal generation, so a simulated
binding A→B→A or disable/reenable cannot revive an older record. Legacy records
without those fields remain stored but hidden; there is no automatic upgrade.
Actual identity transitions remain code-level closed until other persisted ACLs
and long-I/O consumers are migrated. See the [authority foundation and remaining
gates](../../docs/principal-authority.md). This is not a directory-management API.
Resource-specific dependency lineage is **not implemented**; module-level fences
alone must not be claimed as sufficient for future real tool/resource content.

Writes additionally require current assistant write permission. HTTP credentials
are revalidated after the callback and before commit, so an expired credential
rolls back state and audit. Policy decisions use one SQLite transaction snapshot:
a concurrent revocation after a read has been authorized affects subsequent
transactions, not bytes already authorized/read. This is not instantaneous
cross-transaction cancellation or a tamper-evident database. Capabilities are
current hints, never reusable grants.

## HTTP contract

Prefix: `/api/workspaces/{workspace}/assistant/conversations`. The configured
identity provider verifies `Authorization: Bearer ...`; cookies, client role/owner
headers and arbitrary token contents are not identity. Responses use the existing
API envelope and `Cache-Control: no-store`.

| Method and suffix | Meaning |
| --- | --- |
| `GET ?state=active` | Owner's authorized metadata, at most 100 records. `state=archived` is also supported; default is active. Inaccessible dependency/fence rows are omitted without titles. |
| `GET /{id}` | Current owner metadata plus explicit capabilities. |
| `GET /{id}/events?after=0&limit=50` | Ordered saved display records, not replay. `limit` is 1–100; response includes revision, total, next_after and has_more. |
| `GET /{id}/events?after=N&limit=50&revision=R` | Fixed-revision continuation. Nonzero after requires revision (428); any concurrent append/lifecycle change rejects an old revision (412). Restart from the first page, never combine revisions. |
| `PATCH /{id}` | Strict `{"state":"archived"}` or `{"state":"active"}`, with `If-Match: "R"`. Owner + current read/write + fence are rechecked. Stale is 412; absent precondition is 428. |

Archive/restore changes state and revision atomically with audit; a same-state
request at the current revision is a no-op. Archive retains saved events, does
not delete data, cancel work, terminate a process or contact a provider. Archived
history can still be read with current access; trusted appends are blocked until
an explicit valid restore. Restore does **not** grant execution or resume rights.
There are no create/append/prompt/execute/resume/export/delete HTTP routes, SSE,
background jobs, tool approvals or sharing routes. Execute/resume/export
capabilities are always false. Permission errors do not echo stored content.

## Display-only event contract

Kinds: `user_message`, `assistant_message`, `agent_thought`, `tool`, `notice`.
Each has `text` (up to 8 KiB UTF-8); tool adds a bounded `tool_name` and reported
status (`pending`, `in_progress`, `completed`, `failed`, `canceled`). A tool report
is not proof of execution. `agent_thought` means an actual upstream event already
explicitly marked user-visible, never inferred or private model reasoning.

`project_observation` requires an explicit `audience` list containing only `user`
and/or `assistant`. Missing audience fails; assistant-only events are dropped.
Unknown fields are rejected, including raw tool objects, inputs/outputs and
locators. This function is a small allowlist adapter, **not ACP wire compatibility
or a secret scrubber**. The trusted caller must sanitize text before storing it.
Titles/text/tool names are untrusted literal text: clients must not render HTML,
execute commands or automatically fetch links in them. The optional [saved-history UI](../../frontend/assistant/README.md) renders only these literal records through the unified cookie/session entry. This API itself has no renderer.

Limits: 100 conversations per owner/workspace; 2,000 events and 1 MiB canonical
ASCII JSON per conversation; 100 events per append. Revisions are safe integers.
Events are immutable; sequence/count/byte corruption fails closed. Audit stores
only a hashed conversation scope and revision, not titles or event content.

## Tests and incomplete boundaries

Run the synthetic suite on CI:

```sh
python3 -m unittest discover -s packages/assistant_history/tests -v
```

Coverage includes owner/workspace/operator isolation, explicit audience, literal
malicious-looking text, current permission/dependency loss and restoration, persisted scope misplacement,
principal-generation ABA/legacy-fence denial,
revision-fixed pagination, archive/restore, concurrent lifecycle CAS, schema and
record corruption, transactional append and credential-expiry rollback, bounds,
and a loopback WSGI HTTP read/PATCH with a test verifier. No test starts an ACP
process, uses a real login/provider or reads production data.

Not implemented: ACP transport/runtime, prompt submission/cancellation, OS or
container execution sandbox, native session persistence/resume, provider
credentials, real network/MCP/tool invocation, resource-level lineage/redaction,
permission approval UI, streaming, retention/deletion/export or
production integration. A private HOME alone is not a security sandbox. These
remain independent prerequisites, not switches that this package enables.
