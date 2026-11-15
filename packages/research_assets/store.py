"""Dedicated catalog tables in the existing policy SQLite transaction."""
import hashlib
from .core import AssetError, AssetScope, CatalogBinding, canonical, decode, identifier, utc, validate_summary, validate_detail, MAX_ASSETS
from packages.workspaces import PolicyError, SQLiteWorkspaceService


def provision_catalog(store, binding):
    """Explicit trusted setup only; never replace a previously bound source set.

    This is not a request handler, migration of existing data, or authority grant.
    Provision the workspace/module separately using its approved setup workflow.
    """
    if type(store) is not SQLiteWorkspaceService or type(binding) is not CatalogBinding:
        raise AssetError("invalid_setup")
    def setup(policy, conn):
        if binding.scope.workspace_id not in policy._workspaces or (binding.scope.workspace_id, "research_assets") not in policy._modules:
            raise AssetError("workspace_not_provisioned")
        conn.execute('''CREATE TABLE IF NOT EXISTS research_catalog (
            workspace_id TEXT NOT NULL, catalog_id TEXT NOT NULL, binding TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision>=1), evidence TEXT NOT NULL, available INTEGER NOT NULL CHECK(available IN (0,1)),
            PRIMARY KEY(workspace_id,catalog_id), FOREIGN KEY(workspace_id) REFERENCES workspaces(id)) STRICT''')
        conn.execute('''CREATE TABLE IF NOT EXISTS research_asset (
            workspace_id TEXT NOT NULL, catalog_id TEXT NOT NULL, id TEXT NOT NULL,
            type_id TEXT NOT NULL, entity_id TEXT NOT NULL, title TEXT NOT NULL,
            search_text TEXT NOT NULL, summary TEXT NOT NULL, summary_digest TEXT NOT NULL, detail TEXT NOT NULL,
            PRIMARY KEY(workspace_id,catalog_id,id),
            FOREIGN KEY(workspace_id,catalog_id) REFERENCES research_catalog(workspace_id,catalog_id)) STRICT''')
        conn.execute('CREATE INDEX IF NOT EXISTS research_asset_query ON research_asset(workspace_id,catalog_id,type_id,entity_id,title,id)')
        old = conn.execute('SELECT binding FROM research_catalog WHERE workspace_id=? AND catalog_id=?', _key(binding.scope)).fetchone()
        if old:
            if old[0] != binding.encode():
                raise AssetError("binding_already_provisioned")
            return
        conn.execute('INSERT INTO research_catalog VALUES(?,?,?,1,?,0)', (*_key(binding.scope), binding.encode(), '{}'))
    store._transaction(setup, write=True, with_connection=True)


def _key(scope):
    if type(scope) is not AssetScope:
        raise AssetError("invalid_scope")
    return scope.workspace_id, scope.catalog_id


def _named(conn, sql, args):
    cursor = conn.execute(sql, args)
    row = cursor.fetchone()
    return dict(zip((d[0] for d in cursor.description), row)) if row is not None else None


def state(policy, actor, conn, scope, *, write=False):
    # Current policy is evaluated before even discovering a catalog's existence.
    policy.authorize(actor, scope.workspace_id, "research_assets", "read")
    if write:
        policy.authorize(actor, scope.workspace_id, "research_assets_sync", "write")
    row = _named(conn, 'SELECT * FROM research_catalog WHERE workspace_id=? AND catalog_id=?', _key(scope))
    if row is None:
        raise PolicyError(404, "not_found")
    try:
        binding = CatalogBinding.decode(row['binding'])
        if binding.scope != scope or type(row['revision']) is not int or row['revision'] < 1 or row['available'] not in (0, 1):
            raise AssetError("invalid_state")
        evidence = decode(row['evidence'], 16384)
        if (type(evidence) is not dict or (row['available'] and set(evidence) != {s.id for s in binding.sources})
                or (not row['available'] and evidence)):
            raise AssetError("invalid_state")
        for item in evidence.values():
            if type(item) is not dict or set(item) != {'revision', 'observed_at', 'digest', 'count'}:
                raise AssetError("invalid_state")
            identifier(item['revision']); utc(item['observed_at'])
            if type(item['count']) is not int or not 0 <= item['count'] <= MAX_ASSETS:
                raise AssetError('invalid_state')
            if type(item['digest']) is not str or len(item['digest']) != 64 or any(c not in '0123456789abcdef' for c in item['digest']):
                raise AssetError('invalid_state')
        count = conn.execute('SELECT count(*) FROM research_asset WHERE workspace_id=? AND catalog_id=?', _key(scope)).fetchone()[0]
        if count != sum(e['count'] for e in evidence.values()) or count > MAX_ASSETS:
            raise AssetError('invalid_state')
        return {"binding": binding, "binding_text": row['binding'], "revision": row['revision'],
                "evidence": evidence, "available": bool(row['available'])}
    except (ValueError, KeyError, TypeError):
        raise PolicyError(503, "asset_data_unavailable") from None


