"""Invitation records in the SAME policy transaction. No invitation secrets."""
from hashlib import sha256
import json
import re
from uuid import uuid4

from packages.workspaces import PolicyError, SQLiteWorkspaceService, StorageError
from packages.workspaces.model import MAX_REVISION, revision

_TABLE = 'workspace_invitations_v1'
_CREATE = f"""CREATE TABLE {_TABLE} (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    inviter TEXT NOT NULL REFERENCES principals(id),
    principal_id TEXT NOT NULL REFERENCES principals(id),
    binding TEXT NOT NULL, inviter_binding TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('viewer','developer')),
    state TEXT NOT NULL CHECK(state IN ('pending','accepted','revoked','declined')),
    created INTEGER NOT NULL, expires INTEGER NOT NULL CHECK(expires>created),
    revision INTEGER NOT NULL CHECK(revision>=1),
    workspace_revision INTEGER NOT NULL CHECK(workspace_revision>=1),
    inviter_revision INTEGER NOT NULL CHECK(inviter_revision>=1),
    member_revision INTEGER NOT NULL CHECK(member_revision>=0),
    request_key TEXT NOT NULL,
    UNIQUE(workspace_id,inviter,request_key)) STRICT"""
_FIELDS = ('id','workspace_id','inviter','principal_id','binding','inviter_binding','role','state','created','expires',
           'revision','workspace_revision','inviter_revision','member_revision','request_key')


def check_schema(connection):
    row = connection.execute('SELECT type,sql FROM sqlite_schema WHERE name=?', (_TABLE,)).fetchone()
    normalize = lambda sql: re.sub(r'\s+', ' ', sql).strip()
    if row is None or row[0] != 'table' or type(row[1]) is not str or normalize(row[1]) != normalize(_CREATE):
        raise StorageError('invalid_invitation_schema')


def initialize_invitations(store):
    """Trusted additive setup only. No implicit HTTP migration or directory changes."""
    if type(store) is not SQLiteWorkspaceService:
        raise ValueError('initialized policy store required')
    def initialize(policy, connection):
        connection.execute(_CREATE.replace('CREATE TABLE', 'CREATE TABLE IF NOT EXISTS', 1))
        check_schema(connection)
    store._transaction(initialize, write=True, with_connection=True)


def binding(principal, realm):
    return sha256(json.dumps(["principal-authority-v2", realm, principal.id, principal.issuer, principal.subject, principal.generation],
                            ensure_ascii=True, separators=(',', ':')).encode('ascii')).hexdigest()


def records(connection, where, args):
    # where is fixed trusted code, never caller SQL.
    result = [dict(zip(_FIELDS, row)) for row in connection.execute(
        f'SELECT {",".join(_FIELDS)} FROM {_TABLE} WHERE {where}', args).fetchall()]
    # Existing schema stays compatible; malformed/oversized legacy counters are
    # never echoed, rounded, clamped or silently adopted as current authority.
    try:
        for item in result:
            for key in ('revision', 'workspace_revision', 'inviter_revision'):
                revision(item[key])
            value = item['member_revision']  # Zero means no previous membership.
            if type(value) is not int or not 0 <= value <= MAX_REVISION:
                raise ValueError()
    except ValueError:
        raise StorageError('invalid_invitation_record') from None
    return result


def effective_state(policy, item, now):
    if item['state'] != 'pending':
        return item['state']
    if now >= item['expires']:
        return 'expired'
    space = policy._workspaces.get(item['workspace_id'])
    issuer = policy._principals.get(item['inviter'])
    target = policy._principals.get(item['principal_id'])
    admin = policy._memberships.get((item['workspace_id'], item['inviter']))
    member = policy._memberships.get((item['workspace_id'], item['principal_id']))
    if (space is None or space.state != 'active' or space.revision != item['workspace_revision']
            or issuer is None or not issuer.active or binding(issuer, policy._realm) != item['inviter_binding'] or target is None or not target.active
            or binding(target, policy._realm) != item['binding'] or admin is None or admin.role != 'admin'
            or not policy._membership_current(item['inviter'], item['workspace_id'])
            or admin.revision != item['inviter_revision']
            or (member.revision if member else 0) != item['member_revision']
            or (member is not None and (member.state != 'revoked'
                or not policy._membership_authority_current(item['principal_id'], item['workspace_id'])))):
        return 'invalidated'
    return 'pending'


def view(policy, item, now):
    # No binding, external issuer/subject, request key or reusable authority.
    space = policy._workspaces[item['workspace_id']]
    return {key:item[key] for key in ('id','workspace_id','principal_id','role','created','expires','revision')} | {
        'workspace_name':space.name, 'state':effective_state(policy,item,now)}


def create(connection, policy, actor, workspace, body, key, now):
    admin = policy._admin(actor, workspace)
    prior = records(connection, 'workspace_id=? AND inviter=? AND request_key=?', (workspace, actor, key))
    if prior:
        item = prior[0]
        if (item['principal_id'], item['role']) != (body['principal_id'], body['role']):
            raise PolicyError(409, 'idempotency_conflict')
        return item  # Replaying never extends expiry or reactivates authority.
    principal = policy._principals.get(body['principal_id'])
    if principal is None or not principal.active:
        raise PolicyError(400, 'invalid_invitee')
    if body['role'] not in {'viewer','developer'}:
        raise PolicyError(400, 'invalid_invitation_role')
    member = policy._memberships.get((workspace, principal.id))
    if member is not None and member.state == 'active':
        raise PolicyError(409, 'already_member')
    if member is not None and not policy._membership_authority_current(principal.id, workspace):
        raise PolicyError(409, 'membership_authority_unavailable')
    pending = [item for item in records(connection, 'workspace_id=? AND state=?', (workspace,'pending'))
               if effective_state(policy,item,now) == 'pending']
    if any(item['principal_id'] == principal.id for item in pending):
        raise PolicyError(409, 'already_invited')
    if len(pending) >= 100:
        raise PolicyError(409, 'invitation_limit')
    item = dict(zip(_FIELDS, (str(uuid4()),workspace,actor,principal.id,binding(principal, policy._realm),binding(policy._principals[actor], policy._realm),body['role'],
                             'pending',now,now+86400,1,policy._workspaces[workspace].revision,
                             admin.revision,member.revision if member else 0,key)))
    policy._check_change()
    connection.execute(f'INSERT INTO {_TABLE} VALUES ({",".join("?" for _ in _FIELDS)})', tuple(item.values()))
    policy._changed(actor,workspace,'invitation.create',item['id'],1)
    return item


def transition(connection, policy, actor, item, state):
    policy._check_change(item['revision'])
    connection.execute(f'UPDATE {_TABLE} SET state=?,revision=revision+1 WHERE id=?', (state,item['id']))
    item = item | {'state':state,'revision':item['revision']+1}
    policy._changed(actor,item['workspace_id'],'invitation.'+state,item['id'],item['revision'])
    return item
