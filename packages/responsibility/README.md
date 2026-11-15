# Responsibility configuration and candidates

A small, standard-library read model migrated from the prior responsibility
foundation. It has **no authentication, network, database, commands, scheduling or
production bootstrap**. Use the [authorized service](../responsibility_api/README.md)
for HTTP integration; calling a pure function is never authorization.

## Configuration

`fields([...])` returns fresh allowlisted records with stable field ID, name,
`developer|committer` kind, literal repository paths, ordered owner principal IDs
and an optional cross-field backup. Developer owner order retains primary,
secondary and tertiary positions (maximum three); committer fields allow up to
100 owners. There are up to 200 fields and 100 paths per field. Backups must be an
owner of another developer field, not an owner of the source field. A backup does
not automatically become a candidate, reviewer, admin or replacement owner.

IDs use the local `ms-collab.principal.id` namespace, not display names, email,
OAuth IDs, REST assignee IDs or organization roles. String equality across
namespaces is not a mapping. The service additionally verifies nominated owners
are current active workspace members; local principal IDs must not be reused.
Field ownership or kind never grants API, reviewer or GitCode permissions.

Paths match exact text or a directory-boundary prefix: `ui` matches `ui/a`, not
`ui-extra/a`. Globs are not interpreted; `wild*` is a literal name. Traversal,
absolute paths, empty components, backslashes and controls are rejected. No path
is opened on disk. Overlapping fields are all shown. A rename includes both old
and new names. Unmanaged paths remain explicit even with partial field matches.

## Bounded path observations

`normalize_paths(expected, pages, rechecked)` normalizes **already acquired**
files-page observations. It neither fetches the provider nor proves when inputs
were observed. `expected` contains explicit hexadecimal head/base SHAs and target
branch. Ordered pages contain at most 100 records each, at most 20 pages; a short
terminal page is required. Duplicate file names, missing rename origin, unknown
status, bad/conflicting patch flags, malformed or failed pages, a changed final
identity, and inconsistent detail count aliases all fail closed with **no partial
paths**. Both closed `status` and documented old/new patch-path shapes are handled.
The file count counts unique files, not rename-expanded paths. Only allowlisted
normalized paths, diff identity and a fixed reason are retained; no patch text.

A successful result always declares `head_base_rechecked_unpinned`. It is a
bounded read observation, **not a commit-fixed/atomic snapshot, ABA-safe history,
PR-diff equivalence proof or approval gate**. `PathEvidence` is an immutable
normalized value, not a signature, authority or authenticity claim. A trusted
adapter can construct values; no untrusted HTTP route accepts evidence claims.

## Candidate projection

`project(config, approved_items, evidence, current_actor, ...)` supports
`mine`, `all`, `field` and `unassigned` plus bounded stable pagination. Its caller
must supply exactly one already-authorized scope and approved source items.
Only open items are candidates. Missing or incomplete PR evidence produces
`incomplete`, never guessed paths. Issue paths and external-assignee-to-local
identity mapping remain unknown: issues appear in all/unassigned, not mine by
matching a REST ID. The service never accepts an actor query parameter.

Mine includes developer-field owners only, not backups or committer ownership.
A selected committer field can be inspected through the field view. Multiple
matching fields are observations, not assigned tasks. Unassigned remains the
source's **Issue-only** queue; unmapped PRs are visible in all with their explicit
status. Counts cover only supplied, approved observations, not the entire repo.

Every projection keeps `workflow_enabled=false`, `review_status=unverified`,
`approval_available=false`, `transport_executable=false` and
`external_effect=not_observed`. No LGTM, merge, assignment, escalation, SLO,
reviewer eligibility or upstream side-effect claim is made.

## Verification

```sh
python3 -m unittest discover -s packages/responsibility/tests -v
```

All test content is synthetic. [Migration/provenance](MIGRATION.md) ·
[Tests](tests/README.md) · [Authorized API](../responsibility_api/README.md)
