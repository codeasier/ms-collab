import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import {createArtifactDirectoryClient,validateArtifactScopes} from '../artifact-scope-client.mjs'
import {createSessionClient} from '../client.mjs'
const source=fs.readFileSync(new URL('../artifact-scopes.mjs',import.meta.url),'utf8').replace("'/manage/client.mjs'",JSON.stringify(new URL('../../workspace-admin/client.mjs',import.meta.url).href)).replace("'./artifact-scope-client.mjs'",JSON.stringify(new URL('../artifact-scope-client.mjs',import.meta.url).href))
const {mountArtifactScopePicker}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'))
const receipt=n=>'dcr1_'+Buffer.alloc(32,n).toString('base64url')
const spaces=['alpha','beta'].map(id=>({id,name:id+' <img src=x>',kind:'product',state:'active',revision:1}))
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}}
const scope={workspace_id:'alpha',connection_id:'source',bucket_id:'bucket'}
const item={scope,catalog_revision:1},data={workspace_id:'alpha',items:[item],next_after:null}
const paged=(managed=false,start=0,next=receipt(1))=>({...data,items:Array.from({length:25},(_,i)=>({scope:{...scope,bucket_id:`b${String(i+start).padStart(3,'0')}`},...(managed?{capabilities:{refresh:true,review:true,approve:true}}:{catalog_revision:1})})),next_after:next})
const response=(data,status=200)=>({ok:status===200,status,text:async()=>JSON.stringify({data})})
test('reader accepts empty publication revision, but never candidate revision or raw metadata',()=>{
 assert.deepEqual(validateArtifactScopes(data,'alpha'),data)
 for(const extra of [{revision:2},{candidate:[]},{credential_ref:'secret'},{published:null}])assert.throws(()=>validateArtifactScopes({...data,items:[{...item,...extra}]},'alpha'))
 for(const v of [0,-1,1.5,Number.MAX_SAFE_INTEGER+1])assert.throws(()=>validateArtifactScopes({...data,items:[{...item,catalog_revision:v}]},'alpha'))
})
test('exact workspace, shape, safe IDs, ordered unique tuples and usable capability matrix',()=>{
 for(const changed of [{...data,workspace_id:'beta'},{...data,total:3},{...data,items:[item,item]},{...data,items:[{...item,scope:{...scope,bucket_id:'bucket\n'}}]}])assert.throws(()=>validateArtifactScopes(changed,'alpha'))
 for(const capabilities of [{refresh:false,review:false,approve:true},{refresh:false,review:false,approve:false},{refresh:1,review:true,approve:true}])assert.throws(()=>validateArtifactScopes({...data,items:[{scope,capabilities}]},'alpha',true))
 for(const capabilities of [{refresh:true,review:false,approve:false},{refresh:false,review:true,approve:false},{refresh:false,review:true,approve:true}])assert.equal(validateArtifactScopes({...data,items:[{scope,capabilities}]},'alpha',true).items.length,1)
})
test('both projections require canonical opaque receipts and independently ordered local tuples',()=>{
 for(const managed of [false,true]){
  const page=paged(managed),last=['source','b024'],next=paged(managed,25,receipt(2))
  assert.equal(validateArtifactScopes(page,'alpha',managed),page);assert.equal(validateArtifactScopes(next,'alpha',managed,receipt(1),last),next)
  for(const changed of [{...page,next_after:'source~b024'},{...page,items:page.items.slice(1)},{...page,items:[...page.items,page.items[0]]},{...page,items:[...page.items].reverse()}])assert.throws(()=>validateArtifactScopes(changed,'alpha',managed))
  for(const after of ['source~b024','invalid',false,3,receipt(1)+'\n',receipt(1)+'=',receipt(1).slice(0,-1),receipt(1).slice(0,-1)+'B','dcr2_'+receipt(1).slice(5),'dcr1_'+'.'.repeat(43),'dcr1_'+'é'.repeat(43)]){
   assert.throws(()=>validateArtifactScopes(next,'alpha',managed,after,last));assert.throws(()=>validateArtifactScopes({...page,next_after:after},'alpha',managed))
  }
  for(const tail of 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-'){
   const candidate='dcr1_'+'A'.repeat(42)+tail
   if('AEIMQUYcgkosw048'.includes(tail))assert.equal(validateArtifactScopes({...page,next_after:candidate},'alpha',managed).next_after,candidate)
   else assert.throws(()=>validateArtifactScopes({...page,next_after:candidate},'alpha',managed))
  }
  assert.throws(()=>validateArtifactScopes({...next,next_after:receipt(1)},'alpha',managed,receipt(1),last))
  for(const prior of [null,new Array(2),[],['source'],['source','b024','extra'],['source','b024\n'],['source','b025'],['source','z']])assert.throws(()=>validateArtifactScopes(next,'alpha',managed,receipt(1),prior))
  assert.throws(()=>validateArtifactScopes(page,'alpha',managed,null,last));assert.throws(()=>validateArtifactScopes(page,'alpha',managed,receipt(1),last))
 }
})
test('cookie-only GET adapter, no configuration/body/CSRF, latest session invalidation and no retries',async()=>{
 const calls=[],values=[response({principal_id:'reader'}),response(data)]
 const session=createSessionClient({fetcher:async(path,options)=>{calls.push({path,options});return values.shift()},cookie:()=>''})
 await session.inspect();const directory=createArtifactDirectoryClient(session);await directory.list('alpha')
 assert.equal(calls[1].path,'/api/workspaces/alpha/artifacts/scopes');assert.equal(calls[1].options.method,'GET');assert.deepEqual(calls[1].options.headers,{});assert.equal(calls[1].options.credentials,'same-origin');assert.equal(calls[1].options.cache,'no-store')
 values.push(response({},409));await assert.rejects(directory.list('alpha',{after:receipt(1),lastTuple:['source','bucket']}));assert.equal(calls.at(-1).path,'/api/workspaces/alpha/artifacts/scopes?after='+receipt(1));assert.equal(calls.length,3)
 for(const workspace of ['alpha/../beta','alpha\n','https://other.invalid'])await assert.rejects(directory.list(workspace));assert.equal(calls.length,3)
 let release;values.push(new Promise(r=>release=r));const pending=directory.list('alpha');session.clear();release(response(data));await assert.rejects(pending,/Stale session/)
})
test('admin and reader endpoints are distinct, configured by trusted page not response',async()=>{
 const calls=[],session={request:async path=>{calls.push(path);return {...data,items:[]}}}
 await createArtifactDirectoryClient(session,true).list('alpha');assert.deepEqual(calls,['/api/workspaces/alpha/artifact-admin/scopes'])
})

