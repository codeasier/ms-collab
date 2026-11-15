# Local demo server

`ms_collab/` contains the new, read-only composition scaffold and synthetic module
catalog. It uses only Python 3.12+ standard-library modules. It is **not** a
migration of the production backend, identity provider or persistence layer.

From the repository root:

```sh
PYTHONPATH=backend python3 -m ms_collab --port 8000
python3 -m unittest discover -s backend/tests -v
```

Open exactly `http://127.0.0.1:8000`. Binding to other addresses, proxy operation,
real credentials and production data are intentionally unsupported. See
[demo boundaries and API](../docs/runnable-demo.md).
