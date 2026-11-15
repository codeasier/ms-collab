# Authorized pipeline observations UI

A separate static wrapper for an explicitly configured `PipelineApplication`.
It reads current, authorized stored observations: runs, stage/job details, and
successful-job durations. It does **not** expose pipeline execution, cancellation,
retry, logs, artifact download, provider refresh, login or production deployment.
The original synthetic demo is unchanged.

```python
from packages.pipeline_ui import PipelineUI
application = PipelineUI(api=configured_pipeline_api, origin="https://collab.example.invalid")
```

The trusted factory supplies the API, real identity verifier and initialized
policy store. Serve `/pipelines/` and `/api/` at this exact origin with TLS and
trusted proxy/WSGI Host normalization, bounded requests and rate/concurrency
limits. Numeric IPv4 loopback HTTP is allowed only for isolated acceptance.
No listener, default verifier or credential discovery is bundled.

## Current authority and consistency

- Credentials are opaque bearer input for the configured identity verifier, not
  upstream service tokens. They stay in page memory only and are cleared from
  the input on Connect. There are no cookies, storage tokens, token URLs or
  external links/images. Disconnect is not issuer-side revocation.
- Each read names an exact workspace/connection/pipeline. The existing API
  checks current identity, membership and module access in its SQLite snapshot.
  A shared workspace is not a union of private data. The UI does not discover all
  connections or imply that a workspace name grants access.
- List and duration responses must share one scoped snapshot revision. The UI
  loads at most 100 stored runs, then filters/pages them locally in groups of 10.
  A detail read must match the same scope, revision and selected run ID.
  Malformed, missing, unauthorized or mixed-revision responses clear the panel
  rather than combining stale data. Nothing is automatically retried.
- Scope changes, reloads, detail Close/Escape, Disconnect and page lifecycle exit
  abort obsolete requests. Epoch checks also ignore late results after abort.
  Real Back/Forward cannot restore the previous credential or private rows.
  Revocation cannot retract already authorized bytes; subsequent API calls must
  pass current authorization again.

## Honest observation semantics

**Reload stored snapshot** performs GETs only; it does not call the upstream
provider. The separate [acquisition service](../pipeline_sync/README.md) can
publish observations through trusted, currently authorized host integration.
That ingestion entry point is not exposed through this UI.

The screen preserves `full_history_complete = false` and
`live_provider_verified = false`. An empty subset does not prove no provider
history. The duration report covers all stored terminal runs, independently of
run-list filters. A failed run may contain a valid successful-job sample; that
sample never makes the run successful. Missing, reversed, future or ambiguous
job timings follow the core's exclusion rules, not zero-fill. Latest means
latest sample start. Coverage counters may overlap. This is not a deployment,
merge, release, root-cause or health gate.

Run IDs, stage/job names and all provider-origin text are rendered with
`textContent`, never HTML. The static file allowlist, same-origin checks,
no-store/nosniff/no-referrer and strict CSP contain this narrow wrapper. These
are not a full production security review or secret scrubber. Real provider
compatibility, safe data publication, secure credentials and deployment remain
separate prerequisites.

## Synthetic verification

```sh
python3 -m unittest discover -s packages/pipeline_ui/tests -v
node --test frontend/pipelines/tests/*.test.mjs
```

The hosted browser fixture executes the complete synthetic chain:
`TestProvider` → `PipelineSynchronizer` (current authority + atomic publication)
→ `PipelineApplication` → same-origin browser UI. It uses only fictional tokens,
metadata and temporary SQLite stores, with external browser requests blocked.
The verifier/provider doubles exist only under tests and are not production
factories. The CI uploads exactly three synthetic screenshots (desktop, mobile,
and detail), not logs, browser profiles or traces.

- [Browser acceptance and tests](../../frontend/pipelines/tests/README.md)
- [Pipeline API](../pipeline_api/README.md)
- [Documentation index](../../docs/README.md)
