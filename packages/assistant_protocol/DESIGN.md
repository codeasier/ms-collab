# Source assessment and workspace-scoped runner design

## Verified source, not a deployment endorsement

Reviewed OBS at fixed commit `f6c184e81671b460b9322c9f8bf0cadc70599d76`:

| Source | Verified behavior | Migration decision |
| --- | --- | --- |
| `docs/assistant-native-acp.md`, `assistant_native_acp.py` | Native ACP initialization, new session and latest-message prompt; provider-supported native restore, bounded session pool, private session state. | Migrate a small byte-level protocol core only. No live pool, restoration, provider or state directory. |
| `assistant_native_worker.py` | Owner/lease/config/authorization rechecks before dispatch and publication; interruptions do not blindly replay prompts. | Preserve current-authority admission observations and atomic audit. Do not call an audit receipt a lease or capability. |
| `assistant_workspace.py`, `docs/assistant-isolation.md` | `legacy` may launch a configured host command. `sandbox_v1` builds a fixed bubblewrap argv, isolates namespaces, drops capabilities, removes inherited environment, disables network and uses bounded private tmpfs. | No legacy path. No command/env/path configuration or launch code in this increment. |
| `test_assistant_isolation.py` | Some subprocess contract tests replace the launcher. Source documentation explicitly says these are not OS isolation acceptance. | Synthetic protocol tests must not be described as real sandbox or provider acceptance. |
| `assistant_native_service.py` and saved history | Authentication derives owner; native policy epochs and source permissions are checked before disclosure. | Existing ms-collab owner/dependency fences stay intact. History includes realm/principal generation; executable admission and resource-specific lineage remain future gates. |

The protocol shape was checked against the official ACP v1
[initialization](https://agentclientprotocol.com/protocol/v1/initialization),
[session setup](https://agentclientprotocol.com/protocol/v1/session-setup) and
[prompt-turn](https://agentclientprotocol.com/protocol/v1/prompt-turn) contracts.
This subset does not advertise optional client capabilities or claim complete
compatibility with current/future ACP extensions. A provider requiring unsupported
features must fail closed rather than trigger guessed authentication or retries.

On 2026-11-15, the development container had bubblewrap 0.12.0 but a harmless
`/usr/bin/true` namespace probe failed creating a `NETLINK_ROUTE` socket with
`Operation not permitted`. This demonstrates a local execution blocker, not a
universal claim about Linux or hosted CI. No settings were relaxed and no real
agent was launched. A hosted test of pure protocol objects does not remove it.

## Proposed future runner, deliberately not implemented here

The following is design, not a supported configuration or API:

1. **Admission and exact scope.** Derive principal from the live session in the
   policy transaction. Bind owner + workspace + conversation + single operation,
   exact current revisions, server-selected immutable runtime/image digest,
   bounded prompt digest and a monotonic authorization generation. A server-owned
   operation ID and CAS lifecycle must distinguish accepted, dispatch-unknown and
   definitely-not-started states. Do not convert a display-history read or a
   blocked preflight receipt into execution permission.
2. **Conservative dependency contract.** Initially grant no platform data, source
   mounts or MCP. Later reads need exact object-level lineage and authorization
   before dispatch and before release, not only a module permission or page hint.
   A change of relevant policy invalidates the whole native session and any
   pending publication. The history foundation includes realm and principal generation; a real runner
   must carry that fence through admission, dispatch, release and cancellation,
   not rely only on current principal-value equality.
3. **Independently accepted isolation.** Launch only a server-owned pinned
   executable/image in a freshly scoped OS/container sandbox with no host HOME,
   database, repository, sockets, private runtime state or credentials mounted.
   Require distinct process/user/mount/network namespaces, no capabilities or
   nested namespaces, read-only public runtime dependencies, bounded writable
   scratch, CPU/memory/PID/file/output limits and no inherited descriptors/env.
   The `/workspace` protocol cwd names the sandbox's internal scratch only. An
   arbitrary profile cwd, a private HOME or a patched launcher is not acceptance.
4. **No network by default.** Initial malicious offline fixture tests must prove
   no host/sibling reads, writes, network access or process escape. A remote model
   would require a separately approved least-privilege egress/secret broker; do
   not inject all host credentials, a general proxy or unrestricted network.
5. **Dispatch and publication fences.** Recheck current authority immediately
   before each request and on every callback/result, in addition to startup.
   Durable operation/worker ownership uses CAS and lease generations. Detach
   display callbacks before cancellation. Rejected late updates cannot append to
   history. No cached capability or user-supplied owner/lease substitutes for this.
6. **Stop is observable.** Protocol cancellation is a request, not evidence that
   descendants or network I/O stopped. Revoke broker permissions first; terminate
   and reap the exact sandbox process tree with deadlines, then confirm private
   scratch cleanup. Unknown/timeout outcomes remain unknown and are not replayed.
   A crash requires recovery based on durable leases and independently observed
   process exit, not a new prompt or recursive cleanup of a guessed directory.
7. **Audit without content leakage.** Separately record authorized admission,
   intended dispatch, observed response and verified stop/cleanup transitions.
   Never equate an agent-reported tool status or stop reason with OS execution
   proof. Exclude raw prompts, output, private provider IDs and credentials from
   audit; sanitized display publication needs its own provenance/redaction policy.

## Acceptance prerequisites

Before adding an HTTP execution route or UI submit button, independent tests must
run against the **unchanged real launcher** on a supported target OS. They must
include malicious child/descendant escape probes, resource exhaustion, host and
sibling isolation, cancellation during setup/prompt/tool I/O, policy changes while
queued/running, worker crash, unknown dispatch, no replay, credential expiry,
audit failure and namespace/network failures. Kernel/runner dependencies and all
required permissions must be explicit. Skipping or replacing an unavailable
sandbox is a failed prerequisite, not a green execution test.

This increment completes none of those execution acceptance prerequisites. It
adds useful parser/state and refused-admission regressions without weakening the
current closed execution surface. Real provider setup, secrets, paid requests,
OS/network changes and production deployment are not part of this work.
