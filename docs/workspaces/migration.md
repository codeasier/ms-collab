# Compatibility and migration contract

This is a plan, not a migration script or authorization to access/move production
data. The public repository contains only synthetic fixtures. Existing services
and private operations history remain unchanged.

## Incremental sequence

1. Inventory each module's resource tables, identity keys, URLs, indexes, caches,
   jobs, API keys, connector bindings and assistant workdirs. Record ownership
   confidence privately; do not export the real inventory to this repository.
2. Implement stable workspace/principal/module contracts with synthetic A/B/shared
   fixtures and negative tests before assigning real data.
3. Generate an explicit mapping from legacy instance + module + resource ID to
   workspace + binding + stable resource ID. A legacy instance may contain several
   products; neither its name nor a single default repository determines ownership.
4. Classify unresolved, duplicate or mixed-owner data into a non-serving quarantine.
   Never assign it to the shared workspace or broaden permissions to make import
   succeed. A named first workspace is a deliberate mapping, not a global fallback.
5. Dry-run from a verified backup in an isolated environment. Reconcile counts,
   hashes, ownership, relationships, attachment references and access matrices.
   Differences require review; counts alone cannot detect swapped owners.
6. Introduce explicit compatibility lookups for old IDs/links. They resolve only
   after authentication and authorization; ambiguous mappings fail closed. No
   global redirects that reveal private space names or data existence.
7. Choose a bounded write freeze/cutover strategy per module, preserve a source
   watermark and make replay idempotent. Do not operate indefinite dual writers.
   Backfill and live changes must share the same ownership validation.
8. Pilot a second product workspace and shared projection before broad rollout.
   Provider integration must work through configuration/declared adapter contracts,
   not new core product-name special cases.
9. Switch production only after a separately approved plan, verified backup and
   rollback drill. Retain old entry points and audit evidence within access policy.

## Identity, credentials and compatibility

- Keep principal linkage tied to verified issuer+subject. Never infer memberships
  from commit author, display name, matching email or an organization label.
- Existing global admin roles and broad API keys do not become every-space admin
  or wildcard keys. Reissue/limit them through an explicit reviewed mapping.
- Connector secrets stay in approved private secret storage. The migration
  carries references only; copied app data must not contain decryptable secrets.
- Legacy resource IDs may be retained within a workspace, with a composite unique
  constraint. A source-to-new-ID map remains available for reconciliation, not
  public enumeration. URLs must verify membership before revealing mappings.
- Existing public storage links require separate assessment: adding workspace
  metadata cannot revoke an already published permanent URL. Do not claim private
  artifact control until bucket/download policy is verified.
- Runtime agent workspaces are not organizational workspaces. Preserve user/session
  boundaries while adding organization ownership; never copy old evidence between
  product spaces as a shortcut.

## Rollback and stop conditions

Back up state and establish rollback targets before writes. A schema downgrade
after new writes is unsafe unless a tested reverse mapping exists; otherwise use
read-only containment and restore/replay under the approved plan. Preserve
revocations and audit ordering during rollback; restoring old data must not
restore broader access. Do not delete the source or old credentials merely
because code import or a dry-run succeeded.

Stop on unknown ownership, identity mismatch, cross-space access, missed audit,
unaccounted data, failed restoration or concurrent unplanned changes. Keep the old
service authoritative until the relevant module's cutover is explicitly completed.

Acceptance follows [the matrix](acceptance.md); moving source code to the public
repository is neither a production migration nor proof of workspace isolation.
