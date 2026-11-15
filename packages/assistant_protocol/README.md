# ACP protocol preparation, execution still blocked

This package supplies process-free ACP v1 protocol primitives and a small,
owner/workspace-bound **admission refusal audit**. It does not implement or enable
an agent runner. Nothing is mounted by `ApplicationHost` or `ApplicationUI`.
The saved-history API continues to report `execute: false` and `resume: false`.
There is no prompt endpoint, execution button, worker, transport, process launcher,
MCP server, credential loader, network client or production command.

Why this increment stops here: the existing OBS runtime has a trusted legacy
mode and an offline bubblewrap mode. A private HOME only separates state; it
cannot isolate agent-created processes. In this development environment even a
minimal network-isolated bubblewrap launch of `/usr/bin/true` failed before the
program could run: `Failed to create NETLINK_ROUTE socket: Operation not permitted`.
No capability/security setting was changed, no launcher was substituted and no
legacy fallback or agent was started. Having bubblewrap installed is not an
isolation acceptance result. See [source assessment and runner design](DESIGN.md).

## In-memory protocol

`protocol.py` is a narrow, deliberately fail-closed ACP v1 subset, not a claim of
full SDK/provider compatibility. It constructs bytes and consumes bytes in memory.
It does not send them. Initialization advertises no filesystem, terminal or other
client powers; new-session requests use fixed sandbox-internal `/workspace` with
an empty MCP list. A single text prompt is allowed per protocol object. There is
no restore/load/resume, authentication, shell command, arbitrary cwd or env input.

Responses must match the outstanding request and private session. Permission
requests are always refused, filesystem/terminal/unknown client methods are not
implemented, and cancellation/errors retire the object without replay. Bounded
framing rejects malformed and ambiguous messages. Only explicitly user-addressed
text updates can become literal display events; this is audience selection, **not
redaction or proof of safe source data**. Raw tools, arbitrary metadata and provider
locators must not enter saved history. These primitives perform no identity check
and their output is not authority to contact an agent. They are not connected to
the existing history ingress helpers or to the admission audit.

## Audited refusal, not a queue

Trusted setup first initializes the existing policy/history store, then explicitly
calls this package's `initialize(store)`. `ExecutionPreflight` accepts the exact
existing `WorkspaceApplication` authentication boundary, including its strict
session composition. It is not a WSGI application and has no routing method.

`assess(environ, workspace=..., conversation=..., operation=...,
expected_revision=...)` requires POST-shaped authenticated input. With sessions,
HTTPS/Host/Origin/fresh CSRF checks are inherited, without manufacturing a bearer.
The current principal supplies owner; the call requires current owner, active
conversation, current read/write permissions and saved dependency/fence checks,
then matches the exact history revision in the same SQLite transaction. It does
not accept prompt text, credentials, owner overrides, cwd, executable, environment,
provider or caller-supplied isolation assurances.

A successful assessment **always** returns `state: blocked`,
`reason: isolation_not_accepted`, `execution_enabled: false`,
`launch_permitted: false` and a hashed receipt. The receipt cannot authorize any
operation. The SQLite record and existing audit entry commit atomically; neither
contains prompt, title, event content, provider session ID or credential. Same
owner/workspace/operation retries are idempotent only for the same conversation,
revision and authority binding, and every retry reauthorizes. A different binding
conflicts; revocation/expiry blocks the retry before a prior receipt can be returned.
There are at most 100 receipts per owner/workspace, no eviction, retry queue,
background promotion or cleanup route. Operators/admins do not inherit owner data.

The audit action remains the existing `resource.update`, with an
`assistant-preflight:` hashed object key. It records a refused admission, never a
process start or successful model call. Expiry or audit/storage failure rolls the
whole assessment back. Malformed schema/records fail closed. The current principal
model's binding ABA limitation and SQLite transaction snapshot boundary remain as
documented by [saved history](../assistant_history/README.md); this package does
not invent a new principal epoch or instantaneous revocation of external work.

## Verification and next gate

[Dedicated CI](../../.github/workflows/assistant-protocol.yml) runs synthetic
in-memory protocol cases, real temporary SQLite owner/current-authority/idempotency
and concurrent-retry tests, real composed session/CSRF/expiry rollback checks, and
existing history/session/policy regressions. No test starts an ACP process or
contacts a real identity/model provider. No browser change is present.

Production execution requires a separately reviewed and independently tested
isolation launcher, resource limits, monotonic authorization generations, exact
resource lineage, explicit provider/network/secret policy, cancel/kill/cleanup
receipts and an authenticated admission/worker lifecycle. The protocol and audit
are preparatory components only, not substitutes for those gates.
