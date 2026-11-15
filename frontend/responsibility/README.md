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

The business controller now exports an explicit mount function. `entry.mjs` is
the standalone bearer bootstrap; the [unified session entry](../application/README.md)
injects cookie-only requests without importing that legacy entry.

## Historical identity and membership references

Configuration responses include strict `references` status. Stale owner/backup
IDs are labeled **historical, unconfirmed**, not looked up as current people.
All candidate views and counts remain unavailable while any nominated pin is
stale. Current authorized editors may still remove/replace stale IDs in the full
configuration. Ordinary Save never rebinds a previously nominated ID, even after
removal and re-addition. Ineligible IDs must be replaced before reconfirmation.

The separate **Review identity reconfirmation** button is shown only for stale
references with an eligible current editor proposal. Its second-stage dialogue
lists every current owner/backup ID and explains that confirmation binds those
IDs to CURRENT local identities and memberships, not original people or rights.
A single explicit confirmation sends `POST /reconfirm` with the exact full
fields, proposal token and displayed `If-Match` revision. Cancel/Escape, scope
changes, sign-out, pagehide or a failed/uncertain response clear pending intent;
no Save, reload, reconnect or conflict automatically confirms or retries.

Both the standalone bearer entry and shared HTTPS session entry mount this same
controller. The session adapter uses fresh CSRF and cookies only, never a fabricated
bearer credential. Tokens and identity-provider subjects are never rendered.
