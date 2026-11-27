# Configured installation and bounded service assembly

This is a **single-host POSIX installation tool and fixed TLS WSGI assembly** over
[ApplicationHost](../application_host/README.md) and
[ApplicationUI](../application_ui/README.md), not a completed production rollout.
It replaces neither the independent demo nor operator deployment acceptance.
The only accepted identity mode is `disabled`. Real GitCode login remains blocked
by the [provider evidence gate](../../docs/gitcode-login-acceptance.md).
There is no environment/token/PAT/test-user/plugin path to bypass that gate.

## What this increment delivers

- Strict, bounded, duplicate-key rejecting JSON configuration. Unknown fields,
  dynamic import/provider switches, invalid module dependencies or insecure origin
  fail before installation. Explicit module hosting is not a user grant.
- Explicit **new directory only** initialization of the currently reviewed local
  policy/module schemas and a separately bound login store. No users, operators,
  workspaces, memberships, connections, snapshots or login attempts are invented.
- Read-only installation self-check: private ownership/modes, no symlink/hardlink
  aliasing, same-device local SQLite files, DELETE journals, exact schema/layout,
  integrity, foreign keys, policy model, origin/client binding and clock floor.
- A shared process-lifetime maintenance lease. Cold backup requires an exclusive
  lease plus SQLite writer reservations across both attached stores. A new or
  restored service refuses missing/different schema; there is no HTTP migration.
- Fixed, pinned Gunicorn HTTP/1.1 TLS on **127.0.0.1 only**. No general Gunicorn
  option/config loader, imported app string, proxy header authority, socket
  activation, access log or upstream worker/plugin hook. Raw duplicate headers,
  transfer-encoding and ambiguous/oversized framing are rejected before WSGI.
- Private cold backup and verified restore to a **different new directory**.
  Pending authentication verifiers, sessions and CSRF hashes are never backed up;
  restoration creates a fresh empty login DB and retains the observed clock floor.

[Runtime and server tests](tests/README.md) use only synthetic state and temporary
certificates. Passing them does not verify a real proxy, host filesystem, identity
provider, operator enrollment or data migration.

## Install without enabling real login

Requires a reviewed checkout, POSIX Python 3.12+ with SQLite STRICT/serialize and
local filesystem support. The command examples use fictional paths, not host
information. Create an operator-owned private configuration/runtime parent, copy
[the JSON example](../../deploy/service.example.json), and set the file to 0600
and its parent to 0700. `data_directory` must be absolute and **not yet exist**;
its immediate parent must be owned by the service UID and private. No existing
legacy store can be implicitly adopted.

Install only the pinned server wheel into a dedicated environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary=:all: -r deploy/requirements.txt
.venv/bin/python -m packages.service_runtime --config /srv/ms-collab/runtime/service.json validate-config
.venv/bin/python -m packages.service_runtime --config /srv/ms-collab/runtime/service.json init
.venv/bin/python -m packages.service_runtime --config /srv/ms-collab/runtime/service.json check
```

Initialization does not create an administrator. An empty store is intentionally
not a usable collaboration account system until separately reviewed provider
verification and principal enrollment exist. Existing configured service objects
elsewhere do not become imported authority. The first browser entry is public
static content; login start returns 503 and unauthenticated data reads return 401.

A successful `check` means the **installation** is consistent. It deliberately
reports `production_ready: false` and `login_enabled: false`. Adding
`check --require-production` returns exit **3** even for a valid installation,
so automation cannot mistake a layout check for real production acceptance.
Errors return exit 2 with a fixed input-free code. No CLI prints configuration,
SQL, credentials or a traceback. Partial initialization is retained for private
operator diagnosis; retry does not overwrite or delete it.

The installed `installation.json` must have exactly `format`, `binding`,
`policy_schema` and `login_schema`, with integer `format: 1` and the expected
binding and schema values. Boolean `true` and floating-point `1.0` are not
version 1. Valid JSON whitespace and key ordering remain accepted; checking,
startup and backup do not rewrite the manifest into canonical form. Initialization
and restore still produce the same canonical manifest. An invalid installed
manifest is rejected before a backup destination is allocated, although the
source lease and SQLite transactions have already been opened. This validation
does not add schema migration, receipt support or identity/provider enablement.

## Fixed TLS process, not a public deployment command

Supply your separately provisioned certificate and unencrypted private key from
outside the checkout. The key must be private and owned by the process UID. This
tool never generates real certificates, prompts for passwords, contacts a CA or
changes system trust. The local example is:

```sh
.venv/bin/python -m packages.service_runtime --config /srv/ms-collab/runtime/service.json serve \
  --certfile /srv/ms-collab/tls/fullchain.pem --keyfile /srv/ms-collab/tls/private.key --port 8443
