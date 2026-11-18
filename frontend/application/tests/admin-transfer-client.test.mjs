import test from 'node:test'
import assert from 'node:assert/strict'
import * as codec from '../client.mjs'
import {adminTransferClientCases} from '../../workspace-admin/tests/admin-transfer-client-cases.mjs'
adminTransferClientCases('Cookie',async(fetcher,onInvalid)=>{
 const client=codec.createSessionClient({cookie:()=>'__Host-ms-collab-csrf='+'A'.repeat(43),onInvalid,fetcher:(path,options)=>path==='/auth/session'?Promise.resolve({ok:true,status:200,text:async()=>'{"data":{"principal_id":"admin"}}'}):fetcher(path,options)})
 await client.inspect();return client
},codec)
test('Cookie session response accepts exact policy identity domain and rejects whitespace/surrogates',async()=>{
 for(const principal_id of ['😀'.repeat(200),'opaque\0\u007f\uFEFF','a/b','a\\b','a%2Fb']){
  const client=codec.createSessionClient({fetcher:async()=>({ok:true,status:200,text:async()=>JSON.stringify({data:{principal_id}})})})
  assert.deepEqual(await client.inspect(),{principal_id})
 }
 for(const principal_id of ['😀'.repeat(201),'a\u001c','a\u0085','a b','\ud800']){
  const client=codec.createSessionClient({fetcher:async()=>({ok:true,status:200,text:async()=>JSON.stringify({data:{principal_id}})})})
  await assert.rejects(client.inspect(),/Invalid session response/)
 }
})

test('generic prior requests do not acquire the new transfer-only same-epoch401 guard',async()=>{
 const invalid=[],client=codec.createSessionClient({onInvalid:message=>invalid.push(message),fetcher:async path=>({ok:path==='/auth/session',status:path==='/auth/session'?200:401,text:async()=>JSON.stringify(path==='/auth/session'?{data:{principal_id:'admin'}}:{error:'authentication_required'})})})
 await client.inspect();await assert.rejects(client.request('/api/workspaces/alpha/members',{isCurrent:()=>false,transferIsCurrent:()=>false}),error=>error.status===401)
 assert.equal(invalid.length,1);await assert.rejects(client.request('/api/workspaces'),/Session recheck required/)
})
