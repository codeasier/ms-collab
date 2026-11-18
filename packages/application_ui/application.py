"""Static product entry composed only around the configured cookie/session host."""
import json
from pathlib import Path

from packages.application_host import ApplicationHost
from packages.identity import AuthenticationError

_ROOT = Path(__file__).resolve().parents[2] / 'frontend'
_FILES = {
    '/connections/': ('application/connections.html', 'text/html'),
    '/app/connections.mjs': ('application/connections.mjs', 'text/javascript'),
    '/app/connections.css': ('application/connections.css', 'text/css'),
    '/gui-smoke/': ('application/gui-smoke.html', 'text/html'),
    '/gui-smoke/view.mjs': ('gui-smoke/view.mjs', 'text/javascript'),
    '/gui-smoke/client.mjs': ('gui-smoke/client.mjs', 'text/javascript'),
    '/gui-smoke/style.css': ('gui-smoke/style.css', 'text/css'),
    '/app/gui-smoke-session.mjs': ('application/gui-smoke-session.mjs', 'text/javascript'),
    '/research-assets/': ('application/research-assets.html', 'text/html'),
    '/research-assets/browser.mjs': ('research-assets/browser.mjs', 'text/javascript'),
    '/research-assets/client.mjs': ('research-assets/client.mjs', 'text/javascript'),
    '/research-assets/style.css': ('research-assets/style.css', 'text/css'),
    '/assistant/': ('application/assistant.html', 'text/html'),
    '/assistant/history.mjs': ('assistant/history.mjs', 'text/javascript'),
    '/assistant/client.mjs': ('assistant/client.mjs', 'text/javascript'),
    '/assistant/style.css': ('assistant/style.css', 'text/css'),
    '/app/pipeline-scopes.mjs': ('application/pipeline-scopes.mjs', 'text/javascript'),
    '/app/pipeline-scope-client.mjs': ('application/pipeline-scope-client.mjs', 'text/javascript'),
    '/pipelines/': ('application/pipelines.html', 'text/html'),
    '/pipelines/runs.mjs': ('pipelines/runs.mjs', 'text/javascript'),
    '/pipelines/client.mjs': ('pipelines/client.mjs', 'text/javascript'),
    '/pipelines/style.css': ('pipelines/style.css', 'text/css'),
    '/': ('application/index.html', 'text/html'),
    '/manage/': ('application/manage.html', 'text/html'),
    '/community/': ('application/community.html', 'text/html'),
    '/community/board.mjs': ('community/board.mjs', 'text/javascript'),
    '/community/client.mjs': ('community/client.mjs', 'text/javascript'),
    '/community/style.css': ('community/style.css', 'text/css'),
    '/app/community-client.mjs': ('application/community-client.mjs', 'text/javascript'),
    '/responsibility/': ('application/responsibility.html', 'text/html'),
    '/artifacts/': ('application/artifacts.html', 'text/html'),
    '/artifact-admin/': ('application/artifact-admin.html', 'text/html'),
    '/app/artifact-download-ui.mjs': ('application/artifact-download-ui.mjs', 'text/javascript'),
    '/app/artifact-downloads.mjs': ('application/artifact-downloads.mjs', 'text/javascript'),
    '/app/artifact-scopes.mjs': ('application/artifact-scopes.mjs', 'text/javascript'),
    '/app/artifact-scope-client.mjs': ('application/artifact-scope-client.mjs', 'text/javascript'),
    '/app/artifacts-session.mjs': ('application/artifacts-session.mjs', 'text/javascript'),
    '/app/artifacts.css': ('application/artifacts.css', 'text/css'),
    '/app/shell.mjs': ('application/shell.mjs', 'text/javascript'),
    '/app/client.mjs': ('application/client.mjs', 'text/javascript'),
    '/app/module-status.mjs': ('application/module-status.mjs', 'text/javascript'),
    '/app/style.css': ('application/style.css', 'text/css'),
    '/manage/admin-transfer.mjs': ('workspace-admin/admin-transfer.mjs', 'text/javascript'),
    '/manage/admin.mjs': ('workspace-admin/admin.mjs', 'text/javascript'),
    '/manage/client.mjs': ('workspace-admin/client.mjs', 'text/javascript'),
    '/manage/style.css': ('workspace-admin/style.css', 'text/css'),
    '/responsibility/browser.mjs': ('responsibility/browser.mjs', 'text/javascript'),
    '/responsibility/client.mjs': ('responsibility/client.mjs', 'text/javascript'),
    '/responsibility/style.css': ('responsibility/style.css', 'text/css'),
    '/artifacts/browser.mjs': ('artifacts/browser.mjs', 'text/javascript'),
    '/artifacts/client.mjs': ('artifacts/client.mjs', 'text/javascript'),
    '/artifacts/style.css': ('artifacts/style.css', 'text/css'),
    '/artifact-admin/browser.mjs': ('artifact-admin/browser.mjs', 'text/javascript'),
    '/artifact-admin/client.mjs': ('artifact-admin/client.mjs', 'text/javascript'),
    '/artifact-admin/style.css': ('artifact-admin/style.css', 'text/css'),
}