```

The server remains loopback-only, two fixed synchronous workers with bounded
headers, body admission and worker timeouts. The configured `origin` is the exact
browser-visible HTTPS origin; it is not inferred from the bind, forwarded headers
or request claims. TLS does not by itself authorize a request. Every API still
uses the existing cookie/session, CSRF, current membership, revision and content
publication checks. Cross-site/host and mixed bearer requests fail closed.

Gunicorn recommends a buffering proxy to resist slow-client denial of service.
A production operator must separately review and test a TLS-to-TLS buffering
proxy, exact Host preservation, duplicate-header rejection, body/rate/concurrency
limits, timeouts, supervision, network exposure and logging. Do not emit
`Forwarded`/`X-Forwarded-*` or use PROXY protocol; this runtime rejects them and
never trusts them to turn HTTP into HTTPS. Do not enable URL/query/body/header
access logs at a proxy, supervisor or error collector: OAuth query data, cookies
and arbitrary application values are sensitive. The fixed server emits only
input-free operational codes; arrange separate privacy-reviewed monitoring.

This composition does not enable downloads, acquisition, real providers, shell
execution, ACP execution, connection discovery or enrollment. GUI connections
and future workspace connection-management configuration stay at their existing
empty/disabled defaults; arbitrary JSON cannot introduce them. Authorized stored
module content remains governed by the module's existing grants.

## Cold backup and verified restore

Stop every service and external ingestion writer using this installation first.
The runtime's cooperative lease prevents its own live workers from overlapping
maintenance. SQLite locks also exclude other current SQLite writers, but no
advisory lock makes malicious same-UID writers or an unsafe filesystem trusted.
Network filesystems, distributed hosts, filesystem replacement by another local
actor, media/power-loss guarantees and backup encryption are not established.

```sh
.venv/bin/python -m packages.service_runtime --config /srv/ms-collab/runtime/service.json backup \
  --directory /srv/ms-collab/backups/new-checkpoint
.venv/bin/python -m packages.service_runtime --config /srv/ms-collab/runtime/service.json verify-backup \
  --directory /srv/ms-collab/backups/new-checkpoint
```

The destination must not exist, and its parent must be private and service-owned.
A backup contains only `policy.sqlite` and `backup.json`, both 0600 in a 0700
directory. It includes **private business/identity data**, even without active
login material. Never upload, commit, screenshot or use it as a public fixture.
Provide private encrypted storage and retention under a separate operator policy.
Hashes detect accidental damage, not an attacker rewriting both file and manifest.
No cryptographic signature, encryption or secure deletion claim is made.

For a restore drill, make a private copy of configuration with a **new absent**
`data_directory` and unchanged origin/issuer/client binding, then run:

```sh
.venv/bin/python -m packages.service_runtime --config /srv/ms-collab/runtime/restore.json restore \
  --directory /srv/ms-collab/backups/new-checkpoint
