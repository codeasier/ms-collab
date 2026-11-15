"""Community policy management and authorized scope discovery, not provisioning."""
from dataclasses import replace
from hashlib import sha256
import json
import re

from packages.community_api import CommunityApplication
from packages.community_api.records import read, encode
from packages.workspace_api import WorkspaceApplication
from packages.workspace_api.application import _SEGMENT, _invalid, _object
from packages.workspaces import PolicyError
from .records import GRANTS, MAX_REVISION, templates, binding, compatible, identity


class WorkspaceConnectionsApplication(WorkspaceApplication):
    def __init__(self, *, api, connections=()):
        if type(api) is not WorkspaceApplication:
            raise ValueError('explicit_workspace_boundary_required')
        self._api = api
        self._templates = {(t.scope.workspace_id, t.id): t for t in templates(connections)}

    @staticmethod
    def _eligible(policy, template, identities, principal_id, *, admin=False):
        principal = policy._principals.get(principal_id)
        member = policy._memberships.get((template.scope.workspace_id, principal_id))
        return (principal is not None and principal.active and identity(principal) == identities.get(principal_id)
            and member is not None and member.state == 'active' and (not admin or member.role == 'admin'))

    def _managed(self, policy, actor, connection, template, *, write=False):
        policy._admin(actor, template.scope.workspace_id)
        policy.authorize(actor, template.scope.workspace_id, 'community', 'write' if write else 'read')
        if actor not in template.manager_ids:
            raise PolicyError(404, 'not_found')
        identities = binding(connection, template)
        if not self._eligible(policy, template, identities, actor, admin=True):
            raise PolicyError(404, 'not_found')
        state = read(connection, template.scope)
        if not compatible(state, template):
            raise PolicyError(409, 'connection_binding_changed')
        return state, identities

    def _view(self, policy, template, state, identities):
        people = set().union(*(getattr(template.maximum, key) for key in GRANTS))
        options = []
        for p in sorted(people):
            eligibility = {k: p in getattr(template.maximum, k) and self._eligible(policy, template, identities, p, admin=k != 'reader_ids') for k in GRANTS}
            options.append({'principal_id': p, 'eligible': eligibility})
        return {'id': template.id, 'scope': template.scope.as_dict(), 'revision': state.revision,
            'grants': {k: sorted(getattr(state.policy, k)) for k in GRANTS}, 'options': options,
            'snapshot_available': state.snapshot is not None, 'approved_count': len(state.approvals)}

    def _dispatch(self, environ):
        assertion = self._api._verify_request(environ)
        path = environ.get('PATH_INFO', '')
        parts = path.split('/') if type(path) is str else []
        if (len(parts) not in {6, 7, 8} or parts[:3] != ['', 'api', 'workspaces']
                or parts[4:6] != ['connections', 'community'] or environ.get('QUERY_STRING')
                or any(not _SEGMENT.fullmatch(p) for p in parts[3:])):
            raise _invalid()
        verb = environ.get('REQUEST_METHOD')
        listing = len(parts) == 6 and verb == 'GET'
        managing = len(parts) == 7 and parts[6] == 'manage' and verb == 'GET'
        editing = len(parts) == 8 and parts[6] == 'manage' and verb == 'PATCH'
        if not (listing or managing or editing):
            raise PolicyError(404, 'not_found')
        expected = self._revision(environ) if editing else None
        body = self._grants_body(environ) if editing else None
        workspace = parts[3]
        def operation(policy, actor, connection):
            policy.authorize(actor, workspace, 'community', 'read')
            candidates = [t for (w, _), t in sorted(self._templates.items()) if w == workspace]
            if listing:
                items = []
                for template in candidates:
                    try:
                        state = CommunityApplication._state(policy, actor, connection, template.scope, 'items')
                    except PolicyError:
                        continue
                    # Discovery never widens access. Stale/mismatched platform
                    # templates are omitted, without disclosing their metadata.
                    try:
                        identities = binding(connection, template)
                        if not self._eligible(policy, template, identities, actor) or not compatible(state, template):
                            continue
                    except PolicyError:
                        continue
                    items.append({'id': template.id, 'scope': template.scope.as_dict(), 'revision': state.revision})
                return {'workspace_id': workspace, 'module': 'community', 'items': items}
            policy._admin(actor, workspace)
            if managing:
                items = []
                for template in candidates:
                    if actor not in template.manager_ids:
                        continue
                    state, identities = self._managed(policy, actor, connection, template)
                    items.append(self._view(policy, template, state, identities))
                return {'workspace_id': workspace, 'module': 'community', 'items': items}
            template = self._templates.get((workspace, parts[7]))
            if template is None:
                raise PolicyError(404, 'not_found')
            state, identities = self._managed(policy, actor, connection, template, write=True)
            policy._match(expected, state.revision)
            values = {}
            for key in GRANTS:
                selected = body[key]
                if (type(selected) is not list or len(selected) > 100 or any(type(p) is not str for p in selected)
                        or len(set(selected)) != len(selected) or not set(selected) <= set(getattr(template.maximum, key))
                        or any(not self._eligible(policy, template, identities, p, admin=key != 'reader_ids') for p in selected)):
                    raise PolicyError(400, 'invalid_connection_grants')
                values[key] = tuple(sorted(selected))
            if all(set(values[k]) == set(getattr(state.policy, k)) for k in GRANTS):
                return self._view(policy, template, state, identities)
            if state.revision >= MAX_REVISION:
                raise PolicyError(409, 'connection_revision_exhausted')
            config = replace(state.policy, **values)
            scope = template.scope
            cursor = connection.execute("UPDATE community_connections SET config=?,snapshot=NULL,approvals='[]',revision=revision+1 WHERE workspace_id=? AND connection_id=? AND repository=? AND revision=?",
                (encode(config.as_dict()), scope.workspace_id, scope.connection_id, scope.repository, state.revision))
            if cursor.rowcount != 1:
                raise PolicyError(412, 'revision_conflict')
            policy._changed(actor, workspace, 'resource.update',
                'community-config:' + sha256(encode(scope.as_dict()).encode('ascii')).hexdigest(), state.revision + 1)
            return self._view(policy, template, read(connection, scope), identities)
        return self._api._authenticated_transaction(assertion, operation, write=editing, with_connection=True)

    @staticmethod
    def _grants_body(environ):
        if environ.get('CONTENT_TYPE', '').lower() not in {'application/json', 'application/json; charset=utf-8'}:
            raise PolicyError(415, 'json_required')
        length = environ.get('CONTENT_LENGTH', '')
        if type(length) is not str or not re.fullmatch(r'[0-9]{1,6}', length):
            raise PolicyError(411, 'length_required')
        size = int(length)
        if size > 65536:
            raise PolicyError(413, 'body_too_large')
        try:
            raw = environ['wsgi.input'].read(size)
            if type(raw) is not bytes or len(raw) != size:
                raise ValueError()
            body = json.loads(raw.decode('utf-8'), object_pairs_hook=_object,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if type(body) is not dict or set(body) != set(GRANTS):
                raise ValueError()
        except (KeyError, ValueError, UnicodeError, RecursionError):
            raise _invalid() from None
        return body
