// Capability-scoped adapter over the one cookie/session client. Never a bearer bridge.
import {baseURL} from '../community/client.mjs'
const own=(o,keys)=>o!==null&&typeof o==='object'&&!Array.isArray(o)&&Object.keys(o).sort().join(',')===keys.sort().join(',')
export function createCommunitySessionClient(session){
 if(!session||typeof session.request!=='function'||typeof session.clear!=='function')throw new Error('Explicit session client required.')
 return {clear:()=>session.clear(),async request(scope,action,{offset=0,body,revision,signal}={}){
  if(!['capabilities','items','review','refresh','approve'].includes(action)||!Number.isSafeInteger(offset)||offset<0||offset>10000)throw new Error('Invalid community request.')
  const write=['refresh','approve'].includes(action),options={signal}
  if(write){
   if(!Number.isSafeInteger(revision)||revision<1||offset!==0)throw new Error('Current revision required.')
   if(action==='refresh'){if(!own(body,[]))throw new Error('Invalid refresh request.')}
   else if(!own(body,['fingerprints'])||!Array.isArray(body.fingerprints)||body.fingerprints.length>200||new Set(body.fingerprints).size!==body.fingerprints.length||body.fingerprints.some(v=>typeof v!=='string'||v.match(/^[0-9a-f]{64}$/)?.[0]!==v))throw new Error('Invalid complete approval set.')
   options.method='POST';options.body=body;options.revision=revision
  }else if(body!==undefined||revision!==undefined||(!['items','review'].includes(action)&&offset!==0))throw new Error('Invalid read request.')
  const suffix=action+(['items','review'].includes(action)?`?limit=100&offset=${offset}`:'')
  return session.request(baseURL(scope)+'/'+suffix,options)
 }}
}
