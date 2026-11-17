import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import {createPipelineDirectoryClient,validatePipelineScopes} from '../pipeline-scope-client.mjs'
import {createSessionClient} from '../client.mjs'
const source=fs.readFileSync(new URL('../pipeline-scopes.mjs',import.meta.url),'utf8').replace("'/manage/client.mjs'",JSON.stringify(new URL('../../workspace-admin/client.mjs',import.meta.url).href)).replace("'./pipeline-scope-client.mjs'",JSON.stringify(new URL('../pipeline-scope-client.mjs',import.meta.url).href))
const {mountPipelineScopePicker}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'))
const scope={workspace_id:'alpha',connection_id:'ci',pipeline_id:'build'},item={scope,snapshot_revision:1},data={workspace_id:'alpha',items:[item],next_after:null}
const receipt=n=>'dcr1_'+Buffer.alloc(32,n).toString('base64url')
const paged=(start=0,next=receipt(1))=>({...data,items:Array.from({length:25},(_,i)=>({scope:{...scope,pipeline_id:`p${String(i+start).padStart(3,'0')}`},snapshot_revision:1})),next_after:next})
const spaces=['alpha','beta','commons'].map(id=>({id,name:id+' <img src=x>',kind:id==='commons'?'shared':'product',state:'active',revision:1}))
const response=(data,status=200)=>({ok:status===200,status,text:async()=>JSON.stringify({data})})
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}}
test('strict minimal current workspace/tuple/revision only, no provider or run metadata',()=>{
 assert.deepEqual(validatePipelineScopes(data,'alpha'),data)
 for(const mutate of [d=>d.workspace_id='beta',d=>d.total=1,d=>d.items.push(item),d=>d.items[0].scope.pipeline_id+='\n',d=>d.items[0].scope.workspace_id='beta',d=>d.items[0].snapshot_revision=0,d=>d.items[0].snapshot_revision=2**53,d=>d.items[0].snapshot_revision=true,d=>d.items[0].origin='https://ci.invalid',d=>d.items[0].runs=[],d=>d.items[0].catalog_revision=1]){const d=structuredClone(data);mutate(d);assert.throws(()=>validatePipelineScopes(d,'alpha'))}
 assert.deepEqual(validatePipelineScopes({...data,items:[]},'alpha').items,[])
})
test('opaque canonical receipt is independent of strictly ordered tuples and page size',()=>{
 const page=paged();assert.equal(validatePipelineScopes(page,'alpha').items.length,25)
 const last=['ci','p024'],next=paged(25,receipt(2));assert.equal(validatePipelineScopes(next,'alpha',receipt(1),last),next)
 assert.equal(validatePipelineScopes({...next,items:[],next_after:null},'alpha',receipt(1),last).items.length,0)
 for(const changed of [{...page,next_after:'ci~p024'},{...page,items:page.items.slice(1)},{...page,items:[...page.items,item]},{...page,items:[...page.items].reverse()}])assert.throws(()=>validatePipelineScopes(changed,'alpha'))
 for(const after of ['ci~p024','invalid',false,3,receipt(1)+'\n',receipt(1)+'=',receipt(1).slice(0,-1),receipt(1).slice(0,-1)+'B','dcr2_'+receipt(1).slice(5),'dcr1_'+'.'.repeat(43),'dcr1_'+'é'.repeat(43)]){
  assert.throws(()=>validatePipelineScopes(next,'alpha',after,last));assert.throws(()=>validatePipelineScopes({...page,next_after:after},'alpha'))
 }
 // All and only canonical final sextets are accepted (unused low two bits zero).
 for(const tail of 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-'){
  const candidate='dcr1_'+'A'.repeat(42)+tail
  if('AEIMQUYcgkosw048'.includes(tail))assert.equal(validatePipelineScopes({...page,next_after:candidate},'alpha').next_after,candidate)
  else assert.throws(()=>validatePipelineScopes({...page,next_after:candidate},'alpha'))
 }
 assert.throws(()=>validatePipelineScopes({...next,next_after:receipt(1)},'alpha',receipt(1),last))
 for(const prior of [null,new Array(2),[],['ci'],['ci','p024','extra'],['ci','p024\n'],['ci','p025'],['ci','z']])assert.throws(()=>validatePipelineScopes(next,'alpha',receipt(1),prior))
 assert.throws(()=>validatePipelineScopes(page,'alpha',null,last))
 assert.throws(()=>validatePipelineScopes(page,'alpha',receipt(1),last))
})
test('Cookie GET only; cursor failure never retries; stale session never returns a directory',async()=>{
 const calls=[],values=[response({principal_id:'reader'}),response(data)]
 const session=createSessionClient({fetcher:async(path,options)=>{calls.push({path,options});return values.shift()},cookie:()=>''})
 await session.inspect();const directory=createPipelineDirectoryClient(session);await directory.list('alpha')
 assert.equal(calls[1].path,'/api/workspaces/alpha/pipelines/scopes');assert.deepEqual(calls[1].options.headers,{});assert.equal(calls[1].options.method,'GET');assert.equal(calls[1].options.credentials,'same-origin');assert.equal(calls[1].options.cache,'no-store')
 values.push(response({},409));await assert.rejects(directory.list('alpha',{after:receipt(1),lastTuple:['ci','build']}));assert.equal(calls[2].path,'/api/workspaces/alpha/pipelines/scopes?after='+receipt(1));assert.equal(calls.length,3)
 for(const workspace of ['alpha\n','../beta','https://other.invalid'])await assert.rejects(directory.list(workspace))
 const pending=deferred();values.push(pending.promise);const read=directory.list('alpha');session.clear();pending.resolve(response(data));await assert.rejects(read,/Stale session/);assert.equal(calls.length,4)
})
function harness(){
 class Node{
  constructor(tag){this.tag=tag;this.children=[];this.handlers={};this.value='';this.disabled=false;this.hidden=false;this._text=''}
  set textContent(v){this._text=String(v);this.children=[]}get textContent(){return this._text+this.children.map(n=>n.textContent).join('')}
  append(n){this.children.push(n);if(this.tag==='select'&&this.children.length===1)this.value=n.value}
  replaceChildren(){this.children=[];this.value='';this._text=''}
  addEventListener(name,fn){(this.handlers[name]??=[]).push(fn)}
  dispatchEvent(e){for(const fn of this.handlers[e.type]??[])fn(e)}
  requestSubmit(){this.dispatchEvent({type:'submit',preventDefault(){}})}
 }
 const ids=['pipeline-picker','pipeline-picker-workspace','pipeline-picker-scope','pipeline-picker-load','pipeline-picker-use','pipeline-picker-next','pipeline-picker-status','scope-form','workspace','connection','pipeline']
 const nodes=new Map(ids.map(id=>[id,new Node(['pipeline-picker-workspace','pipeline-picker-scope'].includes(id)?'select':'div')]))
 const get=id=>nodes.get(id),doc={getElementById:get,createElement:tag=>new Node(tag)},calls=[],changes=[]
 let opened=0,opening=false,reply=async p=>structuredClone(p==='/api/workspaces'?spaces:data)
 for(const id of ['workspace','connection','pipeline'])get(id).addEventListener('input',()=>changes.push(id))
 get('scope-form').addEventListener('submit',()=>{opened++;opening=true})
 const session={request:async(p,o)=>{calls.push([p,o]);return reply(p,o)}}
 const picker=mountPipelineScopePicker({session,doc,isOpening:()=>opening})
 const fire=(id,type)=>Promise.all((get(id).handlers[type]??[]).map(fn=>fn({type,preventDefault(){}})))
 return {get,picker,calls,changes,fire,setReply:fn=>reply=fn,opened:()=>opened,finish(){opening=false;picker.sync()}}
}
test('mounted picker loads only explicitly, text renders scope, explicit open reuses manual form',async()=>{
 const h=harness();await h.picker.activate();assert.equal(h.calls.length,1);assert.equal(h.get('pipeline-picker-workspace').children.length,3);assert.match(h.get('pipeline-picker-workspace').textContent,/<img src=x>/)
 await h.fire('pipeline-picker-load','click');assert.deepEqual(h.changes,['workspace','connection','pipeline']);assert.equal(h.opened(),0)
 await h.fire('pipeline-picker-use','click');assert.equal(h.get('workspace').value,'alpha');assert.equal(h.get('pipeline').value,'build');assert.equal(h.opened(),1)
 await h.fire('pipeline-picker-use','click');assert.equal(h.opened(),1);h.finish();assert.equal(h.get('pipeline-picker-use').disabled,false)
 await h.fire('pipeline-picker-scope','change');assert.equal(h.get('workspace').value,'');assert.equal(h.changes.length,9)
})
test('ignored abort and repeated list cannot revive older workspace or replace a newer page',async()=>{
 const h=harness();await h.picker.activate();const old=deferred();h.setReply(()=>old.promise)
 const first=h.fire('pipeline-picker-load','click');await h.fire('pipeline-picker-load','click');assert.equal(h.calls.length,2)
 h.get('pipeline-picker-workspace').value='beta';await h.fire('pipeline-picker-workspace','change');assert.equal(h.calls[1][1].signal.aborted,true)
 const fresh=deferred();h.setReply(()=>fresh.promise);const second=h.fire('pipeline-picker-load','click');old.resolve(paged());await first
 assert.equal(h.get('pipeline-picker-load').disabled,true);assert.equal(h.get('pipeline-picker-next').disabled,true);assert.equal(h.get('pipeline-picker-scope').children.length,0)
 fresh.resolve({workspace_id:'beta',items:[{scope:{...scope,workspace_id:'beta'},snapshot_revision:2}],next_after:null});await second
 assert.equal(h.get('pipeline-picker-scope').children.length,1);assert.match(h.get('pipeline-picker-scope').textContent,/revision 2/)
})
test('manual edit and clear invalidate list/workspace activation responses, even with ignored abort',async()=>{
 for(const action of ['manual','clear']){
  const h=harness();await h.picker.activate();const pending=deferred();h.setReply(()=>pending.promise);const list=h.fire('pipeline-picker-load','click')
  if(action==='manual'){h.get('workspace').value='beta';await h.fire('workspace','input')}else h.picker.clear()
  pending.resolve(data);await list;assert.equal(h.get('pipeline-picker-scope').children.length,0);assert.equal(h.get('pipeline-picker-use').disabled,true)
 }
 const h=harness(),pending=deferred();h.setReply(()=>pending.promise);const active=h.picker.activate();h.picker.clear();pending.resolve(spaces);await active;assert.equal(h.get('pipeline-picker').hidden,true);assert.equal(h.get('pipeline-picker-workspace').children.length,0)
})
test('empty is distinct from failed directory; invalid cursor clears page without automatic retry',async()=>{
 const h=harness();await h.picker.activate();h.setReply(async()=>({...data,items:[]}));await h.fire('pipeline-picker-load','click');assert.match(h.get('pipeline-picker-status').textContent,/No stored pipeline scopes/)
 h.setReply(async()=>{throw Object.assign(Error('Directory changed'),{status:409})});await h.fire('pipeline-picker-load','click');assert.equal(h.calls.length,3);assert.match(h.get('pipeline-picker-status').textContent,/no request was retried/);assert.equal(h.get('pipeline-picker-scope').children.length,0)
})

