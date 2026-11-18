# Pipeline snapshot analytics

A standard-library-only migration of the successful-job duration trend algorithm.
No credentials, provider access, log reading, database, process execution, HTTP or
UI integration. Python 3.12+.

```sh
python3 -m packages.pipelines
python3 -m unittest discover -s packages/pipelines/tests -v
```

The CLI prints one fictional failed run with one successful job. A failed or
cancelled **run** can contain useful successful-job timing evidence; this does not
make the whole run successful.

## Input contract

```python
from packages.pipelines import PipelineScope, summarize_durations

scope = PipelineScope("product-alpha", "example-ci", "build")
# First authenticate, authorize membership and the pipeline capability, then
# query the current, authorized snapshot set for this exact scope.
report = summarize_durations(scope, snapshots=[], limit=30)
```

Each snapshot is a dictionary containing:

- `workspace_id`, `connection_id`, `pipeline_id`: the exact requested scope.
- `id`: a unique current run ID. IDs use 1–128 ASCII letters, digits, dots, dashes
  or underscores, starting with a letter or digit.
- `observed_at`: an ISO timestamp with explicit timezone; no assumed local time.
- `status` and optional `conclusion`: normalized with `normalize_status`.
- `stages`: a list of `{name, jobs}`. Each job has `id`, `name`, `status`, optional
  `conclusion`, `started_at` and `finished_at` (explicit-timezone ISO strings).

Allowed status aliases are deliberately small: success/succeeded/passed,
failure/failed/error, canceled/cancelled/aborted, timeout/timedout/timed_out,
skipped, queued/pending and running/in_progress. **`completed` alone is unknown**;
it requires an explicit terminal conclusion. Conflicts, unrecognized values and
non-string statuses remain unknown. This is not a provider SDK: a provider whose
`completed` means success must explicitly supply that translation. Numeric status
codes, implicit epoch units and timezone-free dates are not guessed.

## Analysis semantics

- Scope mismatches, repeated run IDs, invalid observation times or bounds reject
  the whole call with a value-free `InvalidSnapshot` reason. There is no fallback
  to all spaces, and `commons` is not a private-space aggregate.
- A caller supplies at most 5,000 snapshots with at most 10,000 total job entries
  and 200 stages per run. `limit` is 1–500; the newest observed terminal runs are
  selected first, using run ID as a deterministic tie-break. Missing details do
  not cause older runs to be substituted into that window.
- Only explicitly successful jobs contribute durations. Missing/reversed times
  are excluded, not zero-filled. Completion after the snapshot observation is also
  excluded (no implicit clock-skew tolerance). A genuinely measured zero duration
  is retained.
- Trends are grouped by `(stage_name, job_name)` across runs, not unstable job IDs.
  Same-named successful siblings (including one with invalid timing) are excluded
  as ambiguous. Repeated job IDs in one run are also excluded. Distinct stage
  names remain separate; failed siblings do not add success-duration ambiguity.
- Samples are chronological by job start time and run ID. `latest_seconds` means
  latest **sample start**, not newest observation or fastest successful run.
- `average_seconds` is the arithmetic mean of the reported sample durations,
  rounded once to a float. It stays between `min_seconds` and `max_seconds`, inclusive.
- Coverage reports selected/supplied counts, missing details, malformed entries,
  excluded jobs and ambiguity. Counts are diagnostics and can overlap.
  `full_history_complete` is always false: a finite supplied dataset cannot prove
  complete upstream history. A lack of groups does not prove no failures or jobs.
- Returned fields are allowlisted; no raw provider response, logs, URLs, actor
  profiles, source paths or arbitrary metadata is propagated. Labels remain
  untrusted text: consumers must escape them. This is **not a secret scrubber**.

## Trust and remaining work

`PipelineScope` is a namespace, **not authorization**. A caller able to supply
another space's records can analyze them. The future application must check
current identity, membership, module capability and provider connection access
before loading records and again as required at use time. This package does not
solve identity revocation races, credential ownership or sharing.

Providers must store only the current authorized snapshot per run. Retry
invalidation, durable run history and explicit adapter status translation remain
separate integration work; this package neither reads nor repairs historical
caches. It must not be used as a deployment/merge gate or a root-cause detector.

- [Migration provenance and exclusions](MIGRATION.md)
- [Regression tests](tests/README.md)
- [Project migration plan](../../docs/migration-plan.md)
