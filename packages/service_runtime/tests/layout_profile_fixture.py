"""TEST-ONLY synthetic storage, not HTTP authorization or a transfer writer.

Every saved receipt uses an independently assembled historical vector and an
actual trusted core transfer's two audit events. Later role/identity changes
belong in the calling test, never in a helper that silently repairs provenance.
"""
from contextlib import closing
from copy import deepcopy
from hashlib import sha256
import json
import sqlite3
from urllib.parse import quote
from uuid import UUID

from packages.workspaces import Membership, Principal, SQLiteWorkspaceService, Workspace
from packages.service_runtime.schema import layout_profile

FIELDS = ('actor', 'workspace_id', 'request_key', 'replacement_id', 'intent',
          'request_digest', 'provenance', 'actor_revision',
          'replacement_revision', 'audit_first', 'audit_last')
TEXT_LIMITS = (800, 200, 36, 200, 4096, 64, 32768)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(',', ':')).encode('ascii')


def key(number):
    return str(UUID(int=number, version=4))


def vector(*, actor='admin', replacement='member', workspace='alpha',
           realm='0' * 32, issuer='synthetic', actor_subject='admin',
           replacement_subject='member', actor_revision=1,
           replacement_revision=1, generation=1, authority_epoch=1,
           workspace_revision=1, replacement_role='developer'):
    """Independent codec fixture, with no call to a production encoder."""
    def selected(ident, subject, role, revision):
        pin = {'issuer': issuer, 'subject': subject, 'generation': generation}
        return {'id': ident, **pin, 'authority_epoch': authority_epoch,
                'membership': {'role': role, 'state': 'active',
                    'revision': revision, 'authority': {'version': 1,
                        'realm': realm, 'principals': {ident: dict(pin)}}}}
    return {'version': 1, 'action': 'admin.transfer',
            'route': '/api/workspaces/' + quote(workspace, safe='') + '/admin-transfers',
            'realm': realm,
            'workspace': {'id': workspace, 'state': 'active', 'revision': workspace_revision},
            'actor': selected(actor, actor_subject, 'admin', actor_revision),
            'replacement': selected(replacement, replacement_subject,
                                    replacement_role, replacement_revision)}


def record(review, *, number=1, audit_first=1):
    """Build persisted columns directly from the reviewed contract, not codec."""
    intent = {'version': 1, 'action': 'admin.transfer', 'actor': review['actor']['id'],
              'workspace': review['workspace']['id'],
              'replacement': review['replacement']['id'],
              'actor_revision': review['actor']['membership']['revision'],
              'replacement_revision': review['replacement']['membership']['revision'],
              'precondition': 'ats1-' + sha256(canonical(review)).hexdigest()}
    return {'actor': intent['actor'], 'workspace_id': intent['workspace'],
            'request_key': key(number), 'replacement_id': intent['replacement'],
            'intent': canonical(intent).decode('ascii'),
            'request_digest': sha256(canonical(intent)).hexdigest(),
            'provenance': canonical(review).decode('ascii'),
            'actor_revision': intent['actor_revision'],
            'replacement_revision': intent['replacement_revision'],
            'audit_first': audit_first, 'audit_last': audit_first + 1}


def projected(values):
    """Independent model of BLOB prefixes, including one overflow sentinel."""
    result = []
    for index, name in enumerate(FIELDS):
        value = values[name]
        if index < len(TEXT_LIMITS):
            if isinstance(value, str):
                value = value.encode('utf-8')
            if isinstance(value, bytes):
                value = value[:TEXT_LIMITS[index] + 1]
        result.append(value)
    return tuple(result)


def create_policy(path, *, workspace_ids=('alpha',), actor='admin',
                  replacement='member', issuer='synthetic',
                  actor_subject='admin', replacement_subject='member'):
    """Trusted fresh synthetic bootstrap with the exact I2 module additions."""
    store = SQLiteWorkspaceService.initialize(path,
        principals=(Principal(actor, issuer, actor_subject),
                    Principal(replacement, issuer, replacement_subject)),
        workspaces=tuple(Workspace(w, 'Synthetic ' + w) for w in workspace_ids),
        memberships=tuple(m for w in workspace_ids for m in (
            Membership(w, actor, 'admin'), Membership(w, replacement, 'developer'))),
        modules=())
    with closing(sqlite3.connect(path)) as db:
        for statement in layout_profile(2).module_schema:
            db.execute(statement)
        db.commit()
    return store


def append_transfer(store, *, actor='admin', replacement='member',
                    workspace='alpha', number=1):
    """Save a synthetic receipt beside a real core transfer in one transaction.

    No request authentication/replay/current-authority adapter is exercised.
    The caller must explicitly arrange legal initial roles and old/fresh pins.
    """
    def apply(policy, db):
        current_workspace = policy._workspaces[workspace]
        review = {'version': 1, 'action': 'admin.transfer',
                  'route': '/api/workspaces/' + quote(workspace, safe='') + '/admin-transfers',
                  'realm': policy._realm,
                  'workspace': {'id': workspace, 'state': current_workspace.state,
                                'revision': current_workspace.revision}}
        for label, ident in (('actor', actor), ('replacement', replacement)):
            principal = policy._principals[ident]
            membership = policy._memberships[(workspace, ident)]
            review[label] = {'id': ident, 'issuer': principal.issuer,
                'subject': principal.subject, 'generation': principal.generation,
                'authority_epoch': principal.authority_epoch,
                'membership': {'role': membership.role, 'state': membership.state,
                    'revision': membership.revision,
                    'authority': deepcopy(policy._membership_authority[(workspace, ident)])}}
        values = record(review, number=number, audit_first=len(policy._audit) + 1)
        policy.transfer_admin(actor, workspace, replacement,
            expected_revision=values['actor_revision'],
            replacement_revision=values['replacement_revision'])
        db.execute('INSERT INTO workspace_admin_transfers_v1 VALUES (' +
                   ','.join('?' for _ in FIELDS) + ')', tuple(values[n] for n in FIELDS))
        return values
    return store._transaction(apply, write=True, with_connection=True)


def load_policy(db):
    return object.__new__(SQLiteWorkspaceService)._load(db)
