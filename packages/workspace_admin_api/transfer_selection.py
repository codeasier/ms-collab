# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Exact same-snapshot handover review. A proof is not a credential or grant."""
from copy import deepcopy
from hashlib import sha256
from urllib.parse import quote

from packages.workspaces import PolicyError
from .transfer_records import _canonical


def selection(policy, actor, workspace, replacement):
    policy._admin(actor, workspace)
    if (workspace, replacement) not in policy._memberships:
        raise PolicyError(404, 'not_found')
    if actor == replacement or not policy._membership_current(replacement, workspace):
        raise PolicyError(409, 'replacement_ineligible')

    def principal(ident):
        item = policy._principals[ident]
        member = policy._memberships[(workspace, ident)]
        return {'id': ident, 'issuer': item.issuer, 'subject': item.subject,
                'generation': item.generation, 'authority_epoch': item.authority_epoch,
                'membership': {'role': member.role, 'state': member.state,
                    'revision': member.revision,
                    'authority': deepcopy(policy._membership_authority[(workspace, ident)])}}

    scope = policy._workspaces[workspace]
    vector = {'version': 1, 'action': 'admin.transfer',
              'route': f'/api/workspaces/{quote(workspace, safe="")}/admin-transfers',
              'realm': policy._realm,
              'workspace': {'id': workspace, 'state': scope.state, 'revision': scope.revision},
              'actor': principal(actor), 'replacement': principal(replacement)}
    proof = 'ats1-' + sha256(_canonical(vector).encode('ascii')).hexdigest()
    return vector, {'workspace_id': workspace, 'actor_id': actor, 'replacement_id': replacement,
                   'actor_revision': vector['actor']['membership']['revision'],
                   'replacement_revision': vector['replacement']['membership']['revision'],
                   'precondition': proof}
