# Cookie-only research asset browser

- `client.mjs`: bounded scope/query builder and strict metadata/page/detail DTO
  validation. Validates the workspace list and published-catalog directory as
  well. Uses only the supplied shared session and fixed GET routes.
- `browser.mjs`: explicit lifecycle controller, no standalone identity or bearer
  mode. Workspace/catalog picker, exact-ID alternative, type/entity filters,
  revision-pinned pages and a plain-text detail dialog.
- `style.css`: responsive read-only catalog presentation.
- [Tests](tests/README.md): synthetic contract and hosted HTTPS/browser acceptance.
- [Service contract](../../packages/research_assets/README.md).
- [Product entry](../../packages/application_ui/README.md).

After session verification, the page reads the ordinary workspace list once and
shows available workspaces with IDs valid for this module; archived workspaces
are labelled and remain subject to their existing read policy. It does not select
the first workspace or look inside any workspace automatically. Choose a
workspace and press **Load published catalogs**; then choose a catalog and press
**Open catalog**. A directory with no publications is different from a published
catalog with zero assets: the latter is listed and can be opened. Directory
errors never become empty results or trigger automatic retries. **Open by exact
workspace and catalog IDs** remains available, including for an unpublished
scope; it uses the same scoped read path and remains usable while the workspace
list is pending. Successful exact selection retires a still-pending workspace
index request; recheck the session to use the picker again.

Directory and exact-catalog reads check current session, workspace membership and
`research_assets.read` inside their service transactions. Public space is not anonymous. The
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

Changing workspace or catalog selection or editing either exact ID clears old
rows, details and the active exact scope immediately. Changing workspace,
reloading discovery or editing exact IDs also retires the directory options.
Reloading the open catalog checks only its exact scope and discards cached
directory choices. Independent directory, exact-read and detail generations
reject late replies (including stale 401s) even when abort is ineffective.
A cancelled metadata stage cannot start the next stage. 401, logout, pagehide and
session recheck clear state. Already authorized bytes cannot be retracted, but
subsequent reads always reauthorize. Nothing is browser-persisted.

`available:false` is shown as never published, not an empty successful source.
Published empty and filtered empty remain separate presentations. Published
counts describe only the supplied normalized catalog, not verified full coverage
of a real repository or provider.
