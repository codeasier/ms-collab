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

test('cookie byte budget preserves near-limit UTF-8 fields and reads fresh CSRF each time',async()=>{
 const {nearLimitFields,escapedNames,wireBytes}=await import('./body-boundary.mjs')
 const requests=[];let csrf='a'.repeat(43)
 const session=createSessionClient({cookie:()=>`__Host-ms-collab-csrf=${csrf}`,fetcher:async(url,options)=>{requests.push({url,options});return {ok:true,text:async()=>JSON.stringify({data:url==='/auth/session'?{principal_id:'admin'}:{}})}}}),client=createResponsibilitySessionClient(session)
 await session.inspect()
 for(const name of escapedNames){
  const fields=nearLimitFields(65536,name),confirmation_token='b'.repeat(64)
  assert.equal(wireBytes({fields,confirmation_token}),65624)
  csrf='c'.repeat(43);await client.request(scope,'save',{fields,revision:3});assert.equal(new TextEncoder().encode(requests.at(-1).options.body).length,65536);assert.equal(requests.at(-1).options.headers['X-CSRF-Token'],csrf)
  csrf='d'.repeat(43);await client.request(scope,'reconfirm',{fields,revision:3,confirmation_token});const sent=requests.at(-1)
  assert.equal(new TextEncoder().encode(sent.options.body).length,65624);assert.equal(sent.options.method,'POST');assert.equal(sent.options.headers['X-CSRF-Token'],csrf);assert.equal(sent.options.headers['If-Match'],'"3"');assert.equal(sent.options.headers.Authorization,undefined);assert.equal(sent.options.credentials,'same-origin');assert.deepEqual(JSON.parse(sent.options.body),{fields,confirmation_token})
  const count=requests.length,tooLarge=nearLimitFields(65537,name)
  await assert.rejects(client.request(scope,'save',{fields:tooLarge,revision:3}),/too large/)
  await assert.rejects(client.request(scope,'reconfirm',{fields:tooLarge,revision:3,confirmation_token}),/too large/);assert.equal(requests.length,count)
 }
})
test('shared cookie transport never expands other routes, methods or malformed wire envelopes',async()=>{
 const {nearLimitFields}=await import('./body-boundary.mjs')
 const requests=[],session=createSessionClient({cookie:()=>`__Host-ms-collab-csrf=${'a'.repeat(43)}`,fetcher:async(url,options)=>{requests.push({url,options});return {ok:true,text:async()=>JSON.stringify({data:url==='/auth/session'?{principal_id:'admin'}:{}})}}})
 await session.inspect();const path='/api/workspaces/alpha/responsibility/source/sample/project/reconfirm',body={fields:nearLimitFields(),confirmation_token:'b'.repeat(64)}
 for(const wrong of [path+'?x=1',path+'/',path+'-extra',path.replace('/reconfirm','/config'),path.replace('/responsibility/','/community/'),path.replace('/alpha/','/%61lpha/'),path.replace('/alpha/','/'+('a'.repeat(129))+'/'),path.replace('/project/','/project/extra/'),'/api/other'])await assert.rejects(session.request(wrong,{method:'POST',body,revision:3}),/too large/)
 for(const method of ['PUT','PATCH'])await assert.rejects(session.request(path,{method,body,revision:3}),/too large/)
 for(const malformed of [{...body,extra:true},{fields:nearLimitFields(65537)},{...body,confirmation_token:'B'.repeat(64)},{...body,confirmation_token:'b'.repeat(63)},{...body,fields:body.fields.map(field=>({...field,extra:true}))},{toJSON:()=>({...body,extra:true})}])await assert.rejects(session.request(path,{method:'POST',body:malformed,revision:3}),/too large/)
 assert.equal(requests.length,1)
 await session.request(path,{method:'POST',body:{toJSON:()=>body},revision:3});assert.equal(requests.length,2);assert.equal(new TextEncoder().encode(requests.at(-1).options.body).length,65624)
})
