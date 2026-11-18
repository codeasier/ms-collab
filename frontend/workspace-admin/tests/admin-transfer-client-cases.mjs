// Registered by both existing Node roots; these are transport boundary tests,
// not a simulated service transaction or browser acceptance result.
import nodeTest from 'node:test'
import assert from 'node:assert/strict'
import {validateMembers,membershipURL} from '../client.mjs'
const test=(name,body)=>nodeTest(name,{timeout:10000},body)
const workspace='space:1',replacement='person:1',actor='actor\0\u007f\\\uFEFF'+'😀'.repeat(190)
const proof='ats1-'+'a'.repeat(64),key='12345678-1234-4123-8123-123456789abc'
const review={workspace_id:workspace,actor_id:actor,replacement_id:replacement,actor_revision:4,replacement_revision:7,precondition:proof}
const attempt={...review,requestKey:key}
const result={workspace_id:workspace,actor_id:actor,replacement_id:replacement,actor_revision:5,replacement_revision:8,actor_role:'developer',replacement_role:'admin',state:'committed'}
const base='/api/workspaces/space%3A1/admin-transfers',selection=base+'/selections/person%3A1'
const reply=(payload,status=200)=>({ok:status>=200&&status<300,status,text:async()=>JSON.stringify(payload),json:async()=>payload})
const deferred=()=>{let resolve,reject;const promise=new Promise((r,j)=>{resolve=r;reject=j});return{promise,resolve,reject}}
const guard=()=>true
const invoke=(client,kind,options={isCurrent:guard})=>kind==='capabilities'?client.transferCapabilities(workspace,options):kind==='review'?client.reviewAdminTransfer(workspace,replacement,options):client.submitAdminTransfer(attempt,options)
const payloadFor=kind=>({data:kind==='capabilities'?{available:true}:kind==='review'?review:result})
export function adminTransferClientCases(mode,create,codec){
 test(`${mode}: administrator transfer exact wire and byte-equivalent retry`,async()=>{
  const calls=[],client=await create(async(path,options)=>{calls.push({path,options});return reply(path.endsWith('/capabilities')?{data:{available:true}}:path===selection?{data:review}:{data:result})})
  assert.deepEqual(await invoke(client,'capabilities'),{available:true});assert.deepEqual(await invoke(client,'review'),review)
  assert.deepEqual(await invoke(client,'submit'),result);assert.deepEqual(await invoke(client,'submit'),result)
  assert.deepEqual(calls.map(c=>c.path),[base+'/capabilities',selection,base,base])
  const post=calls[2].options
  assert.equal(post.method,'POST');assert.equal(post.body,'{"replacement_id":"person:1","replacement_revision":"7"}')
  assert.equal(post.headers['If-Match'],'"4"');assert.equal(post.headers['Idempotency-Key'],key);assert.equal(post.headers['If-Admin-Transfer-Match'],proof)
  assert.deepEqual(calls[3].options,post)
  for(const {options}of calls){assert.equal(options.credentials,mode==='Cookie'?'same-origin':'omit');assert.equal(options.cache,'no-store');assert.equal(options.redirect,'error');assert.equal(options.headers.isCurrent,undefined)}
  assert.equal(calls[0].options.body,undefined);assert.equal(calls[1].options.headers['If-Match'],undefined)
  if(mode==='Cookie'){assert.equal(post.headers.Authorization,undefined);assert.equal(post.headers['X-CSRF-Token'],'A'.repeat(43))}else assert.equal(post.headers.Authorization,'Bearer synthetic-only')
 })
 test(`${mode}: non-route inputs and exact options reject before any fetch`,async()=>{
  let calls=0;const client=await create(async()=>{calls++;throw Error('unexpected fetch')})
  for(const bad of ['../x','a/b','a\\b','a%2Fb','a%5Cb','a?x','a#x','é','x\0','x\n','😀'.repeat(200)]){
   await assert.rejects(client.transferCapabilities(bad,{isCurrent:guard}));await assert.rejects(client.reviewAdminTransfer(workspace,bad,{isCurrent:guard}));await assert.rejects(client.submitAdminTransfer({...attempt,replacement_id:bad},{isCurrent:guard}))
  }
  for(const options of [null,[],{isCurrent:guard,body:{}},{isCurrent:guard,revision:4},{isCurrent:guard,requestKey:key},{isCurrent:guard,signal:{}},{isCurrent:guard,[Symbol('hidden')]:true}])for(const kind of ['capabilities','review','submit'])await assert.rejects(invoke(client,kind,options))
  for(const isCurrent of [undefined,null,false,()=>false,()=>1,()=>{throw Error('private marker')}])for(const kind of ['capabilities','review','submit'])await assert.rejects(invoke(client,kind,{isCurrent}),e=>e.name==='AbortError'&&!e.message.includes('private marker'))
  for(const path of [base,selection,base+'/capabilities',base+'?x=1',base+'/extra'])await assert.rejects(client.request(path,{method:'POST',body:{replacement_id:replacement},transferIsCurrent:guard}))
  assert.equal(calls,0)
 })
 test(`${mode}: required replacement rejects missing and undefined before any transfer fetch`,async()=>{
  let calls=0;const client=await create(async()=>{calls++;throw Error('unexpected fetch')})
  const {replacement_id,...missing}=review
  assert.equal(codec.adminTransferURL(workspace),base)
  assert.throws(()=>codec.adminTransferURL(workspace,undefined))
  assert.throws(()=>codec.validateAdminTransferSelection(missing,workspace))
  assert.throws(()=>codec.validateAdminTransferSelection({...review,replacement_id:undefined},workspace,undefined))
  await assert.rejects(client.reviewAdminTransfer(workspace))
  await assert.rejects(client.reviewAdminTransfer(workspace,undefined,{isCurrent:guard}))
  await assert.rejects(client.submitAdminTransfer({...missing,requestKey:key},{isCurrent:guard}))
  await assert.rejects(client.submitAdminTransfer({...attempt,replacement_id:undefined},{isCurrent:guard}))
  assert.equal(calls,0)
 })
 test(`${mode}: immutable attempt validates UUID proof revisions and complete tuple before fetch`,async()=>{
  let calls=0;const client=await create(async()=>{calls++;throw Error('unexpected fetch')})
  for(const value of [null,[],{}, {...attempt,extra:true},{...attempt,actor_id:replacement},{...attempt,actor_id:'with space'},{...attempt,actor_revision:0},{...attempt,replacement_revision:'7'},{...attempt,actor_revision:Number.MAX_SAFE_INTEGER+1}])await assert.rejects(client.submitAdminTransfer(value,{isCurrent:guard}))
  for(const bad of ['',key.toUpperCase(),key+'\n','12345678-1234-1123-8123-123456789abc','12345678-1234-4123-7123-123456789abc'])await assert.rejects(client.submitAdminTransfer({...attempt,requestKey:bad},{isCurrent:guard}))
  for(const bad of ['',proof.toUpperCase(),proof+'\n',proof+'\0','"'+proof+'"'])await assert.rejects(client.submitAdminTransfer({...attempt,precondition:bad},{isCurrent:guard}))
  assert.equal(calls,0)
  const held=deferred(),mutable={...attempt},snapshot=await create(async()=>held.promise)
  const pending=snapshot.submitAdminTransfer(mutable,{isCurrent:guard});mutable.actor_id='changed';mutable.actor_revision=99;mutable.precondition='ats1-'+'b'.repeat(64)
  held.resolve(reply({data:result}));assert.deepEqual(await pending,result)
 })
 test(`${mode}: exact HTTP200 envelopes reject extra fields wrong scope and noncanonical results`,async()=>{
  for(const kind of ['capabilities','review','submit']){
   for(const status of [201,202,204]){const client=await create(async()=>reply(payloadFor(kind),status));await assert.rejects(invoke(client,kind))}
   for(const payload of [{...payloadFor(kind),extra:true},{result:payloadFor(kind).data},{data:{...payloadFor(kind).data,extra:true}}]){const client=await create(async()=>reply(payload));await assert.rejects(invoke(client,kind))}
   for(const raw of ['not-json','x'.repeat(16385)]){const client=await create(async()=>({ok:true,status:200,text:async()=>raw}));await assert.rejects(invoke(client,kind))}
  }
  for(const data of [{available:false},{available:'true'},{}]){const client=await create(async()=>reply({data}));await assert.rejects(invoke(client,'capabilities'))}
  for(const data of [{...review,workspace_id:'other'},{...review,replacement_id:'other'},{...review,actor_id:'\ud800'},{...review,precondition:proof+'\n'},{...review,replacement_revision:0}]){const client=await create(async()=>reply({data}));await assert.rejects(invoke(client,'review'))}
  for(const data of [{...result,workspace_id:'other'},{...result,actor_id:'other'},{...result,replacement_id:'other'},{...result,actor_role:'admin'},{...result,replacement_role:'viewer'},{...result,actor_revision:4},{...result,replacement_revision:7},{...result,state:'active'}]){const client=await create(async()=>reply({data}));await assert.rejects(invoke(client,'submit'))}
 })
 test(`${mode}: Python identity domain uses codepoints and keeps NUL DEL FEFF without URL widening`,()=>{
  for(const value of ['ordinary:id','😀'.repeat(200),'a\0\u007f\uFEFF','a/b','a\\b','a%2Fb']){
   assert.equal(codec.validPolicyId(value),true);assert.deepEqual(validateMembers([{workspace_id:workspace,principal_id:value,role:'admin',state:'active',revision:1}],workspace)[0].principal_id,value)
  }
  assert.equal(codec.validPolicyId('😀'.repeat(201)),false)
  for(const point of [9,10,11,12,13,28,29,30,31,32,0x85,0xa0,0x1680,...Array.from({length:11},(_,i)=>0x2000+i),0x2028,0x2029,0x202f,0x205f,0x3000])assert.equal(codec.validPolicyId('a'+String.fromCodePoint(point)),false,point.toString(16))
  for(const value of ['','\ud800','\udfff','a\ud800b'])assert.equal(codec.validPolicyId(value),false)
  assert.equal(codec.displayPolicyId('ordinary:id'),'ordinary:id');assert.equal(codec.displayPolicyId('a\0\u007f\uFEFF'),'a\\u{0}\\u{7F}\\u{FEFF}')
  assert.equal(codec.displayPolicyId('\\u{0}'),'\\\\u{0}');assert.notEqual(codec.displayPolicyId('\0'),codec.displayPolicyId('\\u{0}'))
  assert.equal(codec.displayPolicyId('😀'.repeat(200)),'😀'.repeat(200))
  for(const value of ['a/b','a\\b','a%2Fb','a\0']){assert.throws(()=>membershipURL(workspace,value));assert.throws(()=>codec.adminTransferURL(workspace,value))}
 })
 test(`${mode}: only exact recognized 412 error may mark this invocation no-write`,async()=>{
  for(const [status,payload,expected]of [[412,{error:'stale_revision'},true],[412,{error:'admin_transfer_selection_changed'},true],[412,{error:'stale_revision',extra:true},false],[412,{error:'unknown'},false],[409,{error:'stale_revision'},false],[503,{error:'admin_transfer_unavailable'},false]]){
   const client=await create(async()=>reply(payload,status));await assert.rejects(invoke(client,'submit'),e=>e.status===status&&e.adminTransferRejected===expected&&!e.message.includes(payload.error))
  }
  for(const raw of ['malformed','x'.repeat(257)]){const client=await create(async()=>({ok:false,status:412,text:async()=>raw}));await assert.rejects(invoke(client,'submit'),e=>e.adminTransferRejected===false)}
 })
 test(`${mode}: same-session-epoch stale401 cannot invalidate a newer busy transfer`,async()=>{
  for(const kind of ['capabilities','review','submit']){
   const old=deferred(),newer=deferred(),invalid=[];let sequence=0,current=true,newBusy=true
   const client=await create(async()=>++sequence===1?old.promise:newer.promise,message=>invalid.push(message))
   const first=invoke(client,kind,{isCurrent:()=>current});current=false
   const next=invoke(client,'review',{isCurrent:()=>newBusy})
   old.resolve(reply({error:'authentication_required'},401));await assert.rejects(first,e=>e.name==='AbortError');assert.deepEqual(invalid,[]);assert.equal(newBusy,true)
   newer.resolve(reply({data:review}));assert.deepEqual(await next,review);newBusy=false
  }
 })
 test(`${mode}: late body decode and error body are checked after read before return or401 clear`,async()=>{
  for(const status of [200,401,412])for(const malformed of [false,true]){
   const entered=deferred(),body=deferred(),invalid=[];let current=true
   const client=await create(async()=>({ok:status===200,status,text:async()=>{entered.resolve();return body.promise}}),message=>invalid.push(message))
   const pending=invoke(client,'submit',{isCurrent:()=>current});await entered.promise;current=false
   body.resolve(malformed?'private malformed marker':JSON.stringify(status===200?{data:result}:{error:'stale_revision'}))
   await assert.rejects(pending,e=>e.name==='AbortError');assert.deepEqual(invalid,[])
  }
 })
 test(`${mode}: actually current401 clears credentials and reports uncertainty not rollback`,async()=>{
  for(const kind of ['capabilities','review','submit']){
   const invalid=[],client=await create(async()=>reply({error:'authentication_required'},401),message=>invalid.push(message))
   await assert.rejects(invoke(client,kind),e=>e.status===401&&/may already have completed/.test(e.message))
   assert.equal(invalid.length,mode==='Cookie'?1:0);await assert.rejects(client.request('/api/workspaces'),mode==='Cookie'?/Session recheck required/:/Disconnected/)
  }
 })
 test(`${mode}: false and throwing guards after dispatch discard success without exposing payload`,async()=>{
  for(const kind of ['capabilities','review','submit'])for(const throwing of [false,true]){
   const held=deferred();let current=true
   const client=await create(async()=>held.promise),pending=invoke(client,kind,{isCurrent:()=>{if(!current&&throwing)throw Error('private guard marker');return current}})
   current=false;held.resolve(reply(payloadFor(kind)));await assert.rejects(pending,e=>e.name==='AbortError'&&!e.message.includes('private guard marker'))
  }
 })
}
