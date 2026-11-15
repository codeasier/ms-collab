import test from 'node:test'
import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {createClient,LatestOperation,scopeFrom,queryFrom,validateFields,validateCapabilities,validateConfig,validateCandidates} from '../client.mjs'
const scope=scopeFrom('alpha','source','sample','project')
const fields=[{id:'ui',name:'UI',kind:'developer',paths:['ui'],owner_ids:['local:alice'],backup:null}]
const common={scope,revision:3,config_version:2,identity_namespace:'ms-collab.principal.id',workflow_enabled:false}
const config={...common,fields}
const caps={...common,capabilities:{read:true,edit:true}}
const row={kind:'issue',number:1,title:'<img src=x onerror=alert(1)>',assignment_status:'unknown',unmapped_paths:[],fields:[],my_field_ids:[],assignee_identity_status:'unknown',review_status:'unverified',approval_available:false,path_evidence:'unavailable',head_sha:'',base_sha:''}
const candidates={...common,mode:'readonly_foundation',review_status:'unverified',approval_available:false,transport_executable:false,external_effect:'not_observed',items:[row],total:1,offset:0,limit:20,view:'all',incomplete_pull_count:0,directory_status:'not_implemented',coverage:'approved_observed_subset',community_revision:5,snapshot_available:true,snapshot_observed_at:'2026-01-01T00:00:00Z',evidence_current:false,evidence_observed_at:null}
const clone=v=>structuredClone(v)

