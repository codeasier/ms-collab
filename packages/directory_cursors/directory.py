# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Three explicit directory adapters only. No tuple fallback or implicit DDL."""
from hashlib import sha256
import json
import re
from urllib.parse import parse_qsl

from packages.workspaces import PolicyError
from .store import ReceiptStore, changed, token_value, unavailable
from .transaction import ReceiptOnly, _DirectoryUnavailable

PAGE_SIZE = 25


def fingerprint(value):
    # An internal equality digest, NOT a MAC or client authentication mechanism.
    return sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                             separators=(',', ':')).encode('ascii')).digest()


def configured(value):
    if value is not None and type(value) is not ReceiptStore:
        raise ValueError('explicit directory receipt store required')
    return value


def gate(policy, actor, workspace, action):
    if action == 'artifact.scopes.admin':
        policy._admin(actor, workspace)
        try:
            policy.authorize(actor, workspace, 'artifacts', 'read')
        except PolicyError as error:
            if error.status != 403:
                raise
            policy.authorize(actor, workspace, 'artifacts', 'write')
    else:
        policy.authorize(actor, workspace, 'pipelines' if action == 'pipeline.scopes.read' else 'artifacts', 'read')


def after_query(query):
    """Called only inside current authenticated/scope-authorized transaction."""
    if query == '':
        return None
    if type(query) is not str:
        raise PolicyError(400, 'invalid_request')
    try:
        if len(query) > 600 or re.search(r'%(?![0-9a-fA-F]{2})', query):
            raise ValueError()
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True,
                          max_num_fields=4, encoding='utf-8', errors='strict')
    except (ValueError, UnicodeError):
        raise changed() from None
    if any(key != 'after' for key, _ in pairs):
        raise PolicyError(400, 'invalid_request')
    if len(pairs) != 1:
        raise changed()
    return token_value(pairs[0][1])


def serve(api, assertion, *, workspace, query, action, store, projection, allow_refresh=False):
    if store is None:
        def closed(policy, actor, db, receipts):
            gate(policy, actor, workspace, action)
            raise unavailable()
        # W05 replaces this callback outside the pager. Both that rejection and
        # gate errors must run the SAME final-current path as configured pages.
        # The private denial plan never constructs/arms a receipt context.
        return api._authenticated_transaction(assertion, closed, with_connection=True,
            receipt_only=_DirectoryUnavailable(workspace, action))
    plan = ReceiptOnly(store, workspace, action, projection, allow_refresh)

    def page(policy, actor, db, receipts):
        # arm's fixed gate precedes schema/cursor diagnostics and all index reads.
        receipts.arm()
        token = after_query(query)
        visible = receipts.view()
        anchor = receipts.resume(token) if token is not None else None
        start = 0
        if anchor is not None:
            positions = [i for i, (key, _) in enumerate(visible) if key == anchor]
            if not positions:
                raise changed()
            start = positions[0] + 1
        selected = visible[start:start + PAGE_SIZE]
        next_after = receipts.issue(selected[-1][0]) if start + len(selected) < len(visible) else None
        return {'workspace_id': workspace, 'items': [item for _, item in selected], 'next_after': next_after}

    return api._authenticated_transaction(assertion, page, with_connection=True, receipt_only=plan)