.venv/bin/python -m packages.service_runtime --config /srv/ms-collab/runtime/restore.json check
```

Paths use one leading slash; implementation-defined double-leading-slash aliases
are rejected for configuration, source and destination alike.
Source and target paths must be disjoint: identical paths and either nesting
direction are rejected before output is allocated, so a restore cannot damage
the source checkpoint by creating a child directory inside it. The existing live
directory is never replaced. Source layout/hash and current code
schema must agree before allocating the target. Restoration cannot revive a
backed-up login session because none is copied; everyone must authenticate again
through a future separately accepted provider. A policy backup can still predate
membership/account revocations or business changes. Reconcile those changes in an
isolated restore before any future identity/provider enablement or promotion.
This tool does not silently promote the restored directory or enable login.

## Safe code upgrade and rollback boundary

1. Freeze writers and take/verify a cold backup under the **old compatible code**.
2. Keep the old code/configuration/checkpoint together in private operator storage.
3. Stage the new reviewed code separately, run its tests and check the existing
   installation. Compatible exact layouts may reopen; unknown/newer/older layouts
   fail closed, never by deleting metadata or silently rewriting schema.
4. If layout differs, use only a separately reviewed explicit migration tool and
   a tested recovery plan. **This slice implements no schema upgrade command.**
5. A rollback of code is not permission to roll back current security policy.
   Restore only to a new isolated directory with compatible code; recheck revoked
   identities/grants and data-loss consequences before any operator-approved cutover.

Do not change SQLite PRAGMAs, application IDs, manifest hashes, clock floors or
policy rows to force a failed check green. Database files are capped at 256 MiB
for this installation tool's bounded checks/backup, not a scalable storage backend.

## Evidence

- [Gunicorn 27.0.1 on PyPI](https://pypi.org/project/gunicorn/27.0.1/), wheel hash
  pinned in the dependency file, reviewed 2026-11-15. No extras are installed.
- [Gunicorn settings](https://gunicorn.org/reference/settings/) for the explicit
  server limits; [deployment guidance](https://gunicorn.org/deploy/) for buffering
  and operational boundaries. The runtime's stricter choices are local policy,
  not a claim that default Gunicorn settings satisfy this service's contract.
- [Documentation index](../../docs/README.md) and [deployment examples](../../deploy/README.md).

## GUI grant schema compatibility

The current policy schema includes persistent GUI publisher/reader grants. Old
installation or backup fingerprints are rejected without modifying their bytes.
The [offline GUI schema helper](../gui_smoke/GRANTS.md#legacy-stores-and-recovery-boundaries)
does not update runtime manifests or authorize a deployment migration.

## Administrative retry schema compatibility

The expected policy schema now includes identity-owned v2 administrative retry
records. Old installation/backup digests remain unavailable and are never
rewritten by `check`, startup or restore. The standalone [offline retry helper](
../workspace_admin_api/README.md#explicit-offline-v1-conversion) preserves old keys
as unbound conflict tombstones; it is not an installation/manifest upgrade tool.
Do not delete or edit manifests to bypass this gate. A full runtime upgrade and
production recovery plan remains separate work.


## Exact installed profiles and handover-history foundation

`init` and `initialize(config_path)` still create **I1** by default. I1 is the
exact pre-handover layout, not an alias for whatever schema current modules
export. [The frozen declarations](_layout_v1.py) retain the baseline SQL and its
source commit; [the finite registry](schema.py) checks the actual SQLite-normalized
schema. Compatibility tests compare current module declarations and old runtime
behavior with this frozen contract. Schema drift requires reviewed evolution,
not refreshing I1's fingerprint to make an old installation pass.

A separate, explicit **I2** creates a new directory with the same I1 tables plus
one dedicated permanent administrator-handover history table:

```sh
.venv/bin/python -m packages.service_runtime --config /srv/ms-collab/runtime/service.json init --layout 2
```

Both serving layouts retain disabled real login and the same application/SQLite core and
login versions. I2 now mounts the [explicit administrator handover adapter](../workspace_admin_api/README.md#atomic-administrator-handover)
and its two management clients through the real factory's verified private
handle. I1 keeps every prior route and does not mount this subtree. No old
installation gains handover support just by running the new binary. Constructors/check/HTTP do no
DDL. Unknown formats, booleans/floats in format fields, extra objects, mixed
fingerprints and partial layouts fail closed, without rewriting a manifest.
An older one-layout binary rejects I2; it must not be used to “repair” it.

Installation and backup formats are separately registered:

| Installation | Backup envelope | Restore result |
| --- | --- | --- |
| I1, installation `format:1` | Original B1 `format:1`, unchanged keys | New I1 directory |
| I2, installation `format:2` | B2 `format:2` plus exact `installation_format:2` | New I2 directory |
| I3, installation `format:3` (no service) | B3 `format:3` plus exact `installation_format:3` | New I3 directory |

B1/B2/B3 validation selects only these exact pairs. Restore preserves the profile,
not “latest”; no source or backup is upgraded. Source validation precedes output
allocation. Every restore creates empty login/session state with a nondecreasing
security floor. Successful ordinary factory creation can still maintain the
existing login clock; it is not a byte-read-only operation. Before-commit failure
with confirmed rollback leaves no partial transaction. Commit/I/O or final
publication failures may have an unknown outcome; a failure response alone is
not proof that nothing was written. Incomplete new directories are retained for
inspection and never silently reused. No target is automatically activated.

[Retained receipt validation](../workspace_admin_api/transfer_records.py) checks
all rows with bounded natural-PK keyset pages, complete byte-prefix projections,
canonical intent/provenance, duplicate-column consistency and the exact two
historical audit events. A full historical vector permits proof validation
without substituting current identity or roles. Valid stale/revoked/NULL-current
ownership is retained, not repaired, rebound or treated as a corrupt backup.
Runtime inspection, backup verification and restore all apply this structural
validation. It is **not authorization to replay**; the online adapter separately
requires current admin authority and the original identity/ownership pins.
A legitimately stale retained receipt remains a valid backup record even when
its current-authority replay must be refused.

The fixed history format uses 4 KiB canonical intent and 32 KiB canonical review
vector bounds. The complete vector's conservative maximum is 32,725 ASCII bytes,
including non-BMP policy identities, repeated sidecars, encoded route and JSON
structure. Policy identity strings keep their existing `identifier` semantics;
complete UTF-8 BLOB projections preserve embedded NUL/DEL. A raw NUL suffix after
JSON, or a malformed fixed UUID/proof, is invalid; an escaped NUL inside a valid
policy identity is not truncated or rejected by an authentication-string helper.
There is no token, secret or grant in this storage format.

Consumed history is permanent: no TTL, pruning or reset on identity changes.
Validation enforces at most 256 receipts per local actor/workspace partition,
including counts spanning page boundaries. The writer resolves an existing key
before counting a fresh partition and rejects excess fresh requests rather than
evicting history. Different generations of the same
local actor ID share that partition. The total is a partition-count-dependent
bound, not a constant database size. The full scan is O(history) with bounded
receipt working memory; the existing core loader still materializes its policy
and audit history, and the runtime's 256 MiB database cap remains in force.

There is **no conversion command** in this slice. A possible later conversion
would consume a verified cold B1 backup into a different, new I2 directory,
preserve all old pins/keys/audit, add only empty transfer history and fresh login,
and require an independent review and cutover decision. It must never infer old
receipts from past promote/demote events. In-place upgrade, down-conversion,
production cutover, arbitrary old-layout migration and identity enrollment are
not implemented. A whole-database rollback can erase later revocations or
receipt consumption; checksums and a fresh login do not provide anti-rollback.

New test sources live in the already-discovered `service_runtime/tests` and
`workspace_admin_api/tests` roots. Their scope is exact profiles, codec, controlled
synthetic historical records, restart and backup/restore. The additional
`test_transfer_profile.py` exercises actual I1/I2 factories and restored-profile
mounting; the separate adapter/management suites cover synthetic HTTP authorization
and the unknown-outcome UI. None establishes real login or production recovery.
Existing runtime/TLS and product suites remain unchanged acceptance gates; actual
execution results belong to the exact revision's CI evidence. Online admission
uses a factory-prepared exact registry with bounded complete-byte projections,
not per-request offline `inspect`, full-history validation or in-memory DDL.


## Fresh preset storage and cold recovery (I3, no service)

`init --layout 3` is a separate, explicit **new-directory-only** choice. It
adds exactly one frozen product-preset table to I1, matching the
[product-view preset contract](../artifact_sync/PRODUCT-VIEWS.md). It is not
I2 plus presets: it does not add handover history or any download policy/lease
tables. Omitting `--layout` still creates I1. Existing I1/I2 installations and
B1/B2 backup pairs are unchanged. B3 is separately paired with I3.

Use a private, disposable installation directory with the normal disabled
login configuration. There is no table adoption, conversion or in-place DDL
on `check`, `serve` or restore; an existing source is never reused. Do not
run a separate initializer against an installed database to bypass the exact
profile check.

```sh
.venv/bin/python -m packages.service_runtime --config /srv/ms-collab/runtime/disposable.json init --layout 3
.venv/bin/python -m packages.service_runtime --config /srv/ms-collab/runtime/disposable.json check
```

A successful I3 check is read-only and certifies installation structure, not
preset rows or product acceptance. Initialization leaves the preset table
empty and creates no identity, workspace, grant, publication or session. The
factory rejects I3 before session-store initialization; the CLI cannot serve
it. A B3 cold backup checks the exact I3 manifest under an exclusive lease
before opening the attached databases for writing. Like B1/B2, it can recover
an interrupted SQLite transaction after admission. Unsupported layouts or
invalid manifests are rejected before any writer can recover a journal.

B3 contains only the checked policy SQLite image and its private manifest. It
preserves preset rows and stored provenance exactly, without interpreting them
as current authority or rebinding scopes. Host-provided product definitions,
runtime configuration, certificates, credentials and login state are not
included; do not publish the archive. The digest detects corruption, not an
adversary who can rewrite both archive files. Restore verifies the declared
pair, config binding, checksums, core schema and policy before allocating a
new directory, creates a fresh empty login store at a nondecreasing clock
floor, and never activates service. I3 cannot be relabeled as B1/B2; no
conversion or in-place restore is supported. An incomplete new destination
is retained privately and is never silently reused. The ordinary
`check --require-production` still exits 3 on any valid installation.

This is a storage foundation only. It does not mount product views, presets or
downloads, enable any login, import real data, or substitute for the target
workspace acceptance by an administrator and an independent member.
