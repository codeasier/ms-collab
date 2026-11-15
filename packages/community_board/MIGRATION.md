# Read-model migration boundary

## Reviewed source and rights

The prior community-board implementation at source revision
`f6c184e81671b460b9322c9f8bf0cadc70599d76` was reviewed for the
`snapshot.py` and `delta.py` read-model concepts: issue/pull separation, lifecycle
counts, an initial baseline and explicit state-change summaries. Its root license
is MIT, copyright (c) 2026 codeasier, matching this repository's [LICENSE](../../LICENSE).

This batch is a small reimplementation of that read boundary with explicit
workspace/connection/repository identities, not an import of the service or its
history. No third-party code, package dependency, runtime snapshot or old test
fixture was copied. The source file's network, repository config, locks, filesystem
and responsibility imports are deliberately not carried over.

## Public allowlist for this batch

- `packages/community_board/{__init__,__main__,model,delta,demo}.py`
- `packages/community_board/tests/{__init__,test_board}.py`
- This scope document and adjacent README navigation.

All fixture text, repository names, timestamps and item IDs were authored as
synthetic examples. The implementation does not accept arbitrary provider response
fields, contacts, log bodies or connection credentials. Public projection fields
are a schema constraint, **not** a content-redaction guarantee: future importers
must still check titles/labels for sensitive content before deliberate sharing.

## Intentional semantic changes

1. An absent item is no longer interpreted as closed. A bounded/filtered snapshot
   cannot prove closure, deletion, or repository-wide completeness.
2. Timestamps require an explicit offset, avoiding mixed naive/aware comparisons.
3. One snapshot belongs to one exact composite scope. Comparisons across scopes
   are errors; the public workspace has no privileged read-through behavior.
4. Values are copied into immutable records and new response dictionaries.
5. Provider URLs are omitted until host validation and safe UI-link policy exist.

## Remaining gates

- Application authentication, workspace membership and per-space capability
  checks, including two-principal cross-space negative HTTP tests.
- Credential-bound, workspace-scoped provider adapters with declared scan
  coverage and freshness, followed by sanitized projection persistence.
- Scoped webhook authentication, durable inbox, retries and recovery before
  enabling automatic refresh. An event must not become authoritative content.
- UI rendering, pagination and navigation interruption tests.
- Explicit authorization, preview and audit before publishing any private data
  into `commons`. This package performs no copy/share operation.
- Responsibility rules, review/merge/assignment mutations and full workflow
  automation remain outside this initial read-only batch.

Do not describe this package as completed workspace isolation, a live upstream
board, a production replacement, or feature parity with the original service.
