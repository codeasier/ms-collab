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

## Compare a recorded job across runs

In OBS, a reader opens the job-duration dashboard, compares successive successful
samples for a stage/job, and opens the run behind a slow point. Here, open the
same authorized pipeline scope and find the **Successful-job durations** group.
With at least two eligible samples the plot shows the actual recorded start on
the horizontal axis and duration in seconds on the vertical axis. Dots are
individual observations, not values between runs. Click a dot or use the
sample slider (arrow keys, Home, End), then **View selected run**. The sample
list remains available, including for a single sample; coincident dots can be
selected individually using the slider. The run list filter does not change
the report. Comparing or selecting samples sends no request; opening one uses
the existing authorized run detail and revision-checked task reads.

**Replacement acceptance (not yet verified):** A reader in the target workspace
uses a previously published scope with at least two comparable samples of one
stage/job also known in OBS. Compare their run IDs, recorded start times and
durations for that overlapping set. Select a slower non-latest sample and
open the precise run and its tasks without returning to OBS. If a successful
job came from a failed run, keep the two statuses distinct. Repeat after a
scope switch and check that a reader without access cannot see the samples.
If there are too few comparable records, the trend is not verified; do not
import extra fields or broaden access to fabricate evidence. Report only
sanitized results. This covers neither complete provider history nor identity,
repository or branch filters. OBS remains primary pending real-user acceptance.
