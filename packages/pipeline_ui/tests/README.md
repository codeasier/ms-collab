# Pipeline UI service tests

`python3 -m unittest discover -s packages/pipeline_ui/tests -v`

Synthetic-only composition verifies static allowlisting, origin/TLS guards,
no anonymous authority, workspace separation, provider-to-store-to-API reads and
absence of trigger/retry/log/provider-refresh routes. The browser fixture uses a
fixed synthetic clock and test-only verifier/provider; it is never a deployment
entry point. [UI boundary](../README.md)