test('mounted receipt pagination passes local ordering evidence, clears failures, and reloads only explicitly',async()=>{
 const h=harness();await h.picker.activate();h.setReply(async()=>paged());await h.fire('pipeline-picker-load','click')
 assert.equal(h.get('pipeline-picker-next').disabled,false)
 h.setReply(async()=>paged(25,receipt(2)));await h.fire('pipeline-picker-next','click')
 assert.equal(h.calls.at(-1)[0],'/api/workspaces/alpha/pipelines/scopes?after='+receipt(1));assert.match(h.get('pipeline-picker-scope').textContent,/p025/)
 // A sorted page may still overlap the preceding page. A new opaque token cannot excuse it.
 h.setReply(async()=>paged(25,receipt(3)));await h.fire('pipeline-picker-next','click')
 assert.equal(h.get('pipeline-picker-scope').children.length,0);assert.equal(h.get('pipeline-picker-next').disabled,true)
 assert.match(h.get('pipeline-picker-status').textContent,/Reload the directory explicitly/)
 const count=h.calls.length;await h.fire('pipeline-picker-next','click');assert.equal(h.calls.length,count)
 h.setReply(async()=>paged());await h.fire('pipeline-picker-load','click');assert.equal(h.calls.at(-1)[0],'/api/workspaces/alpha/pipelines/scopes')
 // Returned input token must not make Next loop even with strictly increasing tuples.
 h.setReply(async()=>paged(25,receipt(1)));await h.fire('pipeline-picker-next','click');assert.equal(h.get('pipeline-picker-next').disabled,true)
 for(const status of [409,503]){
  h.setReply(async()=>paged());await h.fire('pipeline-picker-load','click');const before=h.calls.length
  h.setReply(async()=>{throw Object.assign(Error('opaque server failure'),{status})});await h.fire('pipeline-picker-next','click')
  assert.equal(h.calls.length,before+1);assert.equal(h.get('pipeline-picker-scope').children.length,0);assert.equal(h.get('pipeline-picker-next').disabled,true)
  assert.match(h.get('pipeline-picker-status').textContent,/Reload the directory explicitly; no request was retried/)
  assert.doesNotMatch(h.get('pipeline-picker-status').textContent,/opaque server failure/)
  h.setReply(async()=>paged());await h.fire('pipeline-picker-load','click');assert.equal(h.calls.at(-1)[0],'/api/workspaces/alpha/pipelines/scopes')
 }
})
test('manual edit, scope change and clear discard receipt plus last tuple before an explicit fresh page',async()=>{
 for(const action of ['manual','scope','clear']){
  const h=harness();await h.picker.activate();h.setReply(async()=>paged());await h.fire('pipeline-picker-load','click')
  if(action==='manual')await h.fire('pipeline','input')
  else if(action==='scope')await h.fire('pipeline-picker-workspace','change')
  else{h.picker.clear();h.setReply(async()=>spaces);await h.picker.activate()}
  assert.equal(h.get('pipeline-picker-next').disabled,true);const before=h.calls.length;await h.fire('pipeline-picker-next','click');assert.equal(h.calls.length,before)
  h.setReply(async()=>paged());await h.fire('pipeline-picker-load','click');assert.equal(h.calls.at(-1)[0],'/api/workspaces/alpha/pipelines/scopes');assert.equal(h.get('pipeline-picker-scope').children.length,25)
 }
})

test('selection/open preserves same-context pagination while discarding exact-view evidence',async()=>{
 const h=harness();await h.picker.activate();h.setReply(async()=>paged());await h.fire('pipeline-picker-load','click')
 await h.fire('pipeline-picker-scope','change');assert.equal(h.get('workspace').value,'');assert.equal(h.get('pipeline-picker-next').disabled,false)
 await h.fire('pipeline-picker-use','click');assert.equal(h.opened(),1);assert.equal(h.get('workspace').value,'alpha');assert.equal(h.get('pipeline-picker-next').disabled,false)
 h.finish();h.setReply(async()=>paged(25,null));await h.fire('pipeline-picker-next','click');assert.equal(h.calls.at(-1)[0],'/api/workspaces/alpha/pipelines/scopes?after='+receipt(1));assert.equal(h.get('workspace').value,'');assert.equal(h.get('pipeline-picker-next').disabled,true)
})
