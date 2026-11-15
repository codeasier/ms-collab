# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Audited admission refusal. An observation is NEVER a runner capability.

There is intentionally no transport, subprocess, queue, driver injection, HTTP
route or switch that can turn this package into an execution service.
"""
import hashlib
import re

from packages.assistant_history.model import encode, identifier, positive
from packages.assistant_history.store import authorized_row
from packages.workspace_api import WorkspaceApplication
from packages.workspaces import SQLiteWorkspaceService, PolicyError, StorageError

_SCHEMA = """CREATE TABLE assistant_execution_preflights (
 workspace_id TEXT NOT NULL, owner_id TEXT NOT NULL, receipt TEXT NOT NULL,
 conversation_id TEXT NOT NULL, conversation_revision INTEGER NOT NULL CHECK(conversation_revision>=1),
 binding TEXT NOT NULL, reason TEXT NOT NULL CHECK(reason='isolation_not_accepted'),
 PRIMARY KEY(workspace_id,owner_id,receipt),
 FOREIGN KEY(workspace_id,conversation_id) REFERENCES assistant_conversations(workspace_id,id),
 FOREIGN KEY(owner_id) REFERENCES principals(id)) STRICT"""
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _hash(value):
    return hashlib.sha256(encode(value).encode('ascii')).hexdigest()


def validate_schema(connection):
    row = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='assistant_execution_preflights'").fetchone()
    if row is None or type(row[0]) is not str or ' '.join(row[0].split()) != ' '.join(_SCHEMA.split()):
        raise StorageError('invalid_preflight_schema')


def initialize(store):
    """Explicit trusted setup; the history and policy schema must already exist."""
    if type(store) is not SQLiteWorkspaceService:
        raise ValueError('configured_store_required')
    from packages.assistant_history.store import validate_schema as history_schema
    def setup(policy, connection):
        history_schema(connection)
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='assistant_execution_preflights'").fetchone() is None:
            connection.execute(_SCHEMA)
        validate_schema(connection)
    store._transaction(setup, write=True, with_connection=True)


def _audit(policy, owner, workspace, receipt):
    # Compatible with the existing audit action vocabulary. No content/locator.
    policy._changed(owner, workspace, 'resource.update', 'assistant-preflight:'+receipt, 1)


class ExecutionPreflight:
    """Internal authenticated integration seam, not a callable WSGI application.

    The exact existing boundary supports either a verifier or the composed
    session boundary, never client-provided owner/roles or fake bearer tokens.
    Even an authorized assessment records a terminal *blocked* receipt. It does
    not accept a prompt, path, command, env, credentials or an isolation claim.
    """
    def __init__(self, *, boundary):
        if type(boundary) is not WorkspaceApplication:
            raise ValueError('explicit_authentication_boundary_required')
        self._boundary = boundary

    def assess(self, environ, *, workspace, conversation, operation, expected_revision):
        # A future web adapter must use POST + fresh CSRF for this audit write.
        # Do not let a caller pass a safe-method session assertion to write audit.
        if type(environ) is not dict or environ.get('REQUEST_METHOD') != 'POST':
            raise PolicyError(405, 'post_required')
        for value in (workspace, conversation, operation):
            identifier(value)
        if len(operation) > 64:
            raise ValueError('invalid_operation')
        positive(expected_revision)
        assertion = self._boundary._verify_request(environ)

        def assess(policy, owner, connection):
            validate_schema(connection)
            row = authorized_row(policy, connection, owner, workspace, conversation, 'write')
            policy._match(expected_revision, row['revision'])
            if row['state'] != 'active':
                raise PolicyError(409, 'conversation_archived')
            receipt = _hash(['preflight-v1', workspace, owner, operation])
            binding = _hash(['preflight-v1', workspace, owner, conversation, row['revision'], row['fence']])
            existing = connection.execute('SELECT conversation_id,conversation_revision,binding,reason FROM assistant_execution_preflights WHERE workspace_id=? AND owner_id=? AND receipt=?', (workspace, owner, receipt)).fetchone()
            if existing is not None:
                try:
                    identifier(existing[0]); positive(existing[1])
                    if type(existing[2]) is not str or not _DIGEST.fullmatch(existing[2]) or existing[3] != 'isolation_not_accepted':
                        raise ValueError()
                except (ValueError, TypeError):
                    raise StorageError('invalid_preflight_record') from None
                if existing != (conversation, row['revision'], binding, 'isolation_not_accepted'):
                    raise PolicyError(409, 'operation_conflict')
            else:
                count = connection.execute('SELECT COUNT(*) FROM assistant_execution_preflights WHERE workspace_id=? AND owner_id=?', (workspace, owner)).fetchone()[0]
                if count >= 100:
                    raise PolicyError(409, 'preflight_limit')
                connection.execute('INSERT INTO assistant_execution_preflights VALUES (?,?,?,?,?,?,?)', (workspace, owner, receipt, conversation, row['revision'], binding, 'isolation_not_accepted'))
                _audit(policy, owner, workspace, receipt)
            return {'receipt': receipt, 'state': 'blocked', 'reason': 'isolation_not_accepted',
                    'execution_enabled': False, 'launch_permitted': False}

        return self._boundary._authenticated_transaction(assertion, assess, write=True, with_connection=True)
