# Responsibility browser client

Structured configuration editing and approved observed candidates, served by the
[configured UI composition](../../packages/responsibility_ui/README.md). There is
no standalone authentication, API host selector, local storage or provider call.

Connect with an opaque credential, enter the exact workspace/connection/repository,
and inspect the current configuration and candidates. Editor controls require
current explicit `capabilities.edit`; PUT still authenticates and authorizes on
every call. Existing field IDs cannot be removed or renamed. Review and confirm
the complete configuration once at the displayed record revision. On a 412 or
uncertain network result, local edits are cleared; reload and inspect the current
state before deciding on a new edit. No automatic retry or idempotency claim.

The four candidate views use the service's actual filters and bounded pagination.
An Issue in the legacy `unassigned` queue is **assignment unknown**, not proof of
no assignee. Complete paths are unpinned observations, not review, approval or
merge evidence. No path publication, upstream acquisition or action is exposed.

All untrusted text uses DOM text nodes. Requests are relative same-origin with
cookies omitted, no-store and redirects rejected. Credentials stay in a closure,
not URLs, storage or rendered values. Scope/identity changes, 401, disconnect and
pagehide clear drafts, rows, dialog content and ownership of pending operations.
Generation checks occur between requests as well as before rendering: ineffective
AbortSignal cancellation cannot trigger old-scope follow-up reads or resurrect old
state. A disconnected write may still have reached the server; its late response
cannot affect a new identity/scope. Back/Forward requires reconnecting.

Desktop/mobile layouts, real-HTTP synthetic browser acceptance, client tests and
remaining deployment limits are documented in [tests](tests/README.md).
