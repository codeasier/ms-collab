# Pipeline analysis migration boundary

## Source and rights

Reviewed source revision `f6c184e81671b460b9322c9f8bf0cadc70599d76`:
`backend/modules/pipelines/pipeline_durations.py`, its terminal-status helper in
`pipeline_run_dao.py`, and `backend/ut/test_pipeline_durations.py`.
The source root and this repository use MIT, copyright (c) 2026 codeasier.
See [LICENSE](../../LICENSE).

The grouping/ambiguity and successful-job duration logic is adapted, with new
scope validation and a storage-free input boundary. No source service, database,
provider client, original test fixture or Git history is copied. There are no
new third-party runtime dependencies. Tests and CLI examples are newly authored,
fictional data, not redacted production logs.

## Preserved behavior

- A terminal failed/cancelled run may contribute its successful jobs.
- Invalid timing does not become zero; successful duplicate names do not become
  a spurious summed sample. An invalid successful sibling still makes a name
  ambiguous. Identically named jobs in different stages stay separate.
- Return chronological samples, average/min/max/latest duration and explicit
  coverage, never infer full history from a limited local snapshot window.

## Intentional changes

- Scope is now the exact workspace/connection/pipeline triple; run duplicates or
  scope mismatches fail the request, even outside the selected limit.
- `completed` without an explicit conclusion is not treated as successful.
  The old adapter's `COMPLETED` convention must be translated at a future
  provider boundary, not reused as a universal CI state.
- Timezone and epoch-unit guesses are removed. Numeric timestamps must be
  translated by an adapter with an explicit unit contract.
- Jobs finishing after the observation time are excluded; their timing cannot
  be evidence already known at that snapshot.
- Duplicate job IDs are excluded, inputs are bounded, raw snapshot fields are
  not returned, and JSON parsing/DAO imports are removed.

## Public allowlist

- `packages/pipelines/{__init__,__main__,core}.py`
- `packages/pipelines/tests/test_core.py` and adjacent README files
- This migration document, documentation navigation and isolated pipeline CI

## Deliberately excluded

The old pipeline service/controller/DAO, watchers, credentialed clients, log
prefetch/scanning, source bindings, artifact URL generation, triggers/retries,
production filters, real logs, fixtures, deployment files and CI configuration.
No existing self-hosted runner or upstream connection is used. The separate
GitHub-hosted CI only executes tests and the synthetic CLI.

This increment is not a live pipeline dashboard, persistent execution history,
job scheduler or a production CI/CD integration. Provider and permission wiring,
retry/cache semantics, API and UI acceptance need later independently reviewed
increments before this core can be presented as a migrated end-user feature.
