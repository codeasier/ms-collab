# Cookie-only research asset browser

- `client.mjs`: bounded scope/query builder and strict metadata/page/detail DTO
  validation. Uses only the supplied shared session client and scoped GETs.
- `browser.mjs`: explicit lifecycle controller, no standalone identity or bearer
  mode. Type/entity filters, pinned-revision pages and plain-text detail dialog.
- `style.css`: responsive read-only catalog presentation.
- [Tests](tests/README.md): synthetic contract and hosted HTTPS/browser acceptance.
- [Service contract](../../packages/research_assets/README.md).
- [Product entry](../../packages/application_ui/README.md).

Every read still checks current session, membership and `research_assets.read`
inside the original service transaction. Public space is not anonymous. The
shell's module list is configuration, not permission. No upstream acquisition,
credential input, executable code, rendered Markdown/HTML, export or binary
artifact download exists here. Source status/confidence are declarations, never
independent execution/diagnosis evidence.

A metadata load reads types, entities and the first page at one revision, then
server-side filters/pagination/detail calls retain that revision. Each response
must match exact scope, revision, availability and explicit unverified flags.
Counts, entity labels, selected summary/content fingerprints and section shapes
are checked; changing a filter clears old rows, while page changes preserve the
same query total. A 412 requires explicit reload, not silent retry or mixed data.

Changing either scope ID clears all private rows/details immediately. Request
and detail epochs reject late replies even with ineffective AbortSignal; a
cancelled metadata stage cannot start the next stage. 401, logout, pagehide and
session recheck clear state. Already authorized bytes cannot be retracted, but
subsequent reads always reauthorize. Nothing is browser-persisted.

`available:false` is shown as never published, not an empty successful source.
Published empty and filtered empty remain separate presentations. Published
counts describe only the supplied normalized catalog, not verified full coverage
of a real repository or provider.
