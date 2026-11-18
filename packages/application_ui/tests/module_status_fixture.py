"""Fixed synthetic Overview fixtures, never a product API or enrollment path.

Only the opt-in temporary HTTPS browser server calls these helpers. Every target
is fixed here; requests cannot supply a workspace, principal, module or query.
"""
from dataclasses import replace
from urllib.parse import urlsplit

from packages.workspaces import Membership, ModuleInstance, PolicyError


MODE = 'module-status'
LONG_MODULE_KEY = 'm' * 200
_PREFIX = '/synthetic/module-status/'


def provision(store):
    """Seed one disposable store before serving, using bound membership grants."""
    def seed(policy):
        if policy._memberships.get(('alpha', 'reader')) != Membership('alpha', 'reader', 'viewer'):
            raise ValueError('unexpected synthetic reader membership')
        # set_membership edits existing records; it cannot enroll these absent
        # members. This trusted test-only writer captures their current owners.
        policy._grant_membership(Membership('beta', 'reader', 'developer'))
        policy._grant_membership(Membership('commons', 'reader', 'admin'))
        policy.set_module_state('second', 'alpha', 'pipelines', state='disabling', expected_revision=1)
        policy.set_module_state('second', 'alpha', 'pipelines', state='disabled', expected_revision=2)
        policy.set_module_state('second', 'beta', 'pipelines', state='disabling', expected_revision=1)
        policy.install_module('second', 'commons', LONG_MODULE_KEY, capabilities=())
    store._transaction(seed, write=True)


def _empty_capabilities(store):
    def change(policy):
        old = policy._modules[('commons', 'pipelines')]
        if old != ModuleInstance('commons', 'pipelines', ('read', 'write')):
            raise PolicyError(409, 'synthetic_fixture_conflict')
        # There is no product capability editor. Like the fixed SQLite seeds in
        # the existing host tests, this trusted fixture changes only capabilities
        # and preserves the stored lifecycle/revision and separate long-key row.
        policy._modules[('commons', 'pipelines')] = replace(old, capabilities=())
    store._transaction(change, write=True)


def _suspend_alpha(store):
    store.set_workspace_state('admin', 'alpha', state='suspended', expected_revision=1)


def _archive_beta(store):
    store.set_workspace_state('admin', 'beta', state='archived', expected_revision=1)


def _revoke_beta_reader(store):
    def change(policy):
        # Keep archive-before-revoke explicit; do not silently restore history
        # or let this control remove the prerequisite for the archive assertion.
        workspace = policy._workspaces['beta']
        if (workspace.state, workspace.revision) != ('archived', 2):
            raise PolicyError(409, 'synthetic_fixture_conflict')
        policy.set_membership('second', 'beta', 'reader', role='developer',
                              state='revoked', expected_revision=1)
    store._transaction(change, write=True)


_CONTROLS = {
    _PREFIX + 'empty-capabilities-commons': _empty_capabilities,
    _PREFIX + 'suspend-alpha': _suspend_alpha,
    _PREFIX + 'archive-beta': _archive_beta,
    _PREFIX + 'revoke-beta-reader': _revoke_beta_reader,
}


def dispatch(store, *, mode, origin, environ, start_response):
    """Return None for non-fixture traffic so real host/session checks run.

    These controls deliberately accept only an empty, same-origin POST. They never
    read a request-supplied identity, scope, command, JSON object or SQL statement.
    """
    path = environ.get('PATH_INFO', '')
    if mode != MODE or type(path) is not str or not path.startswith(_PREFIX):
        return None

    def respond(status):
        start_response(status, [('Content-Type', 'text/plain; charset=utf-8'),
            ('Content-Length', '0'), ('Cache-Control', 'no-store')])
        return [b'']

    if (environ.get('REQUEST_METHOD') != 'POST'
            or environ.get('HTTP_ORIGIN') != origin
            or environ.get('wsgi.url_scheme') != 'https'
            or environ.get('HTTP_HOST') != urlsplit(origin).netloc):
        return respond('403 Forbidden')
    if path not in _CONTROLS:
        return respond('404 Not Found')
    if (environ.get('QUERY_STRING', '') != ''
            or environ.get('SCRIPT_NAME', '') != ''
            or environ.get('CONTENT_LENGTH', '') not in ('', '0')
            # wsgiref supplies text/plain even when the HTTP header is absent.
            or environ.get('CONTENT_TYPE', '') not in ('', 'text/plain')
            or 'HTTP_TRANSFER_ENCODING' in environ
            or any(name in environ for name in (
                'HTTP_AUTHORIZATION', 'HTTP_X_WORKSPACE_ID', 'HTTP_X_PRINCIPAL_ID',
                'HTTP_X_USER_ID', 'HTTP_X_SUBJECT', 'REMOTE_USER'))):
        return respond('400 Bad Request')
    try:
        _CONTROLS[path](store)
    except PolicyError:
        return respond('409 Conflict')
    return respond('200 OK')
