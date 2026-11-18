import test from 'node:test'
import assert from 'node:assert/strict'
import {createSessionClient,moduleStatusURL,validModuleStatusId,validateModuleStatus} from '../client.mjs'

const record=(workspace='alpha',key='pipelines',state='disabled',revision=1)=>({workspace_id:workspace,key,state,revision})
const response=(data,status=200)=>({ok:status>=200&&status<300,status,text:async()=>JSON.stringify({data})})
const rawResponse=raw=>({ok:true,status:200,text:async()=>raw})
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}
const abortError=error=>error.name==='AbortError'&&error.message==='Stale module status request discarded.'
async function fixture(handler=()=>response(record())){
 const calls=[],invalid=[]
 const client=createSessionClient({cookie:()=>{throw Error('GET must not read CSRF')},onInvalid:value=>invalid.push(value),fetcher:async(path,options)=>{
  calls.push({path,options})
  return path==='/auth/session'?response({principal_id:'reader'}):handler(path,options)
 }})
 await client.inspect()
 return {client,calls,invalid}
}

test('module status identifiers preserve case, punctuation and the exact 200-character bound',()=>{
 for(const id of ['a','0','Team.A_:-9','A'+'z'.repeat(199)]){
  assert.equal(validModuleStatusId(id),true)
  assert.equal(moduleStatusURL(id,id),`/api/workspaces/${encodeURIComponent(id)}/modules/${encodeURIComponent(id)}`)
 }
 for(const id of ['',null,1,{},'a'.repeat(201),' a','a ','a\n','a\r','a\t','a\0','a\x7f','a/b','a\\b','a%2Fb','a?x=1','a#x','a+b','.a','-a','_a',':a','é','a\u00a0']){
  assert.equal(validModuleStatusId(id),false)
  assert.throws(()=>moduleStatusURL(id,'pipelines'))
  assert.throws(()=>moduleStatusURL('alpha',id))
 }
})

test('explicit module status uses one exact authenticated GET with no mutation or provider options',async()=>{
 const workspace='Team.A_:-9',key='Module.A_:-9'.padEnd(200,'z'),signal=new AbortController().signal
 assert.equal(key.length,200)
 const h=await fixture(()=>response(record(workspace,key,'enabled',Number.MAX_SAFE_INTEGER)))
 assert.deepEqual(await h.client.readModuleStatus(workspace,key,{signal,isCurrent:()=>true}),record(workspace,key,'enabled',Number.MAX_SAFE_INTEGER))
 assert.equal(h.calls.length,2)
 const {path,options}=h.calls[1]
 assert.equal(path,moduleStatusURL(workspace,key));assert.equal(path.includes('?'),false)
 assert.equal(options.method,'GET');assert.equal(options.credentials,'same-origin');assert.equal(options.cache,'no-store');assert.equal(options.redirect,'error');assert.equal(options.signal,signal)
 assert.equal(options.body,undefined);assert.deepEqual(options.headers,{})
 assert.equal(options.headers.Authorization,undefined);assert.equal(options.headers['X-CSRF-Token'],undefined);assert.equal(options.headers['If-Match'],undefined);assert.equal(options.headers['Idempotency-Key'],undefined)
 assert.equal(h.invalid.length,0)
})

