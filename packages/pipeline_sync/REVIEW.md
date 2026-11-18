# Review a pipeline snapshot replacement

**Who this serves.** An operator using a trusted local integration can inspect
exactly what a single bounded acquisition would publish. After publication, an
ordinary authorized member uses the existing list, detail, task and duration
views. The operator does not trigger a run or send changes to CI. There is no
new web endpoint. This is not a change to the historical data or a cutover.

**Decide the audience before wiring the feature.** The stored resource is
visible to **all current and future members with pipeline read access in the
chosen workspace**, not only to the operator. A provider token proves neither
ownership nor permission to disclose its results. An integration owner must
independently authorize the fixed upstream projection, its source, who can
review and publish it, and this audience. Stage and job names may contain
private text; the allowlist is not a secret scanner. Do not use this path if
the source requires per-run readers, a different approver, retractable source
grants on already published content, or a narrower audience.

**What happens.**

1. The trusted operator integration supplies the exact configured connection,
   its scoped upstream credential, a verified user's existing credential, the
   expected stored revision, an explicitly chosen transport and an explicit
   review policy. The policy pins the source/limits, reviewer identity and
   membership, and intended publication audience. There are no credentials in
   the prompt and no discovery of them from the environment. A person cannot
   designate themself as a reviewer from a web form or ordinary reader API.
2. The integration validates current authority and the previous observation,
   then uses the bounded list and exact run-detail GETs to acquire a candidate.
   Acquiring it does **not** change the readable snapshot. The checks do not
   cancel external bytes already dispatched before a revocation.
3. On the operator's private controlling terminal, review the **whole** new
   normalized snapshot, destination and baseline revision. Inspect removed run
   IDs too: a shorter, nonempty observation replaces the stored subset if
   approved. An empty candidate is never published by this reviewed path.
   Missing runs are not proved to have been deleted upstream. Leaving
   the current snapshot untouched is the right choice if any scope, field,
   reader audience or removal looks wrong.
4. Confirm the displayed digest exactly to approve this one replacement. There
   is no default affirmative answer. On denial, terminal failure, expiry or a
   competing change no replacement is committed by this operation. If it does
   commit, the previous resource revision and content, current authority and
   scope are checked again and the validated whole candidate and its local audit
   event are committed together. The service does not retry the publication.
   If the caller loses the response, reread the current resource; a newer
   revision by itself does not prove which writer committed it.

**Integration boundary.** Construct the policy in trusted setup from a
separately approved reviewer binding, not by copying the identity of whoever
sent a request. Pass it to the reviewed method, not the original direct
publisher:

```python
from packages.pipeline_sync import PipelineReviewPolicy

policy = PipelineReviewPolicy(
    connection=approved_connection,
    reviewer_pin=approved_reviewer_pin,  # realm, local ID, issuer, subject, generation
    reviewer_epoch=approved_authority_epoch,
    membership_revision=approved_membership_revision,
    audience="current-and-future-workspace-pipeline-readers/v1",
)
result = synchronizer.reviewed_refresh(
    user_credential=operator_credential,
    connection=approved_connection,
    upstream_credential=bound_upstream_credential,
    expected_revision=previous_revision,
    review_policy=policy,
)
```

The names above represent inputs already provided through trusted integration
code. They are not settings to derive from a request or instructions to obtain
credentials. The synchronizer needs an explicitly supplied transport; the
reviewed call does not silently choose one. The original direct method remains
available to its existing trusted callers; this new path is not a global
publication interlock.

**Operational limits.** The reviewed method supports a foreground Linux devpts
controlling terminal in canonical mode owned by the current OS user with mode
0600. It refuses unsupported terminal translations that could change the
review text, as well as group-writable terminals; it does not change terminal
settings or permissions. Redirecting stdin or stdout does not select a review surface.
A controlling terminal is not proof of human identity:
a shared, recorded, remotely mirrored or automated terminal is not a private
review surface. Do not expose this trusted method through a web handler, IPC
service or unattended automation. Use only a private, attended terminal on a
trusted host. The integration must serialize reviews on each terminal: exactly
one pending review and no other reader or writer, even for the same operator.
This method does not lock the terminal against other processes or threads. An
operator may already have seen data before access is revoked.
There is no private candidate table, token to pass to a browser, Cookie
permission, new identity enrollment, or change to the installed schema. Other
authorized writers are not disabled. A digest binds the review, not provenance
of all historical snapshots.

The review time limit fences publication, not operating-system input or DNS:
the process may still be waiting for input after the review expires. It does
not hold a database transaction while waiting for the operator. Each invocation
is for one operator; this is not shared-service admission control. Hosting a
multi-user refresh service would require independently reviewed capacity, total
deadlines, uncertain cleanup, session authority and durable candidate isolation.

The terminal check occurs after collection; a missing or unsupported terminal
prevents display and publication, not provider GETs that already ran under the
trusted caller's authority. The five-minute decision window starts at the
pre-display authorization check and is checked again on the final write. Neither
terminal output nor a dispatched provider request can be made atomic with a
separate revocation. Already authorized or visible bytes cannot be taken back.
