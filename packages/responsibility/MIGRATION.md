# Responsibility migration boundary

## Source evidence, checked 2026-11-15

The prior community-board source was read at fixed revision
`f6c184e81671b460b9322c9f8bf0cadc70599d76`. Its root MIT license is copyright
(c) 2026 codeasier. This batch retains the license attribution and reimplements
an allowlisted subset without importing the old repository/history, operational
configuration, deployment information, live directory or runtime snapshots.

| Reviewed source | Retained/reimplemented here | Explicitly excluded |
| --- | --- | --- |
| `community-board/src/responsibility.py` | Configuration validation, ordered ownership, cross-field backup, literal path boundaries, read-only candidate statuses | Hardcoded single repository, global file paths, raw provider ID identity assumptions, old database/bootstrap |
| `community-board/src/changed_paths.py` | Strict status/patch normalization, rename old/new paths, bounded page termination, final diff identity/count checks, no partial paths on failure | Live API/token calls, implicit enrichment on refresh, old cached-evidence reuse and hardcoded repository gate |
| `docs/community-responsibility-foundation.md` | Distinction between configuration, observed candidates and actual review/workflow; incomplete and unknown boundaries | Internal operations, old UI/deployment evidence and raw documentation export |
| Source license | MIT attribution | No additional third-party dependency/code imported |

The prior standalone `responsibility_policy.py` local review/SLO simulator is
**not migrated or wired** in this batch. It was not a real production workflow;
configuration/candidate migration must not be described as completing review,
LGTM, approval, assignment, command delivery, merge, SLO or escalation.

## Deliberate changes

- Every record is identified by `(workspace, connection, repository)`. There is
  no fixed product or repository, cross-workspace aggregate or privileged public
  workspace. The separate responsibility module can be disabled per space.
- Owners are existing local verified principals, not unverified provider IDs.
  No external Issue assignee identity mapper is implemented or guessed.
- Configuration and observations reside in dedicated tables in the **same**
  policy database, never generic resources or an unlocked second-database join.
- Source titles must already have exact-content community approval. Path data
  additionally requires deliberately prepared publication for explicitly granted
  responsibility readers. New paths are never implicitly imported or shared.
- Prepared publication requires a real configured credential and independent
  publisher grant, rechecked in the transaction. There is no HTTP evidence
  upload route or default provider. Community revision changes invalidate path
  evidence wholesale until explicitly republished, even if titles are unchanged.
- Configuration writes are 64 KiB bounded, require a current admin plus explicit
  editor/read grants and responsibility write capability, and use CAS. Deleting
  existing fields is not supported in this increment. Unknown/stale writes are
  not automatically retried.

## Public batch allowlist

`packages/responsibility/{__init__,core,paths}.py`, corresponding synthetic tests,
`packages/responsibility_api/{__init__,application,records}.py`, its synthetic
SQLite/HTTP tests, adjacent README/provenance files, one dedicated CI workflow
and a documentation-index entry. No old fixture or operational file is copied.

This is a working authenticated service/core slice, **not full feature parity**.
A user-facing configuration/candidate page, real scoped provider acquisition,
explicit path-content review UX, authoritative identity mapping and production
acceptance are still required. No new UI is claimed in this PR. Existing browser
workflows run as regression gates, not evidence of a responsibility UI.

A subsequent [authorized UI slice](../responsibility_ui/README.md) adds structured
editing and observed-candidate views. It does not turn this foundation into live
path acquisition, authoritative Issue assignment or a review/merge workflow.
