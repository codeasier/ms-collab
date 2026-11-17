// Only exact current snapshot identifiers/revision. No provider or connection config.
const own=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join(',')===[...keys].sort().join(',')
const id=v=>typeof v==='string'&&/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(v)&&!/[\s\u0000-\u001f\u007f]/.test(v)
const invalid=()=>{throw Error('Invalid pipeline scope directory. Nothing was opened.')}
const cursor=v=>typeof v==='string'&&v.split('~').length===2&&v.split('~').every(id)
const tuple=s=>[s.connection_id,s.pipeline_id]
const greater=(a,b)=>a[0]>b[0]||a[0]===b[0]&&a[1]>b[1]
export function validatePipelineScopes(data,workspace,after=null){
 if(!id(workspace)||!own(data,['workspace_id','items','next_after'])||data.workspace_id!==workspace||!Array.isArray(data.items)||data.items.length>25||after!==null&&!cursor(after))invalid()
 let prior=after?.split('~')
 for(const item of data.items){
  if(!own(item,['scope','snapshot_revision'])||!Number.isSafeInteger(item.snapshot_revision)||item.snapshot_revision<1)invalid()
  const s=item.scope
  if(!own(s,['workspace_id','connection_id','pipeline_id'])||s.workspace_id!==workspace||Object.values(s).some(v=>!id(v))||prior&&!greater(tuple(s),prior))invalid()
  prior=tuple(s)
 }
 if(data.next_after!==null&&(data.items.length!==25||data.next_after!==tuple(data.items.at(-1).scope).join('~')))invalid()
 return data
}
export function createPipelineDirectoryClient(session){
 if(typeof session?.request!=='function')invalid()
 return {async list(workspace,{after=null,signal}={}){
  if(!id(workspace)||after!==null&&!cursor(after))invalid()
  const path=`/api/workspaces/${workspace}/pipelines/scopes`+(after===null?'':'?'+new URLSearchParams({after}))
  return validatePipelineScopes(await session.request(path,{signal}),workspace,after)
 }}
}
