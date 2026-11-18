# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Two exact reviewed layouts; never adopt schema or migrate during an open."""
from contextlib import closing
from dataclasses import dataclass
from hashlib import sha256
import json
import sqlite3

from ._layout_v1 import (WORKSPACE, LOGIN, MODULE_SCHEMA, POLICY_SCHEMA,
                         _APPLICATION_ID, _SCHEMA_VERSION, _APP, _VERSION)
from packages.workspace_admin_api.transfer_records import SCHEMA as TRANSFERS


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


# Compatibility names keep the frozen I1 meaning, not "the latest layout".
POLICY_DIGEST = expected(POLICY_SCHEMA)
LOGIN_DIGEST = expected(LOGIN)


@dataclass(frozen=True)
class _LayoutProfile:
    format: int
    module_schema: tuple[str, ...]
    policy_schema: tuple[str, ...]
    policy_digest: str
    login_digest: str


_PROFILES = (
    _LayoutProfile(1, MODULE_SCHEMA, POLICY_SCHEMA, POLICY_DIGEST, LOGIN_DIGEST),
    _LayoutProfile(2, (*MODULE_SCHEMA, *TRANSFERS), (*POLICY_SCHEMA, *TRANSFERS),
                   expected((*POLICY_SCHEMA, *TRANSFERS)), LOGIN_DIGEST),
)


def layout_profile(value):
    """Private configuration lookup, never an HTTP-selected capability."""
    if type(value) is not int or value not in (1, 2):
        raise ValueError('unsupported_layout')
    return _PROFILES[value - 1]
