# Saved assistant history UI

A cookie-only page at `/assistant/`, mounted only by the configured
[unified product entry](../../packages/application_ui/README.md). It consumes the
existing [owner-scoped saved display API](../../packages/assistant_history/README.md).
It is **not an ACP client or running-agent interface**. No prompt, execution,
resume, cancellation, MCP, streaming, export, deletion or source ingestion is added.

- The shared session client provides current cookies, fresh CSRF and exact
  `If-Match` for reversible archive/restore. No bearer is synthesized or accepted
  by this page. The static module link is configuration, not an access grant.
- Explicit workspace submission lists at most 100 currently authorized records
  owned by the signed-in principal. There is no owner selector. Administrators
  and operators do not gain another person's history; shared spaces are not public.
- Opening a record reads current metadata, then 50 saved events at that exact
  revision. Every next page must match scope, ID, revision, total and contiguous
  sequence. A mismatch or 412 clears the whole view and requires explicit reload;
  pages from different revisions are never combined. No automatic polling/replay.
- Archive/restore requires current metadata capability and an explicit confirmation
  naming workspace, ID, revision and the limited effect. Events are retained.
  Every server request rechecks current owner/membership/module/dependency fences;
  the UI cannot grant access or repair a revoked fence.
- Scope edits, Close, session invalidation, recheck, logout and pagehide invalidate
  pending reads/writes. Late metadata cannot start another events request. Close
  clears detail while leaving the current list; logout clears every input and row.
  An already accepted lifecycle request may still commit; closing is not undo.
- Titles, event bodies, IDs and tool names use literal `textContent`, never HTML,
  Markdown, embedded media, executable commands or automatically followed links.
  User-visible agent reports are selected saved source content, not inferred or
  private model reasoning. Tool status is a report, not execution evidence.
- No content or credentials are persisted in browser storage/history. API URLs
  still contain logical workspace/conversation IDs; deployments must protect
  access logs. The source API's conservative fences, principal ABA limitation and
  authorized-read transaction boundary are unchanged.

`client.mjs` validates the projection and adapts the shared session client.
`history.mjs` owns cancelable view state, and `style.css` is page-local. There is
no standalone token wrapper or server launch command.

## Acceptance

[Dedicated CI](../../.github/workflows/assistant-session-ui.yml) runs synthetic
Python/Node tests and real local HTTPS Chromium against the composed session host,
including 105 events, owner isolation, readonly capabilities, CSRF/CAS, hostile
literal text, scope/Close/logout races, revocation and Back/Recheck. Four screenshots
cover desktop list/detail and mobile detail/confirmation. Fixtures are opt-in,
use temporary databases/certificates and a synthetic S256 provider, and are never
imported by production code. The readonly fixture is initially provisioned before
serving requests; it is not a fence-rebinding or permission-recovery workflow.
Real GitCode/ACP/provider identity, ingestion, isolated execution and production
integration remain separate, unimplemented prerequisites.