test('scope and field IDs are exact, never routes or provider URLs',()=>{
 for(const value of ['alpha\n','../alpha','https://example.invalid','a/b','a%2Fb','a b'])assert.throws(()=>scopeFrom(value,'source','sample','project'))
 assert.deepEqual(queryFrom('field','ui'),{view:'field',field_id:'ui',offset:0,limit:20})
 assert.throws(()=>queryFrom('field','ui\n'));assert.throws(()=>queryFrom('all','ui'));assert.throws(()=>queryFrom('all','',-1))
})
test('field validation keeps literal paths and local principal identity semantics',()=>{
 assert.equal(validateFields(fields),fields)
 for(const patch of [{id:'ui\n'},{paths:['../x']},{paths:['a//b']},{owner_ids:['local:alice','local:alice']},{kind:'owner'},{backup:{principal_id:'local:alice',field_id:'ui'}},{extra:'not allowed'}])assert.throws(()=>validateFields([{...fields[0],...patch}]))
 assert.doesNotThrow(()=>validateFields([{...fields[0],paths:['wild*','中文/文件']}]))
})
test('capabilities and config bind exact scope and revisions',()=>{
 assert.equal(validateCapabilities(caps,scope),caps);assert.equal(validateConfig(config,scope,caps),config)
 assert.throws(()=>validateCapabilities({...caps,scope:{...scope,workspace_id:'beta'}},scope))
 assert.throws(()=>validateCapabilities({...caps,capabilities:{read:true,edit:'yes'}},scope))
 assert.throws(()=>validateConfig({...config,revision:4},scope,caps));assert.throws(()=>validateConfig({...config,workflow_enabled:true},scope))
})
test('candidate projection fails closed on scope, revision, view and executable claims',()=>{
 const query=queryFrom();assert.equal(validateCandidates(candidates,scope,config,query),candidates)
 for(const patch of [{revision:4},{config_version:1},{view:'mine'},{offset:20},{workflow_enabled:true},{review_status:'approved'},{approval_available:true},{transport_executable:true},{external_effect:'done'},{coverage:'complete_repository'},{scope:{...scope,connection_id:'other'}}])assert.throws(()=>validateCandidates({...candidates,...patch},scope,config,query))
 for(const patch of [{assignment_status:'unassigned'},{path_evidence:'complete_unpinned_observation'},{fields:[{id:'ui',kind:'developer',review_status:'unverified'}]},{head_sha:'a'.repeat(40)},{approval_available:true},{review_status:'verified'}])assert.throws(()=>validateCandidates({...candidates,items:[{...row,...patch}]},scope,config,query))
})
test('complete path observation remains unpinned, matching only configured fields',()=>{
 const pull={...row,kind:'pull',assignment_status:'matched',fields:[{id:'ui',kind:'developer',review_status:'unverified'}],my_field_ids:['ui'],path_evidence:'complete_unpinned_observation',head_sha:'a'.repeat(40),base_sha:'b'.repeat(40)}
 const data={...candidates,evidence_current:true,evidence_observed_at:'2026-01-01T00:00:00Z',items:[pull]}
 assert.doesNotThrow(()=>validateCandidates(data,scope,config,queryFrom()))
 assert.throws(()=>validateCandidates({...data,evidence_current:false,evidence_observed_at:null},scope,config,queryFrom()))
 assert.throws(()=>validateCandidates({...data,items:[{...pull,fields:[{id:'outside',kind:'developer',review_status:'unverified'}]}]},scope,config,queryFrom()))
 assert.throws(()=>validateCandidates({...data,view:'unassigned'},scope,config,queryFrom('unassigned')))
})
test('HTTP is same-origin memory-only bearer and CAS PUT without retry',async()=>{
 const requests=[],client=createClient({fetcher:async(url,options)=>{requests.push({url,options});return {ok:true,text:async()=>JSON.stringify({data:config})}}})
 client.connect('synthetic-only');await client.request(scope,'config');await client.request(scope,'save',{fields,revision:3})
 const read=requests[0],write=requests[1];assert.equal(read.url,'/api/workspaces/alpha/responsibility/source/sample/project/config');assert.equal(write.options.method,'PUT');assert.equal(write.options.headers['If-Match'],'"3"')
 assert.equal(write.options.headers.Authorization,'Bearer synthetic-only');assert.equal(write.options.credentials,'omit');assert.equal(write.options.redirect,'error');assert.equal(write.options.cache,'no-store');assert.deepEqual(JSON.parse(write.options.body),{fields})
 assert.ok(!write.url.includes('synthetic-only'));client.clear();await assert.rejects(client.request(scope,'config'));assert.equal(requests.length,2)
})
test('errors never echo response secrets, conflict is not retried',async()=>{
 let attempts=0;const client=createClient({fetcher:async()=>{attempts++;return {ok:false,status:412,text:async()=>{throw new Error('secret body must not be read')}}}});client.connect('synthetic-only')
 await assert.rejects(client.request(scope,'save',{fields,revision:3}),error=>error.status===412&&error.message.includes('Nothing was retried'))
 assert.equal(attempts,1);assert.throws(()=>client.connect('bad\nvalue'))
})
test('generation fencing survives an ineffective abort and lifecycle reset',()=>{
 const gate=new LatestOperation(),first=gate.begin(),second=gate.begin();assert.equal(first.signal.aborted,true);assert.equal(gate.current(first),false);assert.equal(gate.current(second),true)
 gate.clear();assert.equal(second.signal.aborted,true);assert.equal(gate.current(second),false);const third=gate.begin();assert.equal(gate.current(third),true)
})
test('no unbounded configuration body or extra response envelope',async()=>{
 let calls=0;const client=createClient({fetcher:async()=>{calls++;return {ok:true,text:async()=>JSON.stringify({data:config,extra:'no'})}}});client.connect('synthetic-only')
 const large=Array.from({length:100},(_,i)=>({...fields[0],id:'f'+i,paths:['x'.repeat(1000)]}))
 await assert.rejects(client.request(scope,'save',{fields:large,revision:3}),/64 KiB/);assert.equal(calls,0)
 await assert.rejects(client.request(scope,'config'));assert.equal(calls,1)
})


test('actual isolated HTTP service matches client schemas and CAS save',async()=>{
 const child=spawn('python3',['-m','packages.responsibility_ui.tests.browser_server'],{stdio:['ignore','pipe','pipe']});child.stderr.resume()
 try{
  const origin=await new Promise((resolve,reject)=>{child.stdout.once('data',d=>resolve(d.toString().trim()));child.once('exit',()=>reject(new Error('synthetic fixture stopped')))})
  const client=createClient({fetcher:(url,opts)=>fetch(origin+url,opts)});client.connect('test-admin')
  const caps=validateCapabilities(await client.request(scope,'capabilities'),scope),config=validateConfig(await client.request(scope,'config'),scope,caps)
  for(const view of ['all','mine','field','unassigned']){
   const q=queryFrom(view,view==='field'?'review':'');const data=validateCandidates(await client.request(scope,'candidates',{query:q}),scope,config,q)
   assert.equal(data.total,view==='all'?32:view==='unassigned'?1:30)
  }
  const updated=clone(config.fields);updated[0].name='HTTP client save'
  const saved=validateConfig(await client.request(scope,'save',{fields:updated,revision:config.revision}),scope,{revision:config.revision+1,config_version:config.config_version+1})
  assert.deepEqual(saved.fields,updated)
 }finally{child.kill();await new Promise(resolve=>{if(child.exitCode!==null)resolve();else child.once('exit',resolve)})}
})
