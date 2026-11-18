// Shared invitation-only contracts, registered from both executed Node roots.
import test from 'node:test'
import assert from 'node:assert/strict'
import {invitationSelectionURL,validateInvitationSelection,validInvitationRecipientPrecondition} from '../client.mjs'
const proof='irs1-'+'a'.repeat(64),workspace='space:1',principal='person:1',role='viewer'
const selection={workspace_id:workspace,principal_id:principal,role,precondition:proof}
const path=invitationSelectionURL(workspace,principal,role),collection='/api/workspaces/space%3A1/invitations',body={principal_id:principal,role}
const item={id:'invitation-1',workspace_id:workspace,workspace_name:'Synthetic',principal_id:principal,role,state:'pending',created:10,expires:86410,revision:1}
const reply=(payload,status=200)=>({ok:status===200,status,text:async()=>JSON.stringify(payload),json:async()=>payload})
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve}}
export function invitationClientCases(mode,create){
 test(`${mode}: invitation review and create retain exact tuple, header and body`,async()=>{
  const calls=[],client=await create(async(p,o)=>{calls.push([p,o]);return reply({data:p===path?selection:item})})
  assert.deepEqual(await client.request(path),selection)
  for(const options of [{invitationRecipientPrecondition:proof},{}])assert.deepEqual(await client.request(collection,{method:'POST',body,requestKey:'original-key',...options}),item)
  assert.equal(calls[0][1].body,undefined);assert.equal(calls[0][1].headers['X-CSRF-Token'],undefined)
  assert.equal(calls[1][1].headers['If-Invitation-Recipient-Match'],proof);assert.equal(calls[2][1].headers['If-Invitation-Recipient-Match'],undefined)
  assert.equal(calls[1][1].headers['Idempotency-Key'],'original-key');assert.deepEqual(JSON.parse(calls[1][1].body),body)
  for(const [,options]of calls){assert.equal(options.credentials,mode==='Cookie'?'same-origin':'omit');assert.equal(options.cache,'no-store');assert.equal(options.redirect,'error');assert.equal(options.headers.invitationIsCurrent,undefined)}
  if(mode==='Cookie'){assert.equal(calls[1][1].headers.Authorization,undefined);assert.equal(calls[1][1].headers['X-CSRF-Token'],'A'.repeat(43))}
  else assert.equal(calls[1][1].headers.Authorization,'Bearer synthetic-only')
 })
 test(`${mode}: invitation canonical path, bounded body and option isolation reject before fetch`,async()=>{
  let calls=0;const client=await create(async()=>{calls++;throw Error('must not fetch')})
  for(const target of ['/api/workspaces/space:1/invitations/selections/person%3A1/viewer','/api/workspaces/%73pace/invitations/selections/person/viewer','/api/workspaces/space%3A1/invitations/selections/person:1/viewer',path+'?x=1',path+'#x',path+'/extra',path.replace('person%3A1','person%253A1'),path.replace('person%3A1','a%2Fb'),path.replace('/viewer','/admin'),'/api/workspaces/space%3A1/invitations/selections'])await assert.rejects(client.request(target))
  for(const options of [{method:'POST'},{method:'HEAD'},{body:{}},{invitationRecipientPrecondition:proof},{invitationIsCurrent:true}])await assert.rejects(client.request(path,options))
  for(const other of ['/api/workspaces','/api/workspaces/alpha/members','/api/workspaces/alpha/invitations/id','/api/control/workspaces']){
   await assert.rejects(client.request(other,{method:'POST',body,invitationRecipientPrecondition:proof}))
   await assert.rejects(client.request(other,{invitationIsCurrent:()=>true}))
  }
  for(const bad of [null,{},[],{...body,extra:true},{...body,role:'admin'},{...body,principal_id:'x'.repeat(201)}])await assert.rejects(client.request(collection,{method:'POST',body:bad}))
  for(const bad of [null,1,'',proof+'\n',proof+'\0',proof.toUpperCase(),' '+proof,'"'+proof+'"',proof+','+proof])await assert.rejects(client.request(collection,{method:'POST',body,invitationRecipientPrecondition:bad}))
  assert.equal(calls,0)
 })
 test(`${mode}: exact review and successful invitation envelopes must match their request`,async()=>{
  const long='p'.repeat(200);assert.equal(invitationSelectionURL('alpha',long,'developer'),'/api/workspaces/alpha/invitations/selections/'+long+'/developer')
  assert.deepEqual(validateInvitationSelection(selection,workspace,principal,role),selection)
  for(const bad of ['',proof+'\n',proof+'\r',proof+'\0',proof.toUpperCase(),false,proof+',x'])assert.equal(validInvitationRecipientPrecondition(bad),false)
  for(const data of [null,[],{...selection,extra:true},{...selection,workspace_id:'other'},{...selection,principal_id:'other'},{...selection,role:'developer'},{...selection,precondition:proof+'\n'}]){
   const client=await create(async()=>reply({data}));await assert.rejects(client.request(path))
  }
  for(const status of [201,202]){const client=await create(async()=>({...reply({data:selection}),status}));await assert.rejects(client.request(path))}
  for(const payload of [{data:selection,extra:true},{result:selection}]){const client=await create(async()=>reply(payload));await assert.rejects(client.request(path))}
  for(const data of [{...item,extra:true},{...item,workspace_id:'other'},{...item,principal_id:'other'},{...item,role:'developer'},{...item,state:'active'},{...item,expires:10}]){
   const client=await create(async()=>reply({data}));await assert.rejects(client.request(collection,{method:'POST',body}))
  }
  // Response binding snapshots the dispatched body, not a caller's later edits.
  const gate=deferred(),mutable={...body},snapshot=await create(async()=>gate.promise)
  const pending=snapshot.request(collection,{method:'POST',body:mutable});mutable.principal_id='other';gate.resolve(reply({data:item}));assert.deepEqual(await pending,item)
  // Historical accepted/invalidated responses are valid results, never a regrant.
  for(const state of ['accepted','invalidated']){const client=await create(async()=>reply({data:{...item,state}}));assert.equal((await client.request(collection,{method:'POST',body})).state,state)}
 })
 test(`${mode}: only exact POST 412 body marks a definitive no-write selection rejection`,async()=>{
  for(const [status,payload,expected]of [[412,{error:'invitation_recipient_selection_changed'},true],[409,{error:'invitation_recipient_selection_changed'},undefined],[400,{error:'invitation_recipient_selection_changed'},undefined],[428,{error:'invitation_recipient_selection_changed'},undefined],[412,{error:'other'},false],[412,{error:'invitation_recipient_selection_changed',extra:true},false],[412,{data:'invitation_recipient_selection_changed'},false]]){
   const client=await create(async()=>reply(payload,status));await assert.rejects(client.request(collection,{method:'POST',body}),e=>e.status===status&&e.invitationSelectionRejected===expected)
  }
  for(const text of ['not-json','x'.repeat(129)]){const client=await create(async()=>({ok:false,status:412,text:async()=>text}));await assert.rejects(client.request(collection,{method:'POST',body}),e=>e.invitationSelectionRejected===false)}
  const unreadable=await create(async()=>({ok:false,status:412,text:async()=>{throw Error('unreadable')}}));await assert.rejects(unreadable.request(collection,{method:'POST',body}),e=>e.invitationSelectionRejected===false)
  const review=await create(async()=>reply({error:'invitation_recipient_selection_changed'},412));await assert.rejects(review.request(path),e=>e.invitationSelectionRejected===undefined)
 })
 test(`${mode}: late invitation GET/POST success, error and 401 are inert before invalidation`,async()=>{
  for(const requestPath of [path,collection])for(const status of [200,409,401]){
   const gate=deferred(),invalid=[],calls=[];let current=true
   const client=await create(async(p)=>{calls.push(p);return p==='/api/workspaces'?reply({data:[]}):gate.promise},message=>invalid.push(message))
   const pending=client.request(requestPath,{...(requestPath===collection?{method:'POST',body}:{}),invitationIsCurrent:()=>current})
   current=false;gate.resolve(reply(status===200?{data:requestPath===path?selection:item}:{error:'authentication_required'},status))
   await assert.rejects(pending,e=>e.name==='AbortError');assert.deepEqual(invalid,[])
   await client.request('/api/workspaces');assert.equal(calls.length,2)
  }
 })
 test(`${mode}: stale decoded success and exact-412 bodies cannot affect a newer request`,async()=>{
  for(const status of [200,412])for(const malformed of [false,true]){
   const decoding=deferred(),entered=deferred();let current=true
   const client=await create(async()=>({ok:status===200,status,text:async()=>{entered.resolve();return decoding.promise},json:async()=>{entered.resolve();return JSON.parse(await decoding.promise)}}))
   const pending=client.request(collection,{method:'POST',body,invitationIsCurrent:()=>current});await entered.promise;current=false
   decoding.resolve(malformed?'malformed':JSON.stringify(status===200?{data:item}:{error:'invitation_recipient_selection_changed'}))
   await assert.rejects(pending,e=>e.name==='AbortError')
  }
 })
 test(`${mode}: actually current invitation 401 remains an authentication failure`,async()=>{
  const invalid=[],client=await create(async()=>reply({error:'authentication_required'},401),message=>invalid.push(message))
  await assert.rejects(client.request(path,{invitationIsCurrent:()=>true}),e=>e.status===401)
  assert.equal(invalid.length,mode==='Cookie'?1:0)
  if(mode==='Cookie')await assert.rejects(client.request('/api/workspaces'),/Session recheck required/)
 })
}
