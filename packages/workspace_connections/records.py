"""Explicitly installed, immutable platform bounds for community self-service."""
from dataclasses import asdict, dataclass
from hashlib import sha256
import re

from packages.community_api.records import ConnectionPolicy, encode, decode, read, _ID
from packages.workspaces import SQLiteWorkspaceService, StorageError, PolicyError

GRANTS = ('reader_ids', 'refresher_ids', 'approver_ids')
MAX_REVISION = 9007199254740991
_TABLE = 'workspace_connection_templates_v1'
_SCHEMA = f'''CREATE TABLE {_TABLE} (
 workspace_id TEXT NOT NULL, template_id TEXT NOT NULL, connection_id TEXT NOT NULL,
 repository TEXT NOT NULL, fingerprint TEXT NOT NULL, identities TEXT NOT NULL,
 PRIMARY KEY(workspace_id,template_id), UNIQUE(workspace_id,connection_id,repository),
 FOREIGN KEY(workspace_id,connection_id,repository)
 REFERENCES community_connections(workspace_id,connection_id,repository)
) STRICT'''


@dataclass(frozen=True)
class CommunityConnectionTemplate:
    """Server-only exact binding and maximum grants, never a request body.

    `maximum` pins the complete Connection, credential routing and grant limits.
    A workspace admin must also be an explicitly bound manager. None of these
    platform bounds are mutable through the HTTP service.
    """
    id: str
    maximum: ConnectionPolicy
    manager_ids: tuple[str, ...]

    def __post_init__(self):
        if (type(self.id) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', self.id)
                or type(self.maximum) is not ConnectionPolicy or type(self.manager_ids) is not tuple
                or not self.manager_ids or len(self.manager_ids) > 100
                or any(type(i) is not str or not _ID.fullmatch(i) for i in self.manager_ids)
                or len(set(self.manager_ids)) != len(self.manager_ids)
                or any(len(getattr(self.maximum, k)) > 100 for k in GRANTS)):
            raise ValueError('invalid_connection_template')

    @property
    def scope(self):
        return self.maximum.scope

    @property
    def fingerprint(self):
        # Canonical order avoids treating a reordered allowlist as a new binding.
        value = asdict(self)
        value['manager_ids'] = sorted(self.manager_ids)
        for key in GRANTS:
            value['maximum'][key] = sorted(getattr(self.maximum, key))
        return sha256(encode(value).encode('ascii')).hexdigest()

    @property
    def principals(self):
        return set(self.manager_ids).union(*(getattr(self.maximum, k) for k in GRANTS))


def templates(values):
    if (type(values) is not tuple or len(values) > 100 or any(type(t) is not CommunityConnectionTemplate for t in values)
            or len({(t.scope.workspace_id, t.id) for t in values}) != len(values)
            or len({t.scope for t in values}) != len(values)):
        raise ValueError('explicit_unique_connection_templates_required')
    return values


def check_schema(connection):
    row = connection.execute('SELECT type,sql FROM sqlite_schema WHERE name=?', (_TABLE,)).fetchone()
    normalize = lambda sql: re.sub(r'\s+', ' ', sql).strip()
    if row is None or row[0] != 'table' or type(row[1]) is not str or normalize(row[1]) != normalize(_SCHEMA):
        raise StorageError('invalid_connection_management_schema')


def identity(policy, principal):
    # No missing-generation/realm fallback: a pre-foundation store is unusable.
    try:
        realm, generation = policy._realm, principal.generation
    except AttributeError:
        raise StorageError('identity_generation_required') from None
    if (type(realm) is not str or not re.fullmatch(r'[0-9a-f]{32}', realm)
            or type(generation) is not int or not 1 <= generation <= MAX_REVISION):
        raise StorageError('invalid_connection_identity_generation')
    return [realm, principal.id, principal.issuer, principal.subject, generation]


def compatible(state, template):
    return (state is not None and state.policy.connection == template.maximum.connection
        and state.policy.credential_ref == template.maximum.credential_ref
        and all(set(getattr(state.policy, k)) <= set(getattr(template.maximum, k)) for k in GRANTS)
        and type(state.revision) is int and 1 <= state.revision <= MAX_REVISION)


def initialize_connections(store, values):
    """Trusted additive install only; refuses repinning existing entries.

    Existing community policies must already exist. No identities, connections,
    credentials, providers, approvals or observations are provisioned here.
    """
    values = templates(values)
    if type(store) is not SQLiteWorkspaceService:
        raise ValueError('initialized_policy_store_required')
    def operation(policy, connection):
        connection.execute(_SCHEMA.replace('CREATE TABLE', 'CREATE TABLE IF NOT EXISTS', 1))
        check_schema(connection)
        for template in values:
            if not compatible(read(connection, template.scope), template):
                raise ValueError('connection_binding_not_approved')
            if not template.principals <= policy._principals.keys():
                raise ValueError('unknown_template_principal')
            identities = encode({p: identity(policy, policy._principals[p]) for p in sorted(template.principals)})
            scope = template.scope
            connection.execute(f'INSERT INTO {_TABLE} VALUES (?,?,?,?,?,?)',
                (scope.workspace_id, template.id, scope.connection_id, scope.repository, template.fingerprint, identities))
    store._transaction(operation, write=True, with_connection=True)


def binding(connection, template):
    check_schema(connection)
    row = connection.execute(f'SELECT connection_id,repository,fingerprint,identities FROM {_TABLE} WHERE workspace_id=? AND template_id=?',
        (template.scope.workspace_id, template.id)).fetchone()
    if row is None or row[:3] != (template.scope.connection_id, template.scope.repository, template.fingerprint):
        raise PolicyError(409, 'connection_template_unavailable')
    try:
        identities = decode(row[3])
        if (type(identities) is not dict or set(identities) != template.principals
                or any(type(v) is not list or len(v) != 5 or any(type(x) is not str or not x for x in v[:4])
                    or not re.fullmatch(r'[0-9a-f]{32}', v[0]) or v[1] != key
                    or type(v[4]) is not int or not 1 <= v[4] <= MAX_REVISION for key, v in identities.items())):
            raise ValueError()
    except (TypeError, ValueError, RecursionError):
        raise StorageError('invalid_connection_management_record') from None
    return identities
