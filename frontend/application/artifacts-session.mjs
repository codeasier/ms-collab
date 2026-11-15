// Explicit cookie-only adapters. No connect(), bearer, cookie copy or token getter.
import {createCatalogOperations} from '../artifacts/client.mjs'
import {scopeFrom} from '../artifact-admin/client.mjs'
export function createArtifactSessionClients(session){
 if(!session||typeof session.request!=='function'||typeof session.clear!=='function')throw new Error('Explicit session client required.')
 const clear=()=>session.clear()
 const catalog={clear,...createCatalogOperations((path,{body,signal}={})=>session.request(path,{method:body===undefined?'GET':'POST',body,signal}))}
 const administration={clear,request(scope,action,{body,revision,signal}={}){
  scopeFrom(scope?.workspace_id,scope?.connection_id,scope?.bucket_id)
  if(!['status','review','refresh','approve'].includes(action)||action!=='status'&&(!Number.isSafeInteger(revision)||revision<1))throw new Error('Invalid administration request.')
  const write=action==='refresh'||action==='approve'
  if(write&&(!body||typeof body!=='object'||Array.isArray(body))||!write&&body!==undefined)throw new Error('Invalid administration request.')
  return session.request(`/api/workspaces/${scope.workspace_id}/artifact-admin/${scope.connection_id}/${scope.bucket_id}/${action}`,{method:write?'POST':'GET',body,revision:action==='status'?undefined:revision,signal})
 }}
 return {catalog,administration}
}
