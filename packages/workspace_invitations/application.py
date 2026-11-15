"""Authenticated inbox invitations. IDs are references, NEVER bearer secrets."""
from datetime import datetime, timezone

from packages.workspace_api import WorkspaceApplication
from packages.workspace_api.application import _SEGMENT, _invalid
from packages.workspace_admin_api import WorkspaceAdministrationApplication
from packages.workspace_admin_api.requests import request_key
from packages.workspaces import Membership, PolicyError
from .store import check_schema, records, create, transition, effective_state, view, binding


class WorkspaceInvitationApplication(WorkspaceApplication):
    def __init__(self, *, api, clock=lambda: datetime.now(timezone.utc)):
        if type(api) not in {WorkspaceApplication, WorkspaceAdministrationApplication} or not callable(clock):
            raise ValueError('explicit configured workspace API and clock required')
        self._delegate = api
        self._api = api._api if type(api) is WorkspaceAdministrationApplication else api
        self._clock = clock

    def _now(self):
        value = self._clock()
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('invalid invitation clock')
        # Integer microseconds are unnecessary here, but rounding DOWN would
        # extend strict expiry. Compare full seconds at acceptance/commit.
        return value.timestamp()

    def _dispatch(self, environ):
        path = environ.get('PATH_INFO','')
        if type(path) is not str or len(path) > 1000:
            raise _invalid()
        parts = path.split('/')
        inbox = parts[:3] == ['', 'api', 'invitations']
        admin = parts[:3] == ['', 'api', 'workspaces'] and len(parts) >= 5 and parts[4] == 'invitations'
        if not inbox and not admin:
            return self._delegate._dispatch(environ)
        assertion = self._api._verify_request(environ)
        if environ.get('QUERY_STRING') or any(not _SEGMENT.fullmatch(p) for p in parts[3:]):
            raise _invalid()
        verb = environ.get('REQUEST_METHOD')
        command, body, args, write = None, None, (), False
        if inbox and len(parts) == 3 and verb == 'GET':
            command = 'inbox'
        elif inbox and len(parts) == 4 and verb == 'PATCH':
            command, write, args = 'respond', True, (parts[3], self._revision(environ))
            body = self._body(environ, {'state'})
        elif admin and len(parts) == 5 and verb == 'GET':
            command, args = 'list', (parts[3],)
        elif admin and len(parts) == 5 and verb == 'POST':
            command, write, args = 'create', True, (parts[3], request_key(environ))
            body = self._body(environ, {'principal_id','role'})
            if not _SEGMENT.fullmatch(body['principal_id']):
                raise _invalid()
        elif admin and len(parts) == 6 and verb == 'PATCH':
            command, write, args = 'revoke', True, (parts[3],parts[5],self._revision(environ))
            body = self._body(environ, {'state'})
        if command is None:
            raise PolicyError(404,'not_found')
        deadline = None
        def operation(policy, actor, connection):
            nonlocal deadline
            check_schema(connection)
            now = self._now()
            if command == 'inbox':
                result = [item for item in records(connection,'principal_id=? AND state=?',(actor,'pending'))
                          if effective_state(policy,item,now) == 'pending']
                result.sort(key=lambda item:(item['created'],item['id']))
                return [view(policy,item,now) for item in result[:100]]
            if command == 'respond':
                found = records(connection,'id=? AND principal_id=?',(args[0],actor))
                if not found or binding(policy._principals[actor], policy._realm) != found[0]['binding']:
                    raise PolicyError(404,'not_found')
                item = found[0]
                policy._match(args[1],item['revision'])
                if body['state'] not in {'accepted','declined'}:
                    raise _invalid()
                if effective_state(policy,item,now) != 'pending':
                    raise PolicyError(409,'invitation_unavailable')
                if body['state'] == 'accepted':
                    # Current target, admin revision, lifecycle and prior member
                    # revision were checked in this same locked snapshot.
                    member = Membership(item['workspace_id'],actor,item['role'],
                                        revision=item['member_revision']+1)
                    policy._memberships[(item['workspace_id'],actor)] = member
                    policy._changed(actor,item['workspace_id'],'membership.join',actor,member.revision,policy=True)
                    deadline = item['expires']
                return view(policy,transition(connection,policy,actor,item,body['state']),now)
            workspace = args[0]
            # Existing admins may cancel an outstanding invite even after the
            # space is suspended/archived. This can only reduce future grants.
            member = policy._member(actor,workspace)
            if member.role != 'admin':
                raise PolicyError(403,'forbidden')
            if command == 'create':
                return view(policy,create(connection,policy,actor,workspace,body,args[1],int(now)),now)
            if command == 'list':
                result = records(connection,'workspace_id=?',(workspace,))
                result.sort(key=lambda item:(effective_state(policy,item,now)!='pending',-item['created'],item['id']))
                return [view(policy,item,now) for item in result[:100]]
            found = records(connection,'id=? AND workspace_id=?',(args[1],workspace))
            if not found:
                raise PolicyError(404,'not_found')
            item = found[0]
            policy._match(args[2],item['revision'])
            if body['state'] != 'revoked':
                raise _invalid()
            if item['state'] != 'pending':
                raise PolicyError(409,'invitation_unavailable')
            return view(policy,transition(connection,policy,actor,item,'revoked'),now)
        def before_commit():
            if deadline is not None and self._now() >= deadline:
                raise PolicyError(409,'invitation_unavailable')
        return self._api._authenticated_transaction(assertion,operation,write=write,
                                                    with_connection=True,before_commit=before_commit)
