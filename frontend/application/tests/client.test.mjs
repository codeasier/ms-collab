import test from 'node:test'
import assert from 'node:assert/strict'
import {csrfCookie,validateBootstrap,authorizationTarget,createSessionClient,createResponsibilitySessionClient} from '../client.mjs'
const csrf='A'.repeat(43),origin='https://app.example.invalid'
const response=(data,status=200)=>({ok:status===200,status,text:async()=>JSON.stringify({data})})
const bootstrap={service:'ms-collab',ui_modules:['workspaces','responsibility'],login_configured:true,authorization_endpoint:'https://identity.example.invalid/authorize'}
function fixture(){const calls=[];let raw='__Host-ms-collab-csrf='+csrf;const client=createSessionClient({cookie:()=>raw,fetcher:async(path,options)=>{calls.push({path,options});return response(path==='/auth/session'?{principal_id:'admin'}:{ok:true})}});return {client,calls,cookie:value=>raw=value}}
test('only the exact readable CSRF cookie is accepted, never session credentials',()=>{
 assert.equal(csrfCookie('other=x; __Host-ms-collab-csrf='+csrf),csrf)
 for(const raw of ['', '__Host-ms-collab-session='+csrf,'__Host-ms-collab-csrf='+csrf+'; __Host-ms-collab-csrf='+csrf,'__Host-ms-collab-csrf='+csrf+'\n','__Host-ms-collab-csrf=bad','x'.repeat(9000)])assert.throws(()=>csrfCookie(raw))
})
test('session API uses same-origin cookies, current CSRF and no bearer or retry',async()=>{
 const {client,calls,cookie}=fixture();await assert.rejects(client.request('/api/workspaces'))
 await client.inspect();await client.request('/api/workspaces');await client.request('/api/workspaces/alpha/members/reader',{method:'PATCH',body:{role:'developer',state:'active'},revision:2,requestKey:'synthetic-key'})
 cookie('__Host-ms-collab-csrf='+'B'.repeat(43));await client.request('/api/workspaces/alpha/responsibility/source/sample/project/config',{method:'PUT',body:{fields:[]},revision:1})
 for(const call of calls){assert.equal(call.options.credentials,'same-origin');assert.equal(call.options.cache,'no-store');assert.equal(call.options.redirect,'error');assert.ok(!Object.hasOwn(call.options.headers,'Authorization'));assert.ok(!call.path.includes(csrf))}
 assert.equal(calls[2].options.headers['X-CSRF-Token'],csrf);assert.equal(calls[2].options.headers['If-Match'],'"2"');assert.equal(calls[2].options.headers['Idempotency-Key'],'synthetic-key');assert.equal(calls[3].options.headers['X-CSRF-Token'],'B'.repeat(43))
 client.clear();await assert.rejects(client.request('/api/workspaces'));assert.equal(calls.length,4)
})
test('malformed/missing CSRF, bad routes and body bounds do not send mutations',async()=>{
 const {client,calls,cookie}=fixture();await client.inspect();cookie('')
 for(const path of ['/api/workspaces','//other.invalid/api','/auth/start','https://other.invalid/api','/api/x\n','/api/a/../b'])await assert.rejects(client.request(path,{method:'POST',body:{}}))
 cookie('__Host-ms-collab-csrf='+csrf);await assert.rejects(client.request('/api/workspaces',{method:'POST',body:{x:'x'.repeat(66000)}}));assert.equal(calls.length,1)
})
test('login is explicit, supports anonymous start and never fabricates CSRF',async()=>{
 const {client,calls,cookie}=fixture();cookie('');await client.start();assert.deepEqual(calls[0].options.headers,{'Content-Type':'application/json','X-MS-Collab-Login':'1'})
 cookie('__Host-ms-collab-csrf='+csrf);await client.start();assert.equal(calls[1].options.headers['X-CSRF-Token'],csrf)
 cookie('__Host-ms-collab-csrf=bad');await assert.rejects(client.start());assert.equal(calls.length,2)
})
test('logout can revoke the current server cookie after private page state is cleared',async()=>{
 const {client,calls}=fixture();await client.inspect();client.clear();await client.logout();assert.equal(calls[1].path,'/auth/logout');assert.equal(calls[1].options.headers['X-CSRF-Token'],csrf)
})
test('401 clears client authority and exposes only a fixed safe error',async()=>{
 let count=0;const messages=[];const client=createSessionClient({onInvalid:s=>messages.push(s),fetcher:async()=>++count===1?response({principal_id:'admin'}):{ok:false,status:401,text:()=>{throw new Error('must not read raw error')}}});await client.inspect();await assert.rejects(client.request('/api/workspaces'),e=>e.status===401);await assert.rejects(client.request('/api/workspaces'));assert.equal(count,2);assert.equal(messages.length,1)
})
test('late inspection or data cannot reactivate a cleared page',async()=>{
 let resolve;const client=createSessionClient({fetcher:()=>new Promise(r=>resolve=r)});const pending=client.inspect();client.clear();resolve(response({principal_id:'admin'}));await assert.rejects(pending,/Stale/);await assert.rejects(client.request('/api/workspaces'))
})
test('session and envelope validation reject actor claims and extra fields',async()=>{
 for(const data of [{principal_id:'admin',role:'operator'},{principal_id:'admin\n'},{principal_id:1}]){const client=createSessionClient({fetcher:async()=>response(data)});await assert.rejects(client.inspect());await assert.rejects(client.request('/api/workspaces'))}
 const client=createSessionClient({fetcher:async()=>({ok:true,text:async()=>JSON.stringify({data:{principal_id:'admin'},token:'not permitted'})})});await assert.rejects(client.inspect())
})
test('public bootstrap is bounded configuration, not authorization',()=>{
 assert.equal(validateBootstrap(bootstrap,origin),bootstrap)
 for(const patch of [{ui_modules:['workspaces','unknown']},{login_configured:false},{authorization_endpoint:'http://identity.example.invalid/authorize'},{authorization_endpoint:'https://user:pass@identity.example.invalid/authorize'}])assert.throws(()=>validateBootstrap({...bootstrap,...patch},origin))
 assert.doesNotThrow(()=>validateBootstrap({...bootstrap,login_configured:false,authorization_endpoint:null},origin));assert.throws(()=>validateBootstrap(bootstrap,'http://127.0.0.1'))
})
test('login navigation binds exact configured endpoint and OAuth callback fields',()=>{
 const params={response_type:'code',client_id:'synthetic',redirect_uri:origin+'/auth/callback',state:csrf,code_challenge:'B'.repeat(43),code_challenge_method:'S256',scope:'synthetic_user'}
 const url=bootstrap.authorization_endpoint+'?'+new URLSearchParams(params),data={authorization_url:url};assert.equal(authorizationTarget(data,bootstrap,origin),url)
 for(const value of [url.replace('identity.example.invalid','other.invalid'),url+'&state='+csrf,url+'#fragment',url.replace('S256','plain'),url.replace('auth%2Fcallback','other%2Fcallback'),url+'&return_to=https://other.invalid'])assert.throws(()=>authorizationTarget({authorization_url:value},bootstrap,origin))
 assert.throws(()=>authorizationTarget(data,{...bootstrap,login_configured:false},origin))
})
test('responsibility adapter preserves scope/CAS semantics without a credential adapter',async()=>{
 const calls=[],shared={clear(){},request:async(path,options)=>{calls.push({path,options});return {}}},client=createResponsibilitySessionClient(shared)
 const scope={workspace_id:'alpha',connection_id:'source',repository:'sample/project'}
 await client.request(scope,'candidates',{query:{view:'field',field_id:'review',offset:20,limit:20}});await client.request(scope,'save',{revision:3,fields:[]})
 assert.equal(calls[0].path,'/api/workspaces/alpha/responsibility/source/sample/project/candidates?view=field&offset=20&limit=20&field_id=review');assert.deepEqual(calls[1].options,{signal:undefined,method:'PUT',body:{fields:[]},revision:3})
 assert.equal(Object.hasOwn(client,'connect'),false);await assert.rejects(client.request({...scope,repository:'sample/project/other'},'config'))
})
