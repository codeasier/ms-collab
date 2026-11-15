# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Exact reviewed schema layout. No implicit adoption or migration of old stores."""
from contextlib import closing
from hashlib import sha256
import json
import sqlite3
from packages.workspaces.sqlite import _SCHEMA as WORKSPACE, _APPLICATION_ID, _SCHEMA_VERSION
from packages.login_state.core import _SCHEMA as LOGIN, _APP, _VERSION
from packages.workspace_admin_api.requests import _CREATE as ADMIN
from packages.workspace_invitations.store import _CREATE as INVITES
from packages.community_api.records import _SCHEMA as COMMUNITY
from packages.artifact_sync.records import _CREATE as ARTIFACTS, _EVENT_CREATE as ARTIFACT_EVENTS
from packages.assistant_history.store import _TABLES as HISTORY
from packages.responsibility_api.records import _SCHEMA as RESPONSIBILITY
from packages.gui_smoke.store import _SCHEMA as GUI
from packages.research_assets.store import _SCHEMA as ASSETS

MODULE_SCHEMA = (ADMIN, INVITES, COMMUNITY, ARTIFACTS, ARTIFACT_EVENTS, *HISTORY.values(), RESPONSIBILITY, GUI, *ASSETS)
POLICY_SCHEMA = (*WORKSPACE, *MODULE_SCHEMA)


def schema_digest(db, name='main'):
    if name not in {'main','login'}:
        raise ValueError('fixed_schema_required')
    rows = db.execute(f'SELECT type,name,tbl_name,sql FROM {name}.sqlite_schema WHERE sql IS NOT NULL ORDER BY type,name').fetchall()
    normalized = [(kind, key, table, ' '.join(sql.split())) for kind,key,table,sql in rows]
    return sha256(json.dumps(normalized, ensure_ascii=True, separators=(',', ':')).encode('ascii')).hexdigest()


def expected(statements):
    with closing(sqlite3.connect(':memory:')) as db:
        for sql in statements:
            db.execute(sql)
        return schema_digest(db)


POLICY_DIGEST = expected(POLICY_SCHEMA)
LOGIN_DIGEST = expected(LOGIN)