function harness(managed=false){
 class Node{
  constructor(tag){this.tag=tag;this.children=[];this.handlers={};this.value='';this.disabled=false;this.hidden=false;this._text=''}
  set textContent(v){this._text=String(v);this.children=[]}get textContent(){return this._text+this.children.map(n=>n.textContent).join('')}
  append(n){this.children.push(n);if(this.tag==='select'&&this.children.length===1)this.value=n.value}
  replaceChildren(){this.children=[];this.value='';this._text=''}
  addEventListener(name,fn){(this.handlers[name]??=[]).push(fn)}
  dispatchEvent(e){for(const fn of this.handlers[e.type]??[])fn(e)}
  requestSubmit(){this.dispatchEvent({type:'submit',preventDefault(){}})}
 }
 const ids=['artifact-picker','artifact-picker-workspace','artifact-picker-scope','artifact-picker-load','artifact-picker-use','artifact-picker-next','artifact-picker-status','scope-form','workspace','connection','bucket']
 const nodes=new Map(ids.map(id=>[id,new Node(['artifact-picker-workspace','artifact-picker-scope'].includes(id)?'select':'div')]))
 const get=id=>nodes.get(id),doc={getElementById:get,createElement:tag=>new Node(tag)},calls=[],changes=[]
 let opened=0,reply=async p=>structuredClone(p==='/api/workspaces'?spaces:paged(managed))
 for(const id of ['workspace','connection','bucket'])get(id).addEventListener('input',()=>changes.push(id))
 get('scope-form').addEventListener('submit',()=>opened++)
 const session={request:async(p,o)=>{calls.push([p,o]);return reply(p,o)}}
 const picker=mountArtifactScopePicker({session,managed,doc})
 const fire=(id,type)=>Promise.all((get(id).handlers[type]??[]).map(fn=>fn({type,preventDefault(){}})))
 return {get,picker,calls,changes,fire,setReply:fn=>reply=fn,opened:()=>opened}
}
for(const managed of [false,true]){
 const path='/api/workspaces/alpha/'+(managed?'artifact-admin':'artifacts')+'/scopes'
 test((managed?'admin':'reader')+' mounted pagination uses only opaque after, rejects repeats/overlap and explicitly reloads',async()=>{
  const h=harness(managed);await h.picker.activate();assert.equal(h.calls.length,1);await h.fire('artifact-picker-load','click')
  assert.equal(h.opened(),0);assert.equal(h.get('artifact-picker-next').disabled,false)
  h.setReply(async()=>paged(managed,25,receipt(2)));await h.fire('artifact-picker-next','click')
  assert.equal(h.calls.at(-1)[0],path+'?after='+receipt(1));assert.match(h.get('artifact-picker-scope').textContent,/b025/)
  h.setReply(async()=>paged(managed,25,receipt(3)));await h.fire('artifact-picker-next','click')
  assert.equal(h.get('artifact-picker-scope').children.length,0);assert.equal(h.get('artifact-picker-next').disabled,true);assert.match(h.get('artifact-picker-status').textContent,/Reload the directory explicitly/)
  const count=h.calls.length;await h.fire('artifact-picker-next','click');assert.equal(h.calls.length,count)
  h.setReply(async()=>paged(managed));await h.fire('artifact-picker-load','click');assert.equal(h.calls.at(-1)[0],path)
  h.setReply(async()=>paged(managed,25,receipt(1)));await h.fire('artifact-picker-next','click');assert.equal(h.get('artifact-picker-next').disabled,true)
  for(const status of [409,503]){
   h.setReply(async()=>paged(managed));await h.fire('artifact-picker-load','click');const before=h.calls.length
   h.setReply(async()=>{throw Object.assign(Error('opaque server failure'),{status})});await h.fire('artifact-picker-next','click')
   assert.equal(h.calls.length,before+1);assert.equal(h.get('artifact-picker-scope').children.length,0);assert.equal(h.get('artifact-picker-next').disabled,true)
   assert.match(h.get('artifact-picker-status').textContent,/Reload the directory explicitly; no request was retried/);assert.doesNotMatch(h.get('artifact-picker-status').textContent,/opaque server failure/)
   h.setReply(async()=>paged(managed));await h.fire('artifact-picker-load','click');assert.equal(h.calls.at(-1)[0],path)
  }
 })
 test((managed?'admin':'reader')+' ignored abort cannot revive old receipts or unlock newer pending requests',async()=>{
  const h=harness(managed);await h.picker.activate();const old=deferred();h.setReply(()=>old.promise)
  const first=h.fire('artifact-picker-load','click');await h.fire('artifact-picker-load','click');assert.equal(h.calls.length,2)
  h.get('artifact-picker-workspace').value='beta';await h.fire('artifact-picker-workspace','change');assert.equal(h.calls[1][1].signal.aborted,true)
  const fresh=deferred();h.setReply(()=>fresh.promise);const second=h.fire('artifact-picker-load','click');old.resolve(paged(managed));await first
  assert.equal(h.get('artifact-picker-load').disabled,true);assert.equal(h.get('artifact-picker-next').disabled,true);assert.equal(h.get('artifact-picker-scope').children.length,0)
  await h.fire('artifact-picker-load','click');assert.equal(h.calls.length,3)
  const page=paged(managed);page.workspace_id='beta';for(const row of page.items)row.scope.workspace_id='beta';fresh.resolve(page);await second
  assert.equal(h.get('artifact-picker-scope').children.length,25);assert.equal(h.get('artifact-picker-load').disabled,false)
 })
 test((managed?'admin':'reader')+' manual edit, workspace and session clear discard token and tuple together',async()=>{
  for(const action of ['manual','scope','clear']){
   const h=harness(managed);await h.picker.activate();await h.fire('artifact-picker-load','click')
   if(action==='manual')await h.fire('bucket','input')
   else if(action==='scope')await h.fire('artifact-picker-workspace','change')
   else{h.picker.clear();await h.picker.activate()}
   assert.equal(h.get('artifact-picker-next').disabled,true);const count=h.calls.length;await h.fire('artifact-picker-next','click');assert.equal(h.calls.length,count)
   await h.fire('artifact-picker-load','click');assert.equal(h.calls.at(-1)[0],path);assert.equal(h.get('artifact-picker-scope').children.length,25)
   const pending=deferred();h.setReply(()=>pending.promise);const read=h.fire('artifact-picker-next','click');h.picker.clear();pending.resolve(paged(managed,25,receipt(2)));await read
   assert.equal(h.get('artifact-picker').hidden,true);assert.equal(h.get('artifact-picker-scope').children.length,0);assert.equal(h.get('artifact-picker-next').disabled,true)
  }
 })
}

test('reader and admin selection/open keep directory context but clear prior exact view',async()=>{
 for(const managed of [false,true]){
  const h=harness(managed);await h.picker.activate();await h.fire('artifact-picker-load','click')
  await h.fire('artifact-picker-scope','change');assert.equal(h.get('workspace').value,'');assert.equal(h.get('artifact-picker-next').disabled,false)
  await h.fire('artifact-picker-use','click');assert.equal(h.opened(),1);assert.equal(h.get('workspace').value,'alpha');assert.equal(h.get('artifact-picker-next').disabled,false)
  h.setReply(async()=>paged(managed,25,null));await h.fire('artifact-picker-next','click');assert.equal(h.get('workspace').value,'');assert.equal(h.get('artifact-picker-next').disabled,true)
 }
})
