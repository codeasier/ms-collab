import test from 'node:test'
import assert from 'node:assert/strict'
import {createClient,validateWorkspaces,validateMembers,membershipURL,messageFor,moduleURL,validateModules,validateCatalog} from '../client.mjs'
const space={id:'alpha',name:'Alpha',kind:'product',state:'active',revision:1}
const member={workspace_id:'alpha',principal_id:'reader',role:'viewer',state:'active',revision:1}
test('strict response scope and revisions',()=>{
 assert.deepEqual(validateWorkspaces([space]),[space]);assert.deepEqual(validateMembers([member],'alpha'),[member])
 for(const value of [null,[{...space,id:'../beta'}],[space,space],[{...space,revision:0}]])assert.throws(()=>validateWorkspaces(value))
 for(const value of [[{...member,workspace_id:'beta'}],[member,member],[{...member,role:'owner'}],[{...member,revision:NaN}]])assert.throws(()=>validateMembers(value,'alpha'))
})
test('URL builders reject hostile scope',()=>{
 assert.equal(membershipURL('alpha','reader'),'/api/workspaces/alpha/members/reader')
 for(const bad of ['../beta','https://evil.invalid','a/b','a?x'])assert.throws(()=>membershipURL(bad))
})
test('credential is memory only and requests fixed same-origin with no redirect',async()=>{
 const calls=[];const client=createClient({fetcher:async(...args)=>{calls.push(args);return{ok:true,json:async()=>({data:[member]})}}})
 await assert.rejects(client.request('/api/workspaces'));client.connect('synthetic-only')
 await client.request(membershipURL('alpha','reader'),{method:'PATCH',body:{role:'viewer',state:'revoked'},revision:1})
 const [url,options]=calls[0];assert.ok(url.startsWith('/api/'));assert.equal(options.credentials,'omit');assert.equal(options.redirect,'error');assert.equal(options.cache,'no-store');assert.equal(options.headers['If-Match'],'"1"');assert.equal(options.headers.Authorization,'Bearer synthetic-only')
 client.clear();await assert.rejects(client.request('/api/workspaces'));assert.equal(calls.length,1)
})
test('errors never echo server response body',async()=>{
 const client=createClient({fetcher:async()=>({ok:false,status:401,json:async()=>{throw new Error('must not inspect raw server body')}})})
 client.connect('synthetic-only');await assert.rejects(client.request('/api/workspaces'),{message:messageFor(401),status:401})
})
test('module responses preserve exact scope and fixed capabilities',()=>{
 const module={workspace_id:'alpha',key:'board',capabilities:['read'],state:'disabled',revision:1}
 assert.deepEqual(validateModules([module],'alpha'),[module]);assert.deepEqual(validateCatalog([{key:'board',capabilities:['read']}]),[{key:'board',capabilities:['read']}])
 for(const value of [[{...module,workspace_id:'beta'}],[{...module,capabilities:['execute']}],[module,module],[{...module,state:'running'}]])assert.throws(()=>validateModules(value,'alpha'))
 for(const value of [null,[{key:'../secret',capabilities:['read']}],[{key:'board',capabilities:[]}],[{key:'board',capabilities:['read','read']}]])assert.throws(()=>validateCatalog(value))
 assert.equal(moduleURL('alpha','board'),'/api/workspaces/alpha/modules/board');assert.throws(()=>moduleURL('../beta'))
})
test('administration requests use fixed paths and explicit retry or revision headers',async()=>{
 const requests=[],client=createClient({fetcher:async(path,options)=>{requests.push({path,options});return {ok:true,json:async()=>({data:{}})}}})
 client.connect('opaque-test')
 await client.request('/api/control/workspaces',{method:'POST',body:{name:'Example'},requestKey:'fixture'})
 await client.request(moduleURL('alpha','board'),{method:'PATCH',body:{state:'enabled'},revision:3})
 assert.equal(requests[0].options.headers['Idempotency-Key'],'fixture');assert.equal(requests[0].options.headers['If-Match'],undefined)
 assert.equal(requests[1].options.headers['If-Match'],'"3"');assert.equal(requests[1].options.headers['Idempotency-Key'],undefined)
 for(const path of ['https://evil.example/control/workspaces','//evil.example/x','/api/control/principals'])await assert.rejects(()=>client.request(path))
 await assert.rejects(()=>client.request('/api/control/workspaces',{method:'DELETE'}))
})

test('invitation responses and routes are bounded, scoped, and non-secret',async()=>{
 const {invitationURL,validateInvitations}=await import('../client.mjs')
 const item={id:'invite-1',workspace_id:'alpha',workspace_name:'Alpha',principal_id:'newcomer',role:'viewer',state:'pending',created:100,expires:86500,revision:1}
 assert.deepEqual(validateInvitations([item],'alpha'),[item]);assert.deepEqual(validateInvitations([item]),[item])
 for(const bad of [[{...item,workspace_id:'beta'}],[{...item,role:'admin'}],[{...item,expires:100}],[{...item,state:'active'}],[item,item]])assert.throws(()=>validateInvitations(bad,'alpha'))
 assert.equal(invitationURL(),'/api/invitations');assert.equal(invitationURL(undefined,'invite-1'),'/api/invitations/invite-1')
 assert.equal(invitationURL('alpha','invite-1'),'/api/workspaces/alpha/invitations/invite-1')
 assert.throws(()=>invitationURL('../beta'));assert.throws(()=>invitationURL(undefined,'?secret'))
 const calls=[],client=createClient({fetcher:async(path,options)=>{calls.push({path,options});return{ok:true,json:async()=>({data:item})}}})
 client.connect('synthetic-only');await client.request(invitationURL(),{});await client.request(invitationURL(undefined,item.id),{method:'PATCH',body:{state:'accepted'},revision:1})
 assert.equal(calls[1].options.headers['If-Match'],'"1"');assert.equal(calls[1].options.credentials,'omit');assert.equal(calls[1].options.redirect,'error')
 for(const path of ['/api/invitations?token=secret','/api/invitations/id/accept','https://bad.invalid/api/invitations'])await assert.rejects(()=>client.request(path))
})
