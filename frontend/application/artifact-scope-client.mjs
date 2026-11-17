// Only current, visible identifiers and capabilities. No provider/configuration data.
const own=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join(',')===[...keys].sort().join(',')
const id=v=>typeof v==='string'&&/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(v)&&!/[\s\u0000-\u001f\u007f]/.test(v)
const invalid=()=>{throw Error('Invalid scope directory. Nothing was opened.')}
// 32 opaque bytes in canonical, unpadded base64url. The final two bits are zero.
const cursor=v=>typeof v==='string'&&v.length===48&&/^dcr1_[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$/.test(v)
const position=(after,lastTuple)=>after===null?lastTuple===null:cursor(after)&&Array.isArray(lastTuple)&&lastTuple.length===2&&id(lastTuple[0])&&id(lastTuple[1])
const tuple=s=>[s.connection_id,s.bucket_id]
const greater=(a,b)=>a[0]>b[0]||a[0]===b[0]&&a[1]>b[1]
export function validateArtifactScopes(data,workspace,managed=false,after=null,lastTuple=null){
 if(!id(workspace)||typeof managed!=='boolean'||!own(data,['workspace_id','items','next_after'])||data.workspace_id!==workspace||!Array.isArray(data.items)||data.items.length>25||!position(after,lastTuple))invalid()
 // UI ordering evidence only. Never derive the server cursor from identifiers.
 let prior=lastTuple
 for(const item of data.items){
  if(!own(item,managed?['scope','capabilities']:['scope','catalog_revision']))invalid()
  const s=item.scope;if(!own(s,['workspace_id','connection_id','bucket_id'])||s.workspace_id!==workspace||Object.values(s).some(v=>!id(v))||prior&&!greater(tuple(s),prior))invalid()
  prior=tuple(s)
  if(managed){const c=item.capabilities;if(!own(c,['refresh','review','approve'])||Object.values(c).some(v=>typeof v!=='boolean')||!Object.values(c).some(Boolean)||c.approve&&!c.review)invalid()}
  else if(!Number.isSafeInteger(item.catalog_revision)||item.catalog_revision<1)invalid()
 }
 if(data.next_after!==null&&(data.items.length!==25||!cursor(data.next_after)||data.next_after===after))invalid()
 return data
}
export function createArtifactDirectoryClient(session,managed=false){
 if(typeof session?.request!=='function'||typeof managed!=='boolean')invalid()
 return {async list(workspace,{after=null,lastTuple=null,signal}={}){
  if(!id(workspace)||!position(after,lastTuple))invalid()
  const path=`/api/workspaces/${workspace}/${managed?'artifact-admin':'artifacts'}/scopes`+(after===null?'':'?'+new URLSearchParams({after}))
  return validateArtifactScopes(await session.request(path,{signal}),workspace,managed,after,lastTuple)
 }}
}
