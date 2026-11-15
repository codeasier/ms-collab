# Test-only composition

`browser_server.py` deliberately uses a synthetic verifier/provider and temporary
SQLite store. It must never be imported as deployment configuration. No network
provider access, real credentials, old service data or production settings.
