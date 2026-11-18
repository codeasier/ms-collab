# Pipeline observation frontend

Same-origin, dependency-free ES modules for configured pipeline snapshot reads.
No default credential, upstream writes, logs, provider polling or production
startup. [Service integration](../../packages/pipeline_ui/README.md) ·
[Synthetic acceptance](tests/README.md)

- `entry.mjs`: standalone bearer client creation only, not served by the cookie host.
- `runs.mjs`: explicit-client controller shared by standalone and cookie pages.
- [Cookie-only entry](../../packages/application_ui/README.md): existing session
  authority, scoped GET only, no credential conversion or extra provider rights.

## Stage/task pages

The run dialog reads a revision-pinned task projection after validating the
selected run. It shows per-stage observed job-status counts, not an invented
stage result, and pages tasks ten at a time with status filtering. Every task
page must exactly agree with the already validated bounded run, including full
counts, original positions, duplicate IDs, selected filter and revision. This is
not a scalable remote log/history reader: the original bounded run detail still
provides the consistency baseline.

**Recorded elapsed** is the difference between that task's recorded start and
finish, not a timer. A failed task can show a duration and still be failed. For
a terminal task (including a skipped task), a number means only that the two
recorded times form a valid interval ending no later than the run observation.
Equal instants show `0s`; nonzero fractions are retained to microsecond precision.
Missing, reversed or later times and nonterminal or unknown statuses show
`unavailable`. Neither a stage total nor a successful-job trend is inferred.

Close, new run/scope, session clearing and page lifecycle cancel both detail and
task chains. A late task response cannot populate a newer dialog, even if the
transport ignores AbortSignal. A version conflict clears the view and requires
explicit scope reload. Text remains literal; duplicate IDs carry an ambiguity
notice rather than becoming remote task targets. No raw-log/search/download,
trigger/retry/cancel or provider refresh controls are provided. See the
[privacy decision](../../packages/pipeline_api/LOG_BOUNDARY.md).
