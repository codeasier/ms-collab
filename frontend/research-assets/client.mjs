// Strict read projection. IDs and content revisions are references, never authority.
const own=(value,keys)=>value!==null&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).sort().join(',')===[...keys].sort().join(',')
const match=(value,re)=>typeof value==='string'&&value.match(re)?.[0]===value
const id=value=>match(value,/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/)
const hash=value=>match(value,/^[0-9a-f]{64}$/)
const assetID=value=>match(value,/^a_[0-9a-f]{64}$/)
const integer=(value,min=0,max=1000)=>Number.isSafeInteger(value)&&value>=min&&value<=max
const text=(value,max=256,empty=false,multiline=false)=>typeof value==='string'&&[...value].length<=max&&value===value.trim()&&(empty||value.length>0)&&!(multiline?/[\u0000-\u0008\u000b-\u001f\u007f]/:/[\u0000-\u001f\u007f]/).test(value)
export const TYPES=Object.freeze({api:{title:'API contracts',format:'contract',fields:['module','command'],headings:['Request','Response','Errors','Notes']},records:{title:'Research records',format:'text',fields:[],headings:['Text']},runbook:{title:'Runbooks',format:'sections',fields:['module','status'],headings:['Preconditions','Steps','Expectations']},fault:{title:'Fault knowledge',format:'sections',fields:['library','category','status','confidence'],headings:['Symptoms','Guidance']}})
const invalid=()=>{throw new Error('Invalid or inconsistent catalog. Reload current scope.')}
export function scopeFrom(workspace,catalog){if(![workspace,catalog].every(id))throw new Error('Enter valid workspace and catalog IDs.');return {workspace_id:workspace,catalog_id:catalog}}
const sameScope=(a,b)=>own(a,['workspace_id','catalog_id'])&&a.workspace_id===b.workspace_id&&a.catalog_id===b.catalog_id
function head(value,scope,keys,revision,available){
 if(!own(value,['scope','revision','available','live_provider_verified','execution_verified',...keys])||!sameScope(value.scope,scope)||!integer(value.revision,1,999999999)||typeof value.available!=='boolean'||value.live_provider_verified!==false||value.execution_verified!==false||(revision!==undefined&&value.revision!==revision)||(available!==undefined&&value.available!==available))invalid()
 return value
}
export function validateTypes(value,scope){
 head(value,scope,['items']);if(!Array.isArray(value.items)||value.items.length!==4)invalid()
 const seen=new Set();let total=0
 for(const item of value.items){const schema=TYPES[item?.id];if(!schema||seen.has(item.id)||!own(item,['id','title','detail','columns','count'])||item.title!==schema.title||item.detail!==schema.format||!Array.isArray(item.columns)||JSON.stringify(item.columns)!==JSON.stringify(schema.fields)||!integer(item.count))invalid();seen.add(item.id);total+=item.count}
 if(total>1000||(!value.available&&total!==0))invalid();return value
}
export function validateEntities(value,scope,types){
 head(value,scope,['items'],types.revision,types.available);if(!Array.isArray(value.items)||value.items.length<1||value.items.length>32)invalid()
 let total=0;const ids=new Set()
 for(const item of value.items){if(!own(item,['id','title','count'])||!id(item.id)||!text(item.title)||!integer(item.count)||ids.has(item.id))invalid();ids.add(item.id);total+=item.count}
 if(total!==types.items.reduce((sum,item)=>sum+item.count,0))invalid();return value
}
export function filtersFrom(type='',entity='',q=''){
 if(typeof type!=='string'||(type&&!Object.hasOwn(TYPES,type))||typeof entity!=='string'||(entity&&!id(entity))||!text(q,200,true))throw new Error('Invalid catalog filter.')
 return {type,entity,q}
}
function summary(row,metadata){
 const schema=TYPES[row?.type_id]
 if(!schema||!own(row,['id','type_id','title','summary','entity','fields','content_revision'])||!assetID(row.id)||!text(row.title)||!text(row.summary,1024,true,true)||!hash(row.content_revision)||!own(row.entity,['id','title'])||!metadata.entities.items.some(e=>e.id===row.entity.id&&e.title===row.entity.title)||!own(row.fields,schema.fields)||Object.values(row.fields).some(v=>!text(v))||(row.type_id==='api'&&row.fields.command!==row.title))invalid()
 return row
}
export function validatePage(value,scope,metadata,filters,page,expectedTotal){
 head(value,scope,['items','total','page','page_size'],metadata.types.revision,metadata.types.available)
 if(!integer(value.total)||value.page!==page||value.page_size!==20||!Array.isArray(value.items)||value.items.length!==Math.max(0,Math.min(20,value.total-(page-1)*20)))invalid()
 if(filters.entity&&!metadata.entities.items.some(e=>e.id===filters.entity))invalid()
 const cap=filters.type?metadata.types.items.find(t=>t.id===filters.type).count:metadata.types.items.reduce((n,t)=>n+t.count,0)
 const entityCap=filters.entity?metadata.entities.items.find(e=>e.id===filters.entity).count:1000
 if(value.total>Math.min(cap,entityCap)||(!filters.q&&!filters.entity&&value.total!==cap)||(!filters.q&&!filters.type&&filters.entity&&value.total!==entityCap)||(expectedTotal!==undefined&&value.total!==expectedTotal)||(!value.available&&value.total))invalid()
 const ids=new Set();for(const row of value.items){summary(row,metadata);if(ids.has(row.id)||(filters.type&&row.type_id!==filters.type)||(filters.entity&&row.entity.id!==filters.entity))invalid();ids.add(row.id)}return value
}
export function validateDetail(value,scope,metadata,selected){
 head(value,scope,['asset'],metadata.types.revision,metadata.types.available)
 if(!value.available||!own(value.asset,['id','type_id','title','summary','entity','fields','content_revision','detail']))invalid()
 const {detail,...row}=value.asset;summary(row,metadata)
 if(row.id!==selected.id||row.content_revision!==selected.content_revision||row.type_id!==selected.type_id||row.title!==selected.title||row.summary!==selected.summary||row.entity.id!==selected.entity.id||row.entity.title!==selected.entity.title||Object.keys(selected.fields).some(k=>row.fields[k]!==selected.fields[k]))invalid()
 const schema=TYPES[row.type_id]
 if(!own(detail,['format','sections','execution_verified'])||detail.format!==schema.format||detail.execution_verified!==false||!Array.isArray(detail.sections)||detail.sections.length!==schema.headings.length)invalid()
 for(const [i,section]of detail.sections.entries())if(!own(section,['heading','text'])||section.heading!==schema.headings[i]||!text(section.text,8192,true,true))invalid()
 return value
}
const MAX_CATALOGS=32
export function validateCatalogDirectory(data,workspace){
 if(!id(workspace)||!own(data,['workspace_id','items'])||data.workspace_id!==workspace||
    !Array.isArray(data.items)||data.items.length>MAX_CATALOGS)invalid()
 let last=''
 for(const value of data.items){if(!id(value)||value<=last)invalid();last=value}
 return Object.freeze([...data.items])
}
export function selectableWorkspaces(data){
 if(!Array.isArray(data))invalid()
 const seen=new Set(),items=[]
 for(const workspace of data){
  if(!own(workspace,['id','name','kind','state','revision'])||typeof workspace.id!=='string'||
     !workspace.id.length||[...workspace.id].length>200||seen.has(workspace.id)||
     typeof workspace.name!=='string'||!workspace.name.trim()||[...workspace.name].length>200||
     !['product','shared'].includes(workspace.kind)||!['active','suspended','archived'].includes(workspace.state)||
     !integer(workspace.revision,1,Number.MAX_SAFE_INTEGER))invalid()
  seen.add(workspace.id)
  if(workspace.state!=='suspended'&&id(workspace.id))items.push(Object.freeze({id:workspace.id,name:workspace.name,state:workspace.state}))
 }
 return Object.freeze(items)
}
export function createAssetSessionClient(session){
 if(typeof session?.clear!=='function'||typeof session.readResearchAssets!=='function')invalid()
 return {clear:()=>session.clear(),async workspaces({signal,isCurrent}={}){
  if(typeof isCurrent!=='function')invalid()
  return selectableWorkspaces(await session.readResearchAssets('/api/workspaces',{signal,isCurrent}))
 },async catalogs(workspace,{signal,isCurrent}={}){
  if(!id(workspace)||typeof isCurrent!=='function')invalid()
  return validateCatalogDirectory(await session.readResearchAssets(`/api/workspaces/${workspace}/research-assets`,{signal,isCurrent}),workspace)
 },request(scope,kind,{revision,filters=filtersFrom(),page=1,asset,signal,isCurrent=()=>!signal?.aborted}={}){
  scopeFrom(scope.workspace_id,scope.catalog_id);if(!['types','entities','assets','detail'].includes(kind))throw new Error('Unsupported read.')
  if(kind!=='types'&&!integer(revision,1,999999999))throw new Error('Current catalog revision required.')
  const params=new URLSearchParams();if(revision!==undefined){if(!integer(revision,1,999999999))throw new Error('Current catalog revision required.');params.set('revision',String(revision))}
  if(kind==='assets'){filtersFrom(filters.type,filters.entity,filters.q);if(!integer(page,1,100000))throw new Error('Invalid page.');for(const [key,value]of Object.entries(filters)){if(!['type','entity','q'].includes(key))throw new Error('Invalid filter.');if(value)params.set(key,value)}params.set('page',String(page));params.set('page_size','20')}
  if(kind==='detail'&&!assetID(asset))throw new Error('Invalid asset ID.')
  return session.readResearchAssets(`/api/workspaces/${scope.workspace_id}/research-assets/${scope.catalog_id}/${kind==='detail'?'assets/'+asset:kind}${params.size?'?'+params:''}`,{signal,isCurrent})
 }}
}
