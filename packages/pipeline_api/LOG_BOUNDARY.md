# Task metadata now; raw logs remain closed

This slice deliberately exposes **stage/task observations, not log text**. The
existing controlled GET-only provider already projects stage names and job
IDs/statuses/times into a bounded authorized snapshot. The new task page reads
only that projection. It does not make another provider request or expand token
access. Connection publication and data visibility must still be approved before
using real provider observations; printable names are not a secret scrubber.

## Evidence and privacy decision

The reviewed source consumer at revision
`f6c184e81671b460b9322c9f8bf0cadc70599d76`,
`backend/modules/pipelines/pipeline_service.py`, contains `read_log_page` and
`_log_page_dto`. It checks pipeline/run/job coordinates and pagination coverage,
but preserves arbitrary fragment text and metadata. Cursor validation/redaction
is delegated to a separate adapter. This is consumer source evidence only, not
an independent guarantee about that adapter, its live schema, or its data
classification. The MIT source attribution remains in [LICENSE](../../LICENSE).

Therefore bounded pages or a few token-pattern substitutions would not establish
safe log disclosure. Logs can contain secret values, personal identifiers,
private paths/hosts, commands, source code and sensitive output; arbitrary
provider cursors and search queries can also carry private content. No source
log, real cursor, configuration, credential or operational record was copied.

## What is available

- Current workspace/session policy on every read, exact connection/pipeline/run,
  and a required snapshot revision for task pagination.
- Stage position/name and recorded job-status counts, explicitly **not** an
  inferred stage result. An empty stage or all-success child jobs do not certify
  the stage or run succeeded. The recorded parent-run status remains separate.
- Status-filtered task pages, original stage/job positions and duplicate-ID
  occurrence counts across the whole run. Positions are references only inside
  that snapshot revision, not stable upstream IDs or execution authorization.
- Missing details, an empty observed topology, and a filter with no matching
  tasks remain distinguishable. Source history completeness/live compatibility
  stay unverified. No background refresh or fallback is implied.

## Before a future raw-log adapter may be enabled

A separately reviewed integration needs an explicit permitted recipient/data
policy, least-privilege credential binding to the exact provider scope, and an
upstream protocol/acceptance contract. It must define its redaction limits
honestly rather than claiming that general secret detection is complete. If safe
release cannot be established, withhold the text. Publication/retention must be
reviewed independently from permission to fetch it.

Any continuation must bind workspace, connection, pipeline, run, unambiguous job
identity, observed log version, query and redaction-policy version; changing any
must invalidate it. Define strict byte/page/time limits, control-character and
Unicode handling, partial-scan coverage, no raw fallback or sensitive error
bodies, and no token/cursor/query logging. Reauthorize before each dispatch and
response/publication, while acknowledging that local revocation cannot atomically
retract already authorized network bytes. Validate adverse cases using synthetic
fixtures before any explicitly authorized isolated live acceptance.

None of those future capabilities is enabled here. Raw logs, log search, tail
caches, execution, retry, cancellation and deployment remain unavailable.
