# Artifact download migration boundary

[Package navigation](README.md)

## Source and deliberate redesign

The authorized private OBS source was pinned at
`f6c184e81671b460b9322c9f8bf0cadc70599d76`; only the artifacts read-port contract
was inspected for this slice. Its indexed-path probe and download-link handoff
were used as functional intent, not copied as deployment defaults. This package
replaces link generation with a scope-checked server byte proxy. It does not
export source operation notes, account settings, keys, live listings or logs.

The existing public catalog and approval packages remain backward compatible.
Approval of catalog metadata does not become implicit approval of object bytes.
New content bindings and downloader grants are trusted setup, separate from
catalog readers and approvers. No change is made to the existing metadata UI's
`download_available: false` source flag because that API is metadata-only. The optional unified session UI obtains byte
authority separately through the new availability endpoint.

## Verified scope

Synthetic object content only. Tests exercise real SQLite authorization/index/
lease transactions, source binding, hashing before output, full HTTP delivery,
interrupted iterables, read-time revoke and concurrent admission. Protocol tests
use controlled wire fixtures and DNS/socket doubles, not a live cloud account.
There is no production data movement, private endpoint, live secret or deployment.

## Remaining work

- Independently approved real content bindings and least-privilege read-only
  cloud credentials, plus actual provider/identity compatibility acceptance.
- Deployable composition with resolver/worker/proxy deadlines, disconnect
  propagation, protected temp storage and audited crash-reservation recovery.
- The optional cookie-session browser interaction is implemented and exercised
  with synthetic bytes. Real browser-to-cloud/deployment acceptance remains open.
- Larger objects, ranges/resume, bucket version IDs, customer-key encryption,
  content-approval management and retention automation are not implemented.
- A successful small-object synthetic test is not whole-OBS migration completion
  or a production availability/security certification.