test('invalid module status arguments, guards and options fail before HTTP',async()=>{
 const h=await fixture()
 for(const id of ['',null,4,'a'.repeat(201),' alpha','alpha ','alpha\n','a/b','a%20b','a?x=1','a#x']){
  await assert.rejects(h.client.readModuleStatus(id,'pipelines',{isCurrent:()=>true}))
  await assert.rejects(h.client.readModuleStatus('alpha',id,{isCurrent:()=>true}))
 }
 for(const options of [undefined,null,[],()=>{}, {},{isCurrent:undefined},{isCurrent:true},{isCurrent:null},{isCurrent:()=>true,signal:{}},{isCurrent:()=>true,signal:null},Object.create({isCurrent:()=>true})])await assert.rejects(h.client.readModuleStatus('alpha','pipelines',options))
 for(const name of ['path','query','body','method','headers','revision','requestKey','initialAdminPrecondition','invitationRecipientPrecondition','invitationIsCurrent','login','authenticated','bearer','isCurrentModuleStatus','moduleStatus',Symbol('private')])await assert.rejects(h.client.readModuleStatus('alpha','pipelines',{isCurrent:()=>true,[name]:undefined}))
 assert.equal(h.calls.length,1);assert.equal(h.invalid.length,0)
 h.client.clear();await assert.rejects(h.client.readModuleStatus('alpha','pipelines',{isCurrent:()=>true}),/Session recheck required/);assert.equal(h.calls.length,1)
})

test('status projection accepts only the exact requested tuple, tri-state and positive safe revision',()=>{
 for(const state of ['enabled','disabling','disabled'])for(const revision of [1,Number.MAX_SAFE_INTEGER]){
  const data=record('Team:1','Pipelines.v2',state,revision)
  assert.equal(validateModuleStatus(data,'Team:1','Pipelines.v2'),data)
 }
 const valid=record()
 const invalid=[null,[],[valid],{},'disabled',{...valid,capabilities:[]},{...valid,config:{}},{...valid,metadata:{}},{...valid,workspace_id:'beta'},{...valid,key:'Pipelines'},...['ready','Enabled','',null,1].map(state=>({...valid,state})),...[0,-1,1.5,Number.MAX_SAFE_INTEGER+1,'1',null,Infinity,NaN].map(revision=>({...valid,revision}))]
 for(const field of Object.keys(valid)){const data={...valid};delete data[field];invalid.push(data)}
 for(const data of invalid)assert.throws(()=>validateModuleStatus(data,'alpha','pipelines'))
})

test('transport rejects old full instances, malformed envelopes and non-200 success without fallback',async()=>{
 const valid=record(),extra={...valid,capabilities:['read','write']}
 const invalid=[response(extra),response(null),response([]),response({...valid,workspace_id:'beta'}),response({...valid,key:'other'}),response({...valid,state:'ready'}),response({...valid,revision:0}),response(valid,201),response(valid,204),rawResponse('{secret invalid JSON'),rawResponse('null'),rawResponse('[]'),rawResponse(JSON.stringify(valid)),rawResponse(JSON.stringify({data:valid,capabilities:[]})),rawResponse(JSON.stringify({data:valid,error:'private'})),rawResponse(' '.repeat(2097153))]
 for(const result of invalid){
  const h=await fixture(()=>result)
  await assert.rejects(h.client.readModuleStatus('alpha','pipelines',{isCurrent:()=>true}))
  assert.deepEqual(h.calls.map(call=>call.path),['/auth/session','/api/workspaces/alpha/modules/pipelines']);assert.equal(h.invalid.length,0)
 }
 const unavailable=await fixture(()=>({ok:false,status:404,text:()=>{throw Error('error body must not be read')}}))
 await assert.rejects(unavailable.client.readModuleStatus('alpha','pipelines',{isCurrent:()=>true}),error=>error.status===404)
 assert.equal(unavailable.calls.length,2);assert.equal(unavailable.invalid.length,0)
})

