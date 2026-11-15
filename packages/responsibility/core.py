# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Literal responsibility configuration and read-only candidates, not workflow."""
import re

FIELD_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')
PRINCIPAL_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}\Z')
IDENTITY_NAMESPACE = 'ms-collab.principal.id'


def repo_path(value):
    """Literal repository-relative path. No filesystem or glob interpretation."""
    if (type(value) is not str or not value or len(value)>1024 or value.startswith('/')
            or '\\' in value or any(ord(c)<32 or ord(c)==127 for c in value)
            or any(p in {'','.','..'} for p in value.split('/'))):
        raise ValueError('invalid_literal_path')
    return value


def fields(value):
    """Fresh allowlisted dictionaries; ownership is not a permission grant."""
    if type(value) is not list or len(value)>200:
        raise ValueError('invalid_fields')
    result=[]; seen=set()
    for item in value:
        if type(item) is not dict or set(item)!={'id','name','kind','paths','owner_ids','backup'}:
            raise ValueError('invalid_field')
        fid,name,kind=item['id'],item['name'],item['kind']
        if type(fid) is not str or not FIELD_ID.fullmatch(fid) or fid in seen:
            raise ValueError('invalid_field_id')
        if type(name) is not str or not name.strip() or len(name)>100 or any(ord(c)<32 or ord(c)==127 for c in name):
            raise ValueError('invalid_field_name')
        if kind not in {'developer','committer'}:
            raise ValueError('invalid_field_kind')
        paths,owners=item['paths'],item['owner_ids']
        if type(paths) is not list or not 1<=len(paths)<=100:
            raise ValueError('invalid_paths')
        paths=[repo_path(p) for p in paths]
        if type(owners) is not list or not 1<=len(owners)<=(3 if kind=='developer' else 100):
            raise ValueError('invalid_owners')
        if any(type(p) is not str or not PRINCIPAL_ID.fullmatch(p) for p in owners):
            raise ValueError('invalid_owner')
        if len(set(paths))!=len(paths) or len(set(owners))!=len(owners):
            raise ValueError('duplicate_field_value')
        backup=item['backup']
        if backup is not None:
            if (kind!='developer' or type(backup) is not dict or set(backup)!={'principal_id','field_id'}
                    or type(backup['principal_id']) is not str or not PRINCIPAL_ID.fullmatch(backup['principal_id'])
                    or type(backup['field_id']) is not str or not FIELD_ID.fullmatch(backup['field_id'])):
                raise ValueError('invalid_backup')
            backup=dict(backup)
        result.append(dict(id=fid,name=name.strip(),kind=kind,paths=paths,owner_ids=list(owners),backup=backup));seen.add(fid)
    by_id={f['id']:f for f in result}
    for item in result:
        backup=item['backup']
        if backup:
            source=by_id.get(backup['field_id'])
            if (not source or source['kind']!='developer' or source['id']==item['id']
                    or backup['principal_id'] not in source['owner_ids'] or backup['principal_id'] in item['owner_ids']):
                raise ValueError('invalid_backup_source')
    return result


def matches(path,prefix):
    return path==prefix or path.startswith(prefix+'/')


def project(config, items, evidence, actor, *, view='mine',field_id='',offset=0,limit=50):
    """Inputs must already be scope-authorized and content-approved by a service.

    Evidence maps (kind,number) to validated PathEvidence. No inferred external
    assignee mapping: a REST ID equal to a principal ID proves nothing.
    """
    if type(actor) is not str or not PRINCIPAL_ID.fullmatch(actor):raise ValueError('invalid_actor')
    configured=fields(config)
    if view not in {'mine','all','field','unassigned'} or (view!='field' and field_id):raise ValueError('invalid_view')
    if view=='field' and field_id not in {f['id'] for f in configured}:raise ValueError('unknown_field')
    if type(offset) is not int or not 0<=offset<=100000 or type(limit) is not int or not 1<=limit<=100:raise ValueError('invalid_page')
    # Index literal prefixes once. Avoid config-paths × observed-paths scans:
    # publication is bounded, but both dimensions can still be substantial.
    by_prefix={}
    for index,field in enumerate(configured):
        for prefix in field['paths']:by_prefix.setdefault(prefix,set()).add(index)
    rows=[];incomplete=0
    for item in sorted(items,key=lambda i:i.key):
        if item.state!='open':continue
        proof=evidence.get(item.key) if item.kind=='pull' else None
        complete=proof is not None and proof.complete
        if item.kind=='pull' and not complete:incomplete+=1
        matched_indices=set();unmapped=[]
        if complete:
            for path in proof.paths:
                indices=set();components=path.split('/')
                for end in range(1,len(components)+1):
                    indices.update(by_prefix.get('/'.join(components[:end]),()))
                matched_indices.update(indices)
                if not any(configured[i]['kind']=='developer' for i in indices):unmapped.append(path)
        matched=[field for i,field in enumerate(configured) if i in matched_indices]
        developers=[f for f in matched if f['kind']=='developer']
        mine=[f['id'] for f in developers if actor in f['owner_ids']]
        if view=='mine' and not mine:continue
        if view=='field' and field_id not in {f['id'] for f in matched}:continue
        if view=='unassigned' and item.kind!='issue':continue
        rows.append(dict(kind=item.kind,number=item.number,title=item.title,
            assignment_status='incomplete' if item.kind=='pull' and not complete else 'partially_mapped' if developers and unmapped else 'matched' if developers else 'unassigned',
            unmapped_paths=unmapped,fields=[dict(id=f['id'],kind=f['kind'],review_status='unverified') for f in matched],
            my_field_ids=mine,assignee_identity_status='unknown',review_status='unverified',approval_available=False,
            path_evidence='complete_unpinned_observation' if complete else 'unavailable',
            head_sha=proof.head_sha if complete else '',base_sha=proof.base_sha if complete else ''))
    return dict(mode='readonly_foundation',workflow_enabled=False,review_status='unverified',approval_available=False,
                transport_executable=False,external_effect='not_observed',identity_namespace=IDENTITY_NAMESPACE,
                items=rows[offset:offset+limit],total=len(rows),offset=offset,limit=limit,view=view,
                incomplete_pull_count=incomplete,directory_status='not_implemented',coverage='approved_observed_subset')
