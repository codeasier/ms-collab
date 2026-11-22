"""Authenticated catalog reads and a separate trusted multi-source sync seam."""
from datetime import datetime, timezone
import re
from urllib.parse import parse_qsl

from packages.identity import Credential
from packages.workspace_api import WorkspaceApplication
from packages.workspaces import PolicyError
from packages.workspaces.authority import principal_pin
from .core import AssetError, AssetScope, CatalogBinding, SourceBatch, MAX_ASSETS, MAX_BATCH_BYTES, canonical, identifier, now_utc, pairs, prepare_batch, text
from .store import publish, published_catalogs, query, state


class AssetApplication(WorkspaceApplication):
    """No listener, write route, credential loader or implicit shared-workspace access."""
    def _dispatch(self, environ):
        assertion = self._verify_request(environ)
        scope, endpoint, asset_id, options = self._route(environ)
        def read(policy, actor, conn):
            if endpoint == 'catalogs':
                return published_catalogs(policy, actor, conn, scope, options)
            current = state(policy, actor, conn, scope)
            return query(conn, scope, current, endpoint, asset_id, options)
        return self._authenticated_transaction(assertion, read, with_connection=True)

    @staticmethod
    def _route(environ):
        path, raw = environ.get('PATH_INFO', ''), environ.get('QUERY_STRING', '')
        if type(path) is not str or len(path) > 600:
            raise PolicyError(400, 'invalid_request')
        parts = path.split('/')
        if (len(parts) == 5 and parts[:3] == ['', 'api', 'workspaces'] and parts[4] == 'research-assets'
                and environ.get('REQUEST_METHOD') == 'GET'):
            try:
                return identifier(parts[3]), 'catalogs', None, raw
            except AssetError:
                raise PolicyError(404, 'not_found') from None
        if type(raw) is not str or len(raw) > 4096:
            raise PolicyError(400, 'invalid_request')
        if (environ.get('REQUEST_METHOD') != 'GET' or len(parts) not in (7, 8)
                or parts[:3] != ['', 'api', 'workspaces'] or parts[4] != 'research-assets'
                or parts[6] not in {'types', 'entities', 'assets'}):
            raise PolicyError(404, 'not_found')
        try:
            scope = AssetScope(parts[3], parts[5])
            asset_id = None
            if len(parts) == 8:
                if parts[6] != 'assets' or not re.fullmatch(r'a_[0-9a-f]{64}', parts[7]):
                    raise AssetError('invalid_asset')
                asset_id = parts[7]
            options = pairs(parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=6, errors='strict'))
            allowed = {'revision'} if asset_id is not None or parts[6] != 'assets' else {'type', 'entity', 'q', 'page', 'page_size', 'revision'}
            if not options.keys() <= allowed:
                raise AssetError('invalid_query')
            for key, value in tuple(options.items()):
                if key in {'page', 'page_size', 'revision'}:
                    if not re.fullmatch(r'[1-9][0-9]{0,8}', value):
                        raise AssetError('invalid_query')
                    options[key] = int(value)
                elif key in {'type', 'entity'}:
                    identifier(value)
                else:
                    text(value, 200)
            if options.get('page_size', 50) > 100 or options.get('page', 1) > 100000:
                raise AssetError('invalid_query')
            return scope, parts[6], asset_id, options
        except (ValueError, TypeError, UnicodeError):
            raise PolicyError(400, 'invalid_query') from None


class AssetSynchronizer:
    """Synchronous trusted host method, not an HTTP endpoint/background job.

    Provider.prepare(scope, source) must return a bounded SourceBatch. It must be
    a reviewed read-only adapter configured by the host; there is no default
    provider, local mirror access, outbound network or credential discovery here.
    """
    def __init__(self, *, store, providers, provider_key, provider,
                 clock=lambda: datetime.now(timezone.utc)):
        if provider is None or not callable(getattr(provider, 'prepare', None)):
            raise AssetError('provider_required')
        self._authority = WorkspaceApplication(store=store, providers=providers, provider_key=provider_key, clock=clock)
        self._provider, self._clock = provider, clock

    def refresh(self, *, user_credential, scope, expected_revision):
        if type(user_credential) is not Credential or type(scope) is not AssetScope:
            raise AssetError('invalid_request')
        if type(expected_revision) is not int or expected_revision < 1:
            raise AssetError('invalid_revision')
        assertion = self._authority._verify_request({'HTTP_AUTHORIZATION': 'Bearer '+user_credential.value})
        def flow_pin(policy, actor):
            # Capture only after current authorization. These coordinates fence
            # this call; they are not persisted grants or provider credentials.
            return (principal_pin(policy, actor),
                    policy._memberships[(scope.workspace_id, actor)].revision,
                    policy._workspaces[scope.workspace_id].revision,
                    policy._modules[(scope.workspace_id, 'research_assets')].revision,
                    policy._modules[(scope.workspace_id, 'research_assets_sync')].revision)
        def inspect(policy, actor, conn):
            current = state(policy, actor, conn, scope, write=True)
            policy._match(expected_revision, current['revision'])
            return flow_pin(policy, actor), current
        admitted, baseline = self._authority._authenticated_transaction(assertion, inspect, with_connection=True)
        def unchanged(policy, current_actor, conn):
            resolved, current = inspect(policy, current_actor, conn)
            if resolved != admitted:
                raise PolicyError(409, 'asset_authority_changed')
            if current['binding_text'] != baseline['binding_text']:
                raise PolicyError(409, 'catalog_changed')
            return current
        collected, evidence, seen, size = [], {}, set(), 0
        for source in baseline['binding'].sources:
            self._authority._authenticated_transaction(assertion, unchanged, with_connection=True)
            try:
                batch = self._provider.prepare(scope, source)
            except Exception:
                raise PolicyError(502, 'asset_source_unavailable') from None
            # No DB lock spans provider I/O. Revalidate its result before any
            # projection/next source, and again under the final writer lock.
            self._authority._authenticated_transaction(assertion, unchanged, with_connection=True)
            try:
                if type(batch) is not SourceBatch or batch.source_id != source.id or batch.scope != scope:
                    raise AssetError('source_mismatch')
                # Detach and bound each source before the next external call.
                one = CatalogBinding(scope, baseline['binding'].namespace, baseline['binding'].entities, (source,))
                assets, source_evidence = prepare_batch(one, (batch,), now_utc(self._clock))
                for asset in assets:
                    if asset['id'] in seen:
                        raise AssetError('identity_conflict')
                    seen.add(asset['id']); size += len(canonical(asset))
                    if len(seen) > MAX_ASSETS or size > MAX_BATCH_BYTES:
                        raise AssetError('batch_too_large')
                collected.extend(assets); evidence.update(source_evidence)
            except Exception:
                raise PolicyError(502, 'asset_source_unavailable') from None
        assets = tuple(collected)
        def commit(policy, current_actor, conn):
            current = unchanged(policy, current_actor, conn)
            revision, changed = publish(policy, current_actor, conn, scope, current, assets, evidence)
            return {'scope': scope.as_dict(), 'revision': revision, 'changed': changed, 'count': len(assets),
                    'live_provider_verified': False, 'execution_verified': False}
        return self._authority._authenticated_transaction(assertion, commit, write=True, with_connection=True)
