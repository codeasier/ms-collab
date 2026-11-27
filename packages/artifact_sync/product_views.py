# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Optional local product filters and explicitly listed exact-scope view choices."""
from dataclasses import dataclass
import json
import re
from types import MappingProxyType

from packages.artifacts import ArtifactCatalog, Scope
from packages.artifacts.product_projection import MATCH, project, token, validate_request
from packages.workspace_api.application import _object
from packages.workspaces import PolicyError, StorageError
from .records import MAX_REVISION, decode_snapshot, digest, dump

_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
_HEX = re.compile(r'[0-9a-f]{64}')
_FIELDS = {'format', 'kind', 'match', 'scope', 'view_id', 'product_token', 'parent'}


@dataclass(frozen=True, slots=True, repr=False)
class _View:
    scope: Scope
    view_id: str
    product_token: str
    grant_revision: int
    policy_digest: str
    discovery_label: str | None

    def descriptor(self):
        return {'format': 1, 'kind': 'product-view', 'match': MATCH, 'scope': self.scope.as_dict(),
                'view_id': self.view_id, 'product_token': self.product_token,
                'parent': {'grant_revision': self.grant_revision, 'policy_digest': self.policy_digest}}


def _view(value):
    if (type(value) is not dict or value.keys() not in (_FIELDS, _FIELDS | {'discovery_label'})
            or type(value['format']) is not int or value['format'] != 1
            or type(value['kind']) is not str or value['kind'] != 'product-view'
            or type(value['match']) is not str or value['match'] != MATCH
            or type(value['view_id']) is not str or not _ID.fullmatch(value['view_id'])):
        raise ValueError('invalid product view configuration')
    scope, parent = value['scope'], value['parent']
    if (type(scope) is not dict or scope.keys() != {'workspace_id', 'connection_id', 'bucket_id'}
            or any(type(v) is not str for v in scope.values())
            or type(parent) is not dict or parent.keys() != {'grant_revision', 'policy_digest'}
            or type(parent['grant_revision']) is not int or not 1 <= parent['grant_revision'] <= MAX_REVISION
            or type(parent['policy_digest']) is not str or not _HEX.fullmatch(parent['policy_digest'])):
        raise ValueError('invalid product view binding')
    label = value.get('discovery_label')
    if ('discovery_label' in value and (type(label) is not str or not 1 <= len(label) <= 80
            or label != label.strip() or not label.isprintable())):
        raise ValueError('invalid product view discovery label')
    return _View(Scope(**scope), value['view_id'], token(value['product_token']),
                 parent['grant_revision'], parent['policy_digest'], label)


@dataclass(frozen=True, slots=True, init=False, repr=False, eq=False)
class ProductViewRegistry:
    """Detached immutable configuration; a complete replacement requires a new factory."""
    _views: MappingProxyType

    def __init__(self, values=None):
        if values is None:
            values = ()
        if type(values) not in (list, tuple) or len(values) > 128:
            raise ValueError('bounded product view configuration required')
        views = {}
        for value in values:
            view = _view(value)
            identity = (view.scope, view.view_id)
            if identity in views:
                raise ValueError('duplicate product view configuration')
            views[identity] = view
        object.__setattr__(self, '_views', MappingProxyType(views))

    def __bool__(self):
        return bool(self._views)

    def lookup(self, scope, view_id):
        try:
            return self._views[(scope, view_id)]
        except KeyError:
            raise PolicyError(404, 'not_found') from None


    def choices(self, scope, record):
        policy_digest = digest(record.policy_raw)
        return [{'view_id': view.view_id, 'label': view.discovery_label}
                for view in sorted(self._views.values(), key=lambda view: view.view_id)
                if view.scope == scope and view.discovery_label is not None
                and view.grant_revision == record.grant_revision and view.policy_digest == policy_digest]


