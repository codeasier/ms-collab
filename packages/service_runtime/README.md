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
