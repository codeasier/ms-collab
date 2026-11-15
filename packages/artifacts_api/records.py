# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Bounded metadata records; no file bytes, links, provider or credentials."""
from dataclasses import asdict
from hashlib import sha256
import json

from packages.artifacts import Artifact, Definition, SavedView, Scope, Snapshot
from packages.workspaces import Resource, StorageError


def record_id(scope, kind, view_id=None):
    # A digest avoids ambiguous delimiters and resource identifier length growth.
    # Full scope is also validated inside the record, not trusted from a digest.
    payload = json.dumps([scope.connection_id, scope.bucket_id, view_id],
                         ensure_ascii=True, separators=(",", ":"))
    return kind + ":" + sha256(payload.encode("ascii")).hexdigest()


def catalog_resource(snapshot):
    """Trusted bootstrap/import helper; not an HTTP upload or importer."""
    if type(snapshot) is not Snapshot:
        raise ValueError("validated snapshot required")
    data = {"schema": 1, "scope": snapshot.scope.as_dict(),
            "observed_at": snapshot.observed_at, "offset_minutes": snapshot.offset_minutes,
            "artifacts": [asdict(a) for a in snapshot.artifacts]}
    return Resource(snapshot.scope.workspace_id, "artifacts", record_id(snapshot.scope, "catalog"),
                    json.dumps(data, ensure_ascii=True, separators=(",", ":")))


def view_resource(view):
    """Trusted bootstrap helper for filters, never a grant or shared capability."""
    if type(view) is not SavedView:
        raise ValueError("validated view required")
    data = {"schema": 1, "scope": view.scope.as_dict(), "view_id": view.view_id,
            "name": view.name, "definition": asdict(view.definition)}
    return Resource(view.scope.workspace_id, "artifacts", record_id(view.scope, "view", view.view_id),
                    json.dumps(data, ensure_ascii=True, separators=(",", ":")))


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _load(resource, scope, fields):
    if resource.workspace_id != scope.workspace_id or resource.module_key != "artifacts":
        raise ValueError("resource scope mismatch")
    data = json.loads(resource.text, object_pairs_hook=_unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if (type(data) is not dict or set(data) != fields or type(data["schema"]) is not int
            or data["schema"] != 1 or data["scope"] != scope.as_dict()):
        raise ValueError("invalid metadata record")
    return data


def load_catalog(resource, scope):
    try:
        data = _load(resource, scope, {"schema", "scope", "observed_at", "offset_minutes", "artifacts"})
        if type(data["artifacts"]) is not list:
            raise ValueError()
        rows = []
        for item in data["artifacts"]:
            if type(item) is not dict or set(item) != {"key", "modified_at", "size", "is_dir"}:
                raise ValueError()
            rows.append(Artifact(**item))
        return Snapshot(scope, data["observed_at"], tuple(rows), data["offset_minutes"])
    except (ValueError, TypeError, KeyError, RecursionError):
        raise StorageError("invalid_store") from None


def load_view(resource, scope, view_id):
    try:
        data = _load(resource, scope, {"schema", "scope", "view_id", "name", "definition"})
        if data["view_id"] != view_id:
            raise ValueError()
        return SavedView(scope, view_id, data["name"], Definition.from_dict(data["definition"]))
    except (ValueError, TypeError, KeyError, RecursionError):
        raise StorageError("invalid_store") from None