def product_route(environ):
    """Recognize only these mounted routes, never overtake any legacy method."""
    path = environ.get('PATH_INFO', '')
    if type(path) is not str or len(path) > 1000:
        return None
    parts = path.split('/')
    if (len(parts) not in (8, 9) or parts[:3] != ['', 'api', 'workspaces']
            or parts[4] != 'artifacts' or parts[7] != 'product-views'):
        return None
    if (environ.get('REQUEST_METHOD') != ('GET' if len(parts) == 8 else 'POST')
            or any(not _ID.fullmatch(parts[i]) for i in (3, 5, 6))
            or (len(parts) == 9 and not _ID.fullmatch(parts[8]))):
        raise PolicyError(404, 'not_found')
    if environ.get('QUERY_STRING', '') != '':
        raise PolicyError(400, 'invalid_request')
    return Scope(parts[3], parts[5], parts[6]), parts[8] if len(parts) == 9 else None


def framed_body(environ):
    """Bound transport reads outside the DB transaction; do not decode query or view."""
    media = environ.get('CONTENT_TYPE')
    if type(media) is not str or media.lower() not in ('application/json', 'application/json; charset=utf-8'):
        raise PolicyError(415, 'json_required')
    length = environ.get('CONTENT_LENGTH')
    if type(length) is not str or not re.fullmatch(r'[0-9]{1,6}', length):
        raise PolicyError(411, 'length_required')
    size = int(length)
    if size > 16384:
        raise PolicyError(413, 'body_too_large')
    try:
        raw = environ['wsgi.input'].read(size)
    except (AttributeError, KeyError, OSError, ValueError):
        raise PolicyError(400, 'invalid_request') from None
    if type(raw) is not bytes or len(raw) != size:
        raise PolicyError(400, 'invalid_request')
    return raw


def _bad_constant(_):
    raise ValueError('non-finite product query')


def product_choices(registry, scope, record):
    """Return only explicitly listed choices from one authorized publication."""
    if record.published_raw is None:
        raise PolicyError(404, 'not_found')
    views = registry.choices(scope, record)
    quarters = []
    if views:
        snapshot = decode_snapshot(record.published, record.policy.connection)
        quarters = list(ArtifactCatalog((snapshot,)).quarters(scope))
    return {'format': 1, 'kind': 'product-view-choices', 'scope': scope.as_dict(),
            'catalog_revision': record.published_revision, 'views': views, 'quarters': quarters}


def bound_view(registry, scope, view_id, record):
    """Called only inside a current, exact authorized index read transaction."""
    if record.published_raw is None:
        raise PolicyError(404, 'not_found')
    view = registry.lookup(scope, view_id)
    if (view.grant_revision != record.grant_revision or view.policy_digest != digest(record.policy_raw)):
        raise PolicyError(404, 'not_found')
    return view


def product_result(registry, scope, view_id, raw, record):
    view = bound_view(registry, scope, view_id, record)
    try:
        body = json.loads(raw.decode('utf-8'), object_pairs_hook=_object, parse_constant=_bad_constant)
        query = validate_request(body)
    except (KeyError, ValueError, TypeError, OverflowError, RecursionError):
        raise PolicyError(400, 'invalid_request') from None

    # Exact publication bytes, not private candidate or mutable index revision.
    # This is only an equality hint after current authority; not an access token.
    observed = digest(dump(['product-observation-v1', view.descriptor(), record.published_revision,
                            digest(record.published_raw)]))
    if query.observation is not None and query.observation != observed:
        raise PolicyError(412, 'stale_observation')
    try:
        snapshot = decode_snapshot(record.published, record.policy.connection)
        # The public pure entry validates independently for non-HTTP callers.
        result = project(snapshot, view.product_token, body)
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError):
        raise StorageError('invalid_artifact_index') from None
    result.update(format=1, kind='product-view', match=MATCH, view_id=view_id,
                  catalog_revision=record.published_revision, observation=observed,
                  source={'kind': 'stored_index', 'full_history_complete': False,
                          'live_provider_verified': False, 'download_available': False})
    return result
