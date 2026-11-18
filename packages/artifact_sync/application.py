# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Same read-only artifact routes, backed by explicitly published indexes."""
from packages.artifacts import ArtifactCatalog
from packages.artifacts_api import ArtifactApplication
from packages.artifacts_api.records import load_view, record_id
from packages.workspaces import PolicyError
from .records import authorized, decode_snapshot
from .discovery import directory, directory_request
from .product_views import ProductViewRegistry, framed_body, product_result, product_route
from packages.directory_cursors.directory import configured, serve


class IndexedArtifactApplication(ArtifactApplication):
    def __init__(self, *, directory_cursors=None, product_views=None, **kwargs):
        super().__init__(**kwargs)
        self._directory_cursors = configured(directory_cursors)
        self._product_views = ProductViewRegistry(product_views)

    def _dispatch(self, environ):
        assertion = self._verify_request(environ)
        listing = directory_request(environ, 'artifacts')
        if listing is not None:
            workspace, query = listing
            return serve(self, assertion, workspace=workspace, query=query,
                         action='artifact.scopes.read', store=self._directory_cursors,
                         projection=lambda policy, actor, db: directory(policy, actor, db, workspace))
        route = product_route(environ) if self._product_views else None
        if route is not None:
            scope, view_id = route
            raw = framed_body(environ)

            def product_read(policy, actor, db):
                record = authorized(policy, actor, db, scope, 'read')
                return product_result(self._product_views, scope, view_id, raw, record)

            return self._authenticated_transaction(assertion, product_read, with_connection=True)
        scope, kind, selected, definition, view_id, pagination = self._request(environ)
        def operation(policy, actor, connection):
            record = authorized(policy, actor, connection, scope, 'read')
            if record.published is None:
                raise PolicyError(404, 'not_found')
            snapshot = decode_snapshot(record.published, record.policy.connection)
            catalog = ArtifactCatalog((snapshot,))
            if kind == 'quarters':
                result = {'scope': scope.as_dict(), 'quarters': list(catalog.quarters(scope))}
            elif kind == 'view':
                view_record = policy.read_resource(actor, scope.workspace_id, 'artifacts', record_id(scope, 'view', view_id))
                result = catalog.apply_view(scope, selected, load_view(view_record, scope, view_id), **pagination)
                result['view_revision'] = view_record.revision
            else:
                result = catalog.query(scope, selected, definition, **pagination)
            result['catalog_revision'] = record.published_revision
            result['source'] = {'kind': 'stored_index', 'full_history_complete': False,
                                'live_provider_verified': False, 'download_available': False}
            return result
        return self._authenticated_transaction(assertion, operation, with_connection=True)
