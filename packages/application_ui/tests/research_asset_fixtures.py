"""Synthetic stored projections only; not a provider or production bootstrap."""
from datetime import datetime,timezone
from packages.research_assets import AssetScope,CatalogBinding,Entity,SourceSpec,SourceBatch,provision_catalog,prepare_batch
from packages.research_assets.store import state,publish

NOW=datetime(2026,1,2,tzinfo=timezone.utc)

def provision(policy):
    entities=(Entity('editor','Editor'),Entity('tools','Tools'))
    source=SourceSpec('sample',('editor','tools'),('api','records','runbook','fault'))
    rows=[]
    for n in range(25):
        rows.append({'type_id':'records','entity_id':'editor' if n%2 else 'tools','summary':'Synthetic research summary',
                     'id':f'record-{n:02d}','title':f'Research {n:02d} <img src=x onerror=alert(1)>',
                     'text':'# Literal heading\n<script>alert(1)</script>\n[Not a link](https://example.invalid)\nSynthetic content only.'})
    rows.extend([
        {'type_id':'api','entity_id':'tools','summary':'Synthetic contract','module':'unit','command':'Build <b>contract</b>',
         'request':'{"path":"logical-field"}','response':'OK','errors':'None','notes':''},
        {'type_id':'runbook','entity_id':'editor','summary':'Declared procedure, not executed','id':'guide','title':'Runbook guide',
         'module':'editor','status':'verified','preconditions':'Synthetic only','steps':'Describe; do not execute','expectations':'Not independently verified'},
        {'type_id':'fault','entity_id':'tools','summary':'Declared guidance, not diagnosis','id':'fault','title':'Fault guide',
         'library':'synthetic','category':'build','status':'active','confidence':'declared only','symptoms':'Synthetic symptom','guidance':'Read-only guidance'},
    ])
    for name in ('knowledge','empty','unpublished'):
        scope=AssetScope('alpha',name);binding=CatalogBinding(scope,'synthetic-team',entities,(source,));provision_catalog(policy,binding)
        if name=='unpublished':continue
        prepared,evidence=prepare_batch(binding,(SourceBatch(scope,'sample','r1','2026-01-01T12:00:00Z',tuple(rows if name=='knowledge' else []),True),),NOW)
        def save(current,conn):publish(current,'admin',conn,scope,state(current,'admin',conn,scope),prepared,evidence)
        policy._transaction(save,write=True,with_connection=True)
