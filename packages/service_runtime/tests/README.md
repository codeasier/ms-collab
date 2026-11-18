# Runtime installation tests

- `test_runtime.py`: strict config, no auto bootstrap/identity, private paths,
  schema/time/binding checks, current cookie host, cooperative maintenance locks,
  cold backups without login material, old admin-retry schema/manifest rejection without rewrite, new-directory restoration and redacted CLI.
- `test_server.py`: pinned fixed server assembly, raw-header rejection, environment
  override refusal, TLS floor and logger/error redaction without socket access.
- `https_acceptance.py`: hosted CI only, real loopback TLS process and raw HTTP
  adverse cases with temporary synthetic certificates. No OS trust changes.

Run with the [locked server dependency](../../../deploy/requirements.txt):

```sh
python3 -m unittest discover -s packages/service_runtime/tests -v
python3 -m packages.service_runtime.tests.https_acceptance
```

Do not upload test runtime DBs, certificate keys or server raw output. CI reports
only assertion summaries and fixed success text. Return to [runtime](../README.md).

The invitation recipient-selection regression in `test_server.py` uses the
existing Gunicorn raw-parser harness to reject duplicate
`If-Invitation-Recipient-Match` names and underscore aliases before WSGI mapping.
It adds no runtime startup, identity enrollment, provider or configuration enablement.