# Public, inert assets for a configured indexed host only. Never derive a
# filename from a requested path, product ID, bucket, or configuration value.
_PRODUCT_DOCUMENT = '/artifacts/product-views/'
_PRODUCT_FILES = {
    _PRODUCT_DOCUMENT: ('application/artifact-product-view.html', 'text/html'),
    '/app/artifact-product-view.mjs': ('application/artifact-product-view.mjs', 'text/javascript'),
    '/app/artifact-product-codec.mjs': ('application/artifact-product-codec.mjs', 'text/javascript'),
}


class ApplicationUI:
    """Explicit static wrapper, not a listener, provider, verifier or deployment.

    The exact existing ApplicationHost owns every API/auth request. Static HTML
    contains no identity, scopes, credentials or cached data. No bearer adapters,
    synthetic fixtures, cookie copying or session-to-token conversion are used.
    The trusted host's own transport check protects static/bootstrap requests too.
    """
    def __init__(self, *, host, product_file_downloads=False):
        if type(host) is not ApplicationHost:
            raise ValueError('explicit configured ApplicationHost required')
        if type(product_file_downloads) is not bool:
            raise ValueError('product_file_downloads must be a Boolean')
        if product_file_downloads and not (host.artifact_product_views_enabled
                and host._config.artifact_downloads and host._artifact_downloads is not None):
            raise ValueError('product file checks require configured product views and byte downloads')
        self._host = host
        self._product_file_downloads = product_file_downloads

    def __call__(self, environ, start_response):
        path = environ.get('PATH_INFO', '')
        if type(path) is str and (path.startswith('/api/') or path.startswith('/auth/')):
            return self._host(environ, start_response)
        product_views = self._host.artifact_product_views_enabled
        headers = [('Cache-Control', 'no-store'), ('Pragma', 'no-cache'),
                   ('Referrer-Policy', 'no-referrer'), ('X-Content-Type-Options', 'nosniff'),
                   ('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"),
                   ('Vary', 'Cookie, Origin')]
        def respond(status, body, media='text/plain'):
            start_response(status, headers + [('Content-Type', media+'; charset=utf-8'), ('Content-Length', str(len(body)))])
            return [b'' if environ.get('REQUEST_METHOD') == 'HEAD' else body]
        try:
            # A provider callback's 303 navigation retains cross-site Fetch
            # Metadata. Only fixed, public top-level HTML may be that landing
            # page; no API, bootstrap, script, query or embedded document gets
            # this exception. HTML contains no identity or private scope data.
            navigation = (type(path) is str and (path in {'/', '/manage/', '/connections/', '/responsibility/', '/pipelines/', '/community/', '/artifacts/', '/artifact-admin/', '/assistant/', '/research-assets/', '/gui-smoke/'}
                    or (product_views and path == _PRODUCT_DOCUMENT))
                and environ.get('REQUEST_METHOD') == 'GET'
                and not environ.get('QUERY_STRING')
                and environ.get('HTTP_SEC_FETCH_MODE') == 'navigate'
                and environ.get('HTTP_SEC_FETCH_DEST') == 'document'
                and environ.get('HTTP_ORIGIN') in {None, self._host._config.origin})
            self._host._boundary._transport_security(environ, callback=navigation)
        except AuthenticationError:
            return respond('401 Unauthorized', b'authentication_required')
        if environ.get('REQUEST_METHOD') not in {'GET', 'HEAD'} or environ.get('QUERY_STRING'):
            return respond('404 Not Found', b'not_found')
        enabled = 'responsibility' in self._host._config.modules
        pipelines = 'pipelines' in self._host._config.modules
        artifact_pages = (['artifacts'] if 'artifacts' in self._host._config.modules else []) + (['artifact-admin'] if self._host._config.artifact_administration else [])
        if product_views:
            artifact_pages.append('artifact-product-views')
        if path == '/app/bootstrap':
            endpoint = self._host._boundary._authorization_endpoint
            body = {'data': {'service': 'ms-collab',
                'ui_modules': ['workspaces'] + [name for name in ('responsibility', 'pipelines', 'community', 'assistant', 'research-assets', 'gui-smoke') if name in self._host._config.modules] + artifact_pages + (['connections'] if self._host._config.community_connections else []) + (['artifact-downloads'] if self._host._config.artifact_downloads else []),
                'login_configured': endpoint is not None, 'authorization_endpoint': endpoint}}
            if self._product_file_downloads:
                # Public UI support only. No file, binding or byte authority.
                body['data']['ui_modules'].append('artifact-product-file-downloads')
            return respond('200 OK', json.dumps(body, ensure_ascii=True, separators=(',', ':')).encode('ascii'), 'application/json')
        if path == '/connections/' and not self._host._config.community_connections:
            return respond('404 Not Found', b'not_found')
        if type(path) is str and path in {'/artifacts/', '/artifact-admin/'} and path.strip('/') not in artifact_pages:
            return respond('404 Not Found', b'not_found')
        files = _PRODUCT_FILES if product_views and type(path) is str and path in _PRODUCT_FILES else _FILES
        if type(path) is not str or path not in files or (path == '/responsibility/' and not enabled) or (path == '/pipelines/' and not pipelines) or (path == '/assistant/' and 'assistant' not in self._host._config.modules) or (path == '/community/' and 'community' not in self._host._config.modules) or (path == '/research-assets/' and 'research-assets' not in self._host._config.modules) or (path == '/gui-smoke/' and 'gui-smoke' not in self._host._config.modules):
            return respond('404 Not Found', b'not_found')
        filename, media = files[path]
        return respond('200 OK', (_ROOT / filename).read_bytes(), media)