def publish(policy, actor, conn, scope, previous, assets, evidence):
    for source, incoming in evidence.items():
        old = previous['evidence'].get(source)
        if old:
            changed = incoming['revision'] != old['revision'] or incoming['digest'] != old['digest']
            if (utc(incoming['observed_at']) < utc(old['observed_at'])
                    or (changed and utc(incoming['observed_at']) == utc(old['observed_at']))):
                raise PolicyError(409, "stale_observation")
            if incoming['revision'] == old['revision'] and incoming['digest'] != old['digest']:
                raise PolicyError(409, "source_revision_conflict")
    if previous['available'] and evidence == previous['evidence']:
        return previous['revision'], False
    conn.execute('DELETE FROM research_asset WHERE workspace_id=? AND catalog_id=?', _key(scope))
    for asset in assets:
        summary = {k: v for k, v in asset.items() if k != 'detail'}
        search = ' '.join([asset['title'], asset['summary'], *asset['fields'].values()]).casefold()
        summary_text = canonical(summary)
        conn.execute('INSERT INTO research_asset VALUES(?,?,?,?,?,?,?,?,?,?)',
                     (*_key(scope), asset['id'], asset['type_id'], asset['entity']['id'], asset['title'], search,
                      summary_text, hashlib.sha256(canonical([scope.as_dict(), summary]).encode('ascii')).hexdigest(), canonical(asset['detail'])))
    revision = previous['revision'] + 1
    conn.execute('UPDATE research_catalog SET revision=?,evidence=?,available=1 WHERE workspace_id=? AND catalog_id=?',
                 (revision, canonical(evidence), *_key(scope)))
    policy._changed(actor, scope.workspace_id, "resource.update", "research-assets-" + scope.catalog_id, revision)
    return revision, True


def query(conn, scope, current, endpoint, asset_id, options):
    binding = current['binding']
    head = {"scope": scope.as_dict(), "revision": current['revision'], "available": current['available'],
            "live_provider_verified": False, "execution_verified": False}
    if options.get('revision') is not None and options['revision'] != current['revision']:
        raise PolicyError(412, "stale_revision")
    try:
        from .core import TYPES
        if endpoint in {'types', 'entities'}:
            column = 'type_id' if endpoint == 'types' else 'entity_id'
            counts = dict(conn.execute('SELECT '+column+',count(*) FROM research_asset WHERE workspace_id=? AND catalog_id=? GROUP BY '+column, _key(scope)))
            allowed = set(TYPES) if endpoint == 'types' else {e.id for e in binding.entities}
            if not set(counts) <= allowed:
                raise AssetError('invalid_state')
            items = ([{'id': k, 'title': v[0], 'detail': v[1], 'columns': list(v[2]), 'count': counts.get(k, 0)} for k, v in TYPES.items()]
                     if endpoint == 'types' else [{'id': e.id, 'title': e.title, 'count': counts.get(e.id, 0)} for e in binding.entities])
            return {**head, 'items': items}
        if asset_id is not None:
            row = _named(conn, 'SELECT id,type_id,entity_id,title,summary,summary_digest,detail FROM research_asset WHERE workspace_id=? AND catalog_id=? AND id=?', (*_key(scope), asset_id))
            if not row:
                raise PolicyError(404, "not_found")
            summary = _summary(row, binding)
            detail = validate_detail(decode(row['detail'], 65536), summary)
            return {**head, 'asset': {**summary, 'detail': detail}}
        if options.get('type') and options['type'] not in TYPES:
            raise PolicyError(400, "invalid_query")
        if options.get('entity') and options['entity'] not in {e.id for e in binding.entities}:
            raise PolicyError(400, "invalid_query")
        where, args = 'workspace_id=? AND catalog_id=?', list(_key(scope))
        for field, column in (('type', 'type_id'), ('entity', 'entity_id')):
            if options.get(field):
                where += ' AND '+column+'=?'; args.append(options[field])
        if options.get('q'):
            where += ' AND instr(search_text,?)>0'; args.append(options['q'].casefold())
        total = conn.execute('SELECT count(*) FROM research_asset WHERE '+where, args).fetchone()[0]
        page, size = options.get('page', 1), options.get('page_size', 50)
        cursor = conn.execute('SELECT id,type_id,entity_id,title,summary,summary_digest FROM research_asset WHERE '+where+' ORDER BY type_id,title COLLATE BINARY,id LIMIT ? OFFSET ?',
                            [*args, size, (page-1)*size])
        rows = [dict(zip((d[0] for d in cursor.description), row)) for row in cursor.fetchall()]
        return {**head, 'items': [_summary(row, binding) for row in rows], 'total': total, 'page': page, 'page_size': size}
    except (ValueError, TypeError, KeyError):
        raise PolicyError(503, "asset_data_unavailable") from None


def _summary(row, binding):
    return validate_summary(decode(row['summary'], 32768), binding, asset_id=row['id'],
                            type_id=row['type_id'], entity_id=row['entity_id'], title=row['title'],
                            digest=row['summary_digest'])
