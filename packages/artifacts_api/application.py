# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Read-only artifact HTTP adapter sharing transactional identity/policy checks."""
import json
import re
from urllib.parse import parse_qsl

from packages.artifacts import ArtifactCatalog, Definition, Scope
from packages.artifacts.catalog import quarter
from packages.workspace_api import WorkspaceApplication
from packages.workspace_api.application import _object
from packages.workspaces import PolicyError
from .records import load_catalog, load_view, record_id

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _invalid():
    return PolicyError(400, "invalid_request")


def _page(data):
    page, size = data.get("page", 1), data.get("page_size", 50)
    if type(page) is not int or not 1 <= page <= 1000000 or type(size) is not int or not 1 <= size <= 100:
        raise _invalid()
    return {"page": page, "page_size": size}


class ArtifactApplication(WorkspaceApplication):
    """No UI/login/listener or provider. Config comes from a trusted app factory.

    Every request loads catalog and view records AFTER current authorization in
    the same SQLite snapshot; no globally configured metadata bypasses policy.
    """
    def _dispatch(self, environ):
        assertion = self._verify_request(environ)
        scope, kind, selected_quarter, definition, view_id, pagination = self._request(environ)

        def operation(policy, actor):
            # read_resource rechecks member/module/capability before existence.
            record = policy.read_resource(actor, scope.workspace_id, "artifacts", record_id(scope, "catalog"))
            catalog = ArtifactCatalog((load_catalog(record, scope),))
            if kind == "quarters":
                return {"scope": scope.as_dict(), "catalog_revision": record.revision,
                        "quarters": list(catalog.quarters(scope))}
            view_revision = None
            if kind == "view":
                view_record = policy.read_resource(actor, scope.workspace_id, "artifacts", record_id(scope, "view", view_id))
                view = load_view(view_record, scope, view_id)
                result = catalog.apply_view(scope, selected_quarter, view, **pagination)
                view_revision = view_record.revision
            else:
                result = catalog.query(scope, selected_quarter, definition, **pagination)
            result["catalog_revision"] = record.revision
            if view_revision is not None:
                result["view_revision"] = view_revision
            return result

        return self._authenticated_transaction(assertion, operation)

    @staticmethod
    def _query_body(environ):
        if environ.get("CONTENT_TYPE", "").lower() not in {"application/json", "application/json; charset=utf-8"}:
            raise PolicyError(415, "json_required")
        length = environ.get("CONTENT_LENGTH", "")
        if type(length) is not str or not re.fullmatch(r"[0-9]{1,6}", length):
            raise PolicyError(411, "length_required")
        if int(length) > 16384:
            raise PolicyError(413, "body_too_large")
        try:
            raw = environ["wsgi.input"].read(int(length))
            if type(raw) is not bytes or len(raw) != int(length):
                raise ValueError()
            body = json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if type(body) is not dict or not {"quarter", "definition"} <= body.keys() or set(body) - {"quarter", "definition", "page", "page_size"}:
                raise ValueError()
            return quarter(body["quarter"]), Definition.from_dict(body["definition"]), _page(body)
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise _invalid() from None

    def _request(self, environ):
        path, verb, query = environ.get("PATH_INFO", ""), environ.get("REQUEST_METHOD", ""), environ.get("QUERY_STRING", "")
        if type(path) is not str or len(path) > 1000 or type(query) is not str or len(query) > 256:
            raise _invalid()
        parts = path.split("/")
        if (len(parts) not in (8, 9) or parts[:3] != ["", "api", "workspaces"]
                or parts[4] != "artifacts" or any(not _ID.fullmatch(p) for p in parts[3:])):
            raise PolicyError(404, "not_found")
        scope = Scope(parts[3], parts[5], parts[6])
        if len(parts) == 8 and parts[7] == "quarters" and verb == "GET" and not query:
            return scope, "quarters", None, None, None, {}
        if len(parts) == 8 and parts[7] == "query" and verb == "POST" and not query:
            selected, definition, pagination = self._query_body(environ)
            return scope, "query", selected, definition, None, pagination
        if len(parts) == 9 and parts[7] == "views" and verb == "GET":
            try:
                values = _object(parse_qsl(query, keep_blank_values=True, strict_parsing=True, max_num_fields=3))
                if "quarter" not in values or set(values) - {"quarter", "page", "page_size"}:
                    raise ValueError()
                selected = quarter(values.pop("quarter"))
                if any(not re.fullmatch(r"[0-9]{1,7}", value) for value in values.values()):
                    raise ValueError()
                pagination = _page({key: int(value) for key, value in values.items()})
            except (ValueError, TypeError):
                raise _invalid() from None
            return scope, "view", selected, None, parts[8], pagination
        raise PolicyError(404, "not_found")