for(const kind of ['200','409','401','malformed','body rejection','fetch rejection'])test(`same-epoch obsolete ${kind} cannot invalidate a newer held status request`,async()=>{
 const old=deferred(),newer=deferred();let current=true,count=0
 const h=await fixture(()=>++count===1?old.promise:newer.promise)
 const prior=h.client.readModuleStatus('alpha','pipelines',{isCurrent:()=>current})
 const rejected=assert.rejects(prior,abortError)
 current=false
 const next=h.client.readModuleStatus('beta','pipelines',{isCurrent:()=>true})
 let completed=false;next.then(()=>{completed=true})
 if(kind==='fetch rejection')old.reject(Error('private fetch URL'))
 else if(kind==='body rejection')old.resolve({ok:true,status:200,text:async()=>{throw Error('private body text')}})
 else if(kind==='malformed')old.resolve(rawResponse('{private malformed'))
 else old.resolve(response(record(),Number(kind)))
 await rejected
 assert.equal(completed,false);assert.equal(h.invalid.length,0);assert.equal(h.calls.length,3)
 newer.resolve(response(record('beta','pipelines','disabling',2)))
 assert.deepEqual(await next,record('beta','pipelines','disabling',2));assert.equal(h.invalid.length,0)
})

for(const kind of ['valid','malformed','body rejection'])test(`invalidation during asynchronous body reading discards ${kind} without exposing it`,async()=>{
 const body=deferred(),reading=deferred();let current=true
 const h=await fixture(()=>({ok:true,status:200,text:()=>{reading.resolve();return body.promise}}))
 const pending=h.client.readModuleStatus('alpha','pipelines',{isCurrent:()=>current})
 const rejected=assert.rejects(pending,abortError)
 await reading.promise;current=false
 if(kind==='body rejection')body.reject(Error('private response payload'))
 else body.resolve(kind==='valid'?JSON.stringify({data:record()}):'{private malformed payload')
 await rejected;assert.equal(h.invalid.length,0)
})

for(const phase of [1,2,3,4,5])for(const result of ['false','nonboolean','throw'])test(`status guard fails closed with a value-free AbortError at boundary ${phase}: ${result}`,async()=>{
 let checks=0
 const h=await fixture()
 const isCurrent=()=>{if(++checks<phase)return true;if(result==='throw')throw Error('private guard value');return result==='false'?false:'true'}
 await assert.rejects(h.client.readModuleStatus('alpha','pipelines',{isCurrent}),abortError)
 assert.equal(h.invalid.length,0);assert.equal(h.calls.length,phase===1?1:2)
})

test('session epoch change fences a status even if its local guard incorrectly remains true',async()=>{
 const held=deferred(),h=await fixture(()=>held.promise)
 const pending=h.client.readModuleStatus('alpha','pipelines',{isCurrent:()=>true}),rejected=assert.rejects(pending,abortError)
 h.client.clear();held.resolve(response(record(),401));await rejected
 assert.equal(h.invalid.length,0)
})

test('current status 401 keeps ordinary invalidation and requires an explicit session inspection',async()=>{
 const h=await fixture(()=>({ok:false,status:401,text:()=>{throw Error('must not decode private errors')}}))
 await assert.rejects(h.client.readModuleStatus('alpha','pipelines',{isCurrent:()=>true}),error=>error.status===401)
 assert.equal(h.invalid.length,1)
 await assert.rejects(h.client.request('/api/workspaces'),/Session recheck required/)
 await assert.rejects(h.client.readModuleStatus('beta','pipelines',{isCurrent:()=>true}),/Session recheck required/)
 assert.equal(h.calls.length,2)
 await h.client.inspect();assert.equal(h.calls.length,3)
})

test('public request option names cannot install the private status guard or suppress a genuine 401',async()=>{
 for(const name of ['isCurrent','guard','moduleStatus','moduleStatusContext','statusContext']){
  const h=await fixture(()=>response({},401))
  const value=name==='isCurrent'||name==='guard'?()=>false:{workspace:'alpha',key:'pipelines',path:'/api/workspaces/alpha/modules/pipelines',isCurrent:()=>false}
  await assert.rejects(h.client.request('/api/workspaces/alpha/modules/pipelines',{[name]:value}),error=>error.status===401)
  assert.equal(h.invalid.length,1);await assert.rejects(h.client.request('/api/workspaces'),/Session recheck required/);assert.equal(h.calls.length,2)
 }
})
