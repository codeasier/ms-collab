# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Named, exact-quarter file filters; never resources, file lists or grants."""
import json

from packages.artifacts import Scope
from packages.artifacts.catalog import quarter
from packages.artifacts.product_projection import validate_request
from packages.workspace_api.application import _object
from packages.workspaces import PolicyError, SQLiteWorkspaceService, StorageError
from .product_views import _ID, bound_view, product_result
from .records import digest, dump, scope_key

_TABLE = 'artifact_product_presets_v1'
_CREATE = f"""CREATE TABLE {_TABLE} (
workspace TEXT NOT NULL, connection TEXT NOT NULL, bucket TEXT NOT NULL,
view TEXT NOT NULL, binding TEXT NOT NULL, quarter TEXT NOT NULL,
preset TEXT NOT NULL, created_by TEXT NOT NULL, query TEXT NOT NULL CHECK(length(query) <= 16384),
PRIMARY KEY(workspace,connection,bucket,view,binding,quarter,preset))"""
_FIELDS = {'format', 'quarter', 'mode', 'path', 'keyword', 'recursive', 'start_time', 'end_time'}


def _schema(db):
    rows = db.execute("SELECT type,sql FROM sqlite_schema WHERE tbl_name=? "
                      "AND NOT(type='index' AND sql IS NULL)", (_TABLE,)).fetchall()
    if len(rows) != 1 or rows[0][0] != 'table' or rows[0][1] != _CREATE:
        raise StorageError('invalid_product_preset_schema')


def initialize_product_presets(store):
    """Explicit trusted setup. Never creates or repairs tables on request."""
    if type(store) is not SQLiteWorkspaceService:
        raise ValueError('initialized policy store required')
    def setup(_, db):
        db.execute(_CREATE.replace('CREATE TABLE', 'CREATE TABLE IF NOT EXISTS', 1))
        _schema(db)
    store._transaction(setup, write=True, with_connection=True)


def preset_route(env):
    path = env.get('PATH_INFO')
    if type(path) is not str or len(path) > 1000:
        return None
    parts = path.split('/')
    if (len(parts) != 13 or parts[:3] != ['', 'api', 'workspaces'] or parts[4] != 'artifacts'
            or parts[7] != 'product-views' or parts[9] != 'quarters' or parts[11] != 'presets'):
        return None
    if (env.get('REQUEST_METHOD') not in ('GET', 'POST')
            or any(not _ID.fullmatch(parts[i]) for i in (3, 5, 6, 8, 12))):
        raise PolicyError(404, 'not_found')
    try:
        selected = quarter(parts[10])
    except ValueError:
        raise PolicyError(404, 'not_found') from None
    if env.get('QUERY_STRING', '') != '':
        raise PolicyError(400, 'invalid_request')
    return Scope(parts[3], parts[5], parts[6]), parts[8], selected, parts[12]


def _query(raw, selected, *, saving):
    body = json.loads(raw, object_pairs_hook=_object)
    validate_request(body)
    allowed = _FIELDS | ({'if_observation'} if saving else set())
    if (body.keys() - allowed or body['quarter'] != selected or body.get('mode') != 'files'
            or (saving and 'if_observation' not in body)):
        raise ValueError('expected active product file query')
    return body


def serve(registry, policy, actor, db, record, route, raw):
    scope, view_id, selected, name = route
    # Reading the publication already passed authorized(..., 'read') in this transaction.
    # Writing additionally requires CURRENT module write AND workspace administration;
    # neither an index approver nor the platform operator substitutes for these.
    if raw is not None:
        policy.authorize(actor, scope.workspace_id, 'artifacts', 'write')
        policy._admin(actor, scope.workspace_id)
    view = bound_view(registry, scope, view_id, record)
    binding = digest(dump(view.descriptor()))
    coords = (*scope_key(scope), view_id, binding, selected, name)
    _schema(db)
    column = '1' if raw is not None else 'query'
    row = db.execute(f'SELECT {column} FROM {_TABLE} WHERE workspace=? AND connection=? '
                     'AND bucket=? AND view=? AND binding=? AND quarter=? AND preset=?', coords).fetchone()
    if raw is not None:
        try:
            query = _query(raw.decode('utf-8'), selected, saving=True)
        except (TypeError, ValueError, KeyError, OverflowError, RecursionError):
            raise PolicyError(400, 'invalid_request') from None
        # Compare against this view and current publication before storing; the
        # observation does not prove a previous user action or confer any access.
        data = product_result(registry, scope, view_id, raw, record)
        if not data['quarter_available']:
            raise PolicyError(404, 'not_found')
        if row is not None:
            raise PolicyError(409, 'preset_exists')
        count = db.execute(f'SELECT count(*) FROM {_TABLE} WHERE workspace=? AND connection=? AND bucket=?',
                           scope_key(scope)).fetchone()[0]
        if count >= 128:
            raise PolicyError(409, 'preset_limit')
        del query['if_observation']
        # Immutable creation provenance stays inside the private preset record. Never
        # put a query, preset name, view token, or file key in workspace-wide resources.
        db.execute(f'INSERT INTO {_TABLE} VALUES (?,?,?,?,?,?,?,?,?)', (*coords, actor, dump(query)))
    else:
        if row is None:
            raise PolicyError(404, 'not_found')
        try:
            if type(row[0]) is not str or len(row[0]) > 16384:
                raise ValueError('invalid preset')
            query = _query(row[0], selected, saving=False)
        except (TypeError, ValueError, KeyError, OverflowError, RecursionError):
            raise StorageError('invalid_product_preset') from None
        data = product_result(registry, scope, view_id, dump(query).encode('ascii'), record)
        if not data['quarter_available']:
            raise PolicyError(404, 'not_found')
    return {'format': 1, 'kind': 'product-preset', 'preset_id': name, 'query': query, 'result': data}
