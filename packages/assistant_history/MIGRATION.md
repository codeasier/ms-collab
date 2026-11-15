# Assistant migration assessment

## Source and selection

Reviewed the existing OBS assistant at source commit
`f6c184e81671b460b9322c9f8bf0cadc70599d76` before choosing this increment:

- `docs/assistant-native-acp.md`: general ACP conversations, typed display events,
  owner-bound native sessions and advertised provider capability negotiation.
- `docs/assistant-history.md`: current-authority history, owner isolation, bounded
  pages and no execution/replay from a history read.
- `docs/assistant-archive.md`: reversible local archive metadata, lifecycle CAS
  and no claim that archive destroys/terminates provider state.
- `backend/modules/assistant/assistant_native_service.py`,
  `assistant_native_dao.py`, `assistant_native_projection.py`: current authority
  rechecks, authorization epochs, typed projection and stored history boundaries.

This is a clean, small adaptation of the safe invariants into ms-collab's current
trusted identity and transactional workspace model. It does not copy deployment
configuration, private directories, credentials, raw source history or runtime
code. Existing source and this implementation carry the codeasier MIT copyright.

## What is preserved versus deliberately reduced

| Source concept | This increment |
| --- | --- |
| Owner-bound conversation history | Dedicated owner/workspace rows; even administrators cannot read another owner's content. |
| Current authorization and auth epoch | Current policy checks plus fixed scope and persistent workspace/member/module revision fences; principal binding/active state have current-value checks only, not a monotonic epoch. |
| Typed user-visible updates | Small explicit display schema and audience selection, with no raw ACP object ingestion. |
| Stable history pagination | Consecutive event sequence with fixed conversation revision; fail on mixed snapshots. |
| Reversible archive with lifecycle CAS | Metadata-only state PATCH with If-Match and atomic audit; archived append is rejected. |
| Native provider sessions | Not migrated; local IDs do not locate or resume any provider session. |
| Busy native job guards | No runtime/jobs exist; transaction + expected revision fences local ingress races only. |
| Scoped tools and approval lifecycle | Not migrated; real resource dependency lineage/redaction/approval remains required. |
| ACP process and provider networking | Not migrated or enabled. A private HOME is not an OS sandbox, and a trusted legacy runtime is not a public multi-user isolation boundary. |

The current core principal schema has no generation: restoring the exact original
issuer/subject or reactivating a principal can restore reads if all persistent
fences still match. Detecting intermediate identity changes is explicitly deferred
until a monotonic principal generation exists; the source auth epoch is not claimed
as fully migrated.

History reading is not permission to prompt, execute, reconnect or replay. No
source default provider, command, environment or network configuration is carried
forward. Later execution work needs a separately reviewed isolation and resource
authorization design; this PR is not that design or an implicit authorization.

A later [protocol/preflight increment](../assistant_protocol/README.md) adds
process-free ACP parsing and audited execution refusal only. It neither changes
this history API's capabilities nor enables native session execution. The separate
[runner design](../assistant_protocol/DESIGN.md) records the verified isolation
blocker and remaining target-OS/authorization prerequisites.
