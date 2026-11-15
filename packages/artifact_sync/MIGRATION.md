# Artifact sync migration provenance

Reviewed private source baseline:
`codeasier/msinsight-obs-web-browser@f6c184e81671b460b9322c9f8bf0cadc70599d76`.
Source and destination use MIT with the same copyright holder. This is a fresh
scoped implementation of reusable behavior, not a copy of repository history,
operations documents, credentials, deployments, logs or production databases.

| Reviewed source | Re-expressed behavior | Excluded or deliberately changed |
| --- | --- | --- |
| `backend/sync-worker/obs_sdk_handler.py` | ListObjects pagination, recent-window filtering, advancing-marker guard, four-field object normalization | SDK/global configuration, default region/bucket, environment AK/SK, product keyword filters, checksum omission, guessed local timestamps and operational logs |
| `backend/sync-worker/sync_worker.py` | Incremental merge after a successful complete bounded scan | Process scheduler, task database, deployment paths, automatic publication and real data movement |
| `backend/modules/artifacts/quarterly_store.py` | Quarter projection and virtual parent behavior, reused through the migrated artifact core | Cross-file publication and implicit/global scope; index and audit now commit with current authorization in one SQLite database |

## Protocol sources

The standard REST transport follows Huawei's primary documentation:

- [Listing Objects](https://support.huaweicloud.com/intl/en-us/api-obs/obs_04_0022.html):
  bucket-root GET, URL-encoded prefix/marker, bounded pages and continuation.
- [Header authentication](https://support.huaweicloud.com/intl/en-us/api-obs/obs_04_0010.html):
  OBS HMAC-SHA1 signature with date, optional session header and bucket resource;
  listing parameters are not signed subresources. No signed URL is generated.
- [Bucket naming restrictions](https://support.huaweicloud.com/intl/en-us/usermanual-obs/en-us_topic_0045853662.html):
  the implementation excludes dotted labels for virtual-host HTTPS compatibility.

These references define the intended protocol, not live compatibility evidence.
Tests use synthetic XML and credentials only. A real scoped integration must still
verify region/namespace/encoding/signature behavior with a least-privilege account,
without loosening validation or treating unknown formats as empty success.

## Remaining boundaries

This adds a bounded read-only provider, persisted index and explicit publication
seam. It does **not** migrate real data, a scheduler, deployment/secret management,
large-scale history, provider versions/deletions, editable views, configuration
management UI, refresh/approval UI or protected file download. Existing static
catalog APIs remain available separately; the new index adapter must be selected
explicitly by trusted app setup. See [integration contract](README.md).

The later [administration slice](../artifact_admin/README.md) supplies a separately
configured refresh/approval UI/API. It does not remove the provider compatibility,
production migration or download boundaries above.
