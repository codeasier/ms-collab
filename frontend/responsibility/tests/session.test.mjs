import test from 'node:test'
import assert from 'node:assert/strict'
import {createSessionClient,createResponsibilitySessionClient} from '../../application/client.mjs'
import {scopeFrom} from '../client.mjs'
const scope=scopeFrom('alpha','source','sample','project')
const fields=[{id:'ui',name:'UI',kind:'developer',paths:['ui'],owner_ids:['reader'],backup:null}]
test('reconfirmation uses the shared cookie session, fresh CSRF and exact proposal CAS',async()=>{
 const requests=[];let csrf='a'.repeat(43)
 const session=createSessionClient({cookie:()=>`__Host-ms-collab-csrf=${csrf}`,fetcher:async(url,options)=>{requests.push({url,options});return {ok:true,text:async()=>JSON.stringify({data:url==='/auth/session'?{principal_id:'admin'}:{}})}}}),client=createResponsibilitySessionClient(session)
 await session.inspect();await client.request(scope,'reconfirm',{fields,revision:3,confirmation_token:'b'.repeat(64)})
 csrf='c'.repeat(43);await client.request(scope,'reconfirm',{fields,revision:4,confirmation_token:'d'.repeat(64)})
 for(const [i,token] of [[1,'a'],[2,'c']]){const {url,options}=requests[i];assert.ok(url.endsWith('/reconfirm'));assert.equal(options.method,'POST');assert.equal(options.credentials,'same-origin');assert.equal(options.headers.Authorization,undefined);assert.equal(options.headers['X-CSRF-Token'],token.repeat(43));assert.equal(options.headers['If-Match'],`"${i+2}"`);assert.deepEqual(JSON.parse(options.body),{fields,confirmation_token:(i===1?'b':'d').repeat(64)})}
 for(const token of [null,undefined,'A'.repeat(64)])await assert.rejects(client.request(scope,'reconfirm',{fields,revision:4,confirmation_token:token}))
 for(const revision of [undefined,0,NaN,1.5,'3'])await assert.rejects(client.request(scope,'reconfirm',{fields,revision,confirmation_token:'d'.repeat(64)}))
 assert.equal(requests.length,3);client.clear();await assert.rejects(client.request(scope,'reconfirm',{fields,revision:4,confirmation_token:'d'.repeat(64)}));assert.equal(requests.length,3)
})
test('cookie reconfirmation response cannot survive identity epoch change',async()=>{
 let release;const response=new Promise(r=>release=r)
 const session=createSessionClient({cookie:()=>`__Host-ms-collab-csrf=${'a'.repeat(43)}`,fetcher:async url=>url==='/auth/session'?{ok:true,text:async()=>JSON.stringify({data:{principal_id:'admin'}})}:response}),client=createResponsibilitySessionClient(session)
 await session.inspect();const pending=client.request(scope,'reconfirm',{fields,revision:3,confirmation_token:'b'.repeat(64)});session.clear();release({ok:true,text:async()=>JSON.stringify({data:{}})});await assert.rejects(pending,/Stale session response/)
})
