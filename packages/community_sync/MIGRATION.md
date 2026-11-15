# Migration record

Reviewed 2026-11-15 against the legacy OBS baseline
`f6c184e81671b460b9322c9f8bf0cadc70599d76` and `ms-collab` baseline
`156f1c7f9fcd992729479d43a7f0f969dc17bd0a`.

The legacy `community-board/src/gitcode.py`, `snapshot.py` and `projection.py`
provided the behavioral context: paged issue/PR reads, normalized observations,
and stale-update/observed-subset boundaries. This package is a clean adaptation,
not a bulk copy. It retains the MIT copyright notice and the existing public
`community_board` scope/model contract.

Explicit exclusions: credential discovery, installed CLI probing, deployment
paths, runtime files, operation notes, provider writes, webhook infrastructure,
real profiles/repositories, production tokens and old repository history. No
production request was made to validate this change.

The public API documentation was consulted on 2026-11-15:

- [Repository issue list](https://docs.gitcode.com/docs/apis/get-api-v-5-repos-owner-repo-issues/)
- [Repository pull request list](https://docs.gitcode.com/docs/apis/get-api-v-5-repos-owner-repo-pulls/)

These documents describe the list routes and pagination/update ordering. Their
site currently displays AtomGit branding. This slice intentionally retains the
legacy GitCode API origin; it does not silently follow redirects or switch
credential destinations. Bearer-header behavior follows the legacy adapter and
is **not independently verified against a live account**. Future provider-brand
or endpoint changes require an explicit reviewed adapter update.
