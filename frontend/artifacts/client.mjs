const id = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/
const quarter = /^[1-9][0-9]{3}Q[1-4]$/
const integer = (v, min = 0) => Number.isSafeInteger(v) && v >= min
const text = (v, n) => typeof v === 'string' && v.length <= n && !/[\u0000-\u001f\u007f]/u.test(v)
const instant = v => text(v,40) && /(?:Z|[+-]\d{2}:\d{2})$/.test(v) && Number.isFinite(Date.parse(v))
const invalid = () => new Error('The service returned an invalid response.')
export function validateScope(scope) {
  if (!scope || Object.keys(scope).sort().join() !== 'bucket_id,connection_id,workspace_id' || Object.values(scope).some(v => typeof v !== 'string' || !id.test(v))) throw new Error('Enter valid workspace, connection and bucket IDs.')
  return {...scope}
}
const sameScope = (actual, expected) => {validateScope(actual); return Object.keys(expected).every(k => actual[k] === expected[k])}
export function validKey(value, directory = false) {
  return text(value,2048) && !value.startsWith('/') && !value.includes('\\') && !value.includes('://') && !value.split('/').some(p => ['.','..'].includes(p)) && (directory ? value === '' || value.endsWith('/') : value !== '')
}
const base = scope => {const s=validateScope(scope);return `/api/workspaces/${s.workspace_id}/artifacts/${s.connection_id}/${s.bucket_id}`}
export function validateQuarters(data, scope) {
  if (!data || !sameScope(data.scope,scope) || !integer(data.catalog_revision,1) || !Array.isArray(data.quarters) || data.quarters.some(q=>typeof q!=='string'||!quarter.test(q)) || new Set(data.quarters).size!==data.quarters.length) throw invalid()
  return data
}
export function validateResult(data, {scope,selected,page,pageSize,view=''}) {
  if (!data || !sameScope(data.scope,scope) || data.quarter!==selected || !instant(data.observed_at) || typeof data.quarter_available!=='boolean' || !integer(data.catalog_revision,1) || (view && !integer(data.view_revision,1)) || (!view && Object.hasOwn(data,'view_revision')) || !integer(data.total) || data.page!==page || data.page_size!==pageSize || !Array.isArray(data.items) || data.items.length>pageSize || (!data.quarter_available && (data.total!==0 || data.items.length!==0)) || data.items.length!==Math.max(0,Math.min(pageSize,data.total-(page-1)*pageSize))) throw invalid()
  const seen=new Set()
  for (const row of data.items) {
    if (!row || typeof row.is_dir!=='boolean' || typeof row.virtual!=='boolean' || !validKey(row.key,row.is_dir) || row.key.endsWith('/')!==row.is_dir || !integer(row.size) || (row.is_dir&&row.size!==0) || (row.virtual&&!row.is_dir) || !(row.modified_at===null&&row.virtual || instant(row.modified_at)) || seen.has(row.key)) throw invalid()
    const stem=row.key.replace(/\/$/,'');const parent=stem.includes('/')?stem.slice(0,stem.lastIndexOf('/')+1):''
    if (row.parent!==parent || row.name!==stem.slice(stem.lastIndexOf('/')+1)) throw invalid()
    seen.add(row.key)
  }
  return data
}
export function messageFor(status) {
  return ({401:'Authentication expired or was rejected. Reconnect with a valid credential.',403:'You do not have permission to read this catalog.',404:'This scope or named view is unavailable or no longer accessible.',409:'This workspace or module is unavailable.',503:'Catalog data or the service is unavailable. No successful empty catalog is assumed.'})[status] || 'The service could not complete the request.'
}
export function createClient({fetcher=fetch}={}) {
  let credential=''
  async function request(path,{signal,body}={}) {
    if (!credential) throw new Error('Disconnected.')
    const headers={Authorization:`Bearer ${credential}`};if(body!==undefined)headers['Content-Type']='application/json'
    const response=await fetcher(path,{method:body===undefined?'GET':'POST',headers,body:body===undefined?undefined:JSON.stringify(body),signal,credentials:'omit',cache:'no-store',redirect:'error'})
    if(!response.ok){const error=new Error(messageFor(response.status));error.status=response.status;throw error}
    let value;try{value=await response.json()}catch{throw invalid()}
    if(!value || !Object.hasOwn(value,'data'))throw invalid()
    return value.data
  }
  return {
    connect(value){if(typeof value!=='string'||!value||value.length>8192||/\s/.test(value))throw new Error('Enter a valid opaque credential.');credential=value},
    clear(){credential=''},
    async quarters(scope,signal){return validateQuarters(await request(base(scope)+'/quarters',{signal}),scope)},
    async query({scope,selected,definition,page=1,pageSize=10,view=''},signal){
      if(!quarter.test(selected)||!integer(page,1)||page>1000000||![10,20,50].includes(pageSize)||typeof view!=='string'||(view&&!id.test(view)))throw new Error('Invalid query.')
      const path=base(scope);let result
      if(view) result=await request(`${path}/views/${view}?quarter=${selected}&page=${page}&page_size=${pageSize}`,{signal})
      else {
        if(!definition||!validKey(definition.path,true))throw new Error('Invalid catalog path.')
        result=await request(path+'/query',{signal,body:{quarter:selected,definition,page,page_size:pageSize}})
      }
      return validateResult(result,{scope,selected,page,pageSize,view})
    }
  }
}
