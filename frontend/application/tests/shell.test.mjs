import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import {createSessionClient,createResponsibilitySessionClient,validateBootstrap,authorizationTarget} from '../client.mjs'
import {validateWorkspaces,displayPolicyId} from '../../workspace-admin/client.mjs'
import {mountModuleStatus} from '../module-status.mjs'
const source=fs.readFileSync(new URL('../shell.mjs',import.meta.url),'utf8').replace(/^import .*\n/gm,'')
const bootstrap={service:'ms-collab',ui_modules:['workspaces','responsibility'],login_configured:true,authorization_endpoint:'https://identity.example.invalid/authorize'}
const response=(data,status=200)=>({ok:status===200,status,text:async()=>JSON.stringify({data})})
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}}
const flush=()=>new Promise(r=>setImmediate(r))
function harness({fetcher,activate=async()=>{}}){
 const elements=new Map(),calls=[],state={mounted:0,cleared:0,options:null}
 for(const id of ['app-sign-in','app-sign-out','app-recheck','app-principal','app-status','app-login-note','app-responsibility-link','app-community-link','app-pipelines-link','app-assistant-link'])elements.set(id,{textContent:'',hidden:false,disabled:false,handlers:{},addEventListener(name,fn){this.handlers[name]=fn},setAttribute(){},replaceChildren(){}})
 const document={body:{dataset:{page:'manage'}},getElementById:id=>elements.get(id),createElement:()=>({})}
 vm.runInNewContext(source,{document,window:{addEventListener(){}},location:{protocol:'https:',origin:'https://app.example.invalid',assign(){}},createSessionClient:options=>createSessionClient({...options,cookie:()=>'__Host-ms-collab-csrf='+'A'.repeat(43),fetcher:async(path,options)=>{calls.push(path);return fetcher(path,options)}}),createResponsibilitySessionClient,validateBootstrap,authorizationTarget,validateWorkspaces,displayPolicyId,mountWorkspacePage:options=>{state.mounted++;state.options=options;return {activate,clear(){state.cleared++;options.client.clear()}}},mountResponsibilityPage(){throw new Error('unexpected module')}})
 return {elements,calls,state,click:id=>elements.get(id).handlers.click()}
}
test('early recheck cannot cancel bootstrap or inspect an unmounted page',async()=>{
 const boot=deferred(),h=harness({fetcher:path=>path==='/app/bootstrap'?boot.promise:response({principal_id:'admin'})})
 assert.equal(h.elements.get('app-recheck').disabled,true);await h.click('app-recheck');assert.deepEqual(h.calls,['/app/bootstrap']);assert.equal(h.state.mounted,0)
 boot.resolve(response(bootstrap));await flush();await flush();assert.equal(h.state.mounted,1);assert.deepEqual(h.calls,['/app/bootstrap','/auth/session']);assert.equal(h.elements.get('app-principal').textContent,'admin');assert.equal(h.elements.get('app-recheck').disabled,false)
})
test('setup failure has an explicit retry, without a fake session or page mount',async()=>{
 let tries=0;const h=harness({fetcher:path=>path==='/app/bootstrap'?(++tries===1?response({},503):response(bootstrap)):response({principal_id:'admin'})})
 await flush();assert.equal(h.state.mounted,0);assert.equal(h.elements.get('app-recheck').textContent,'Retry setup');assert.equal(h.elements.get('app-sign-in').disabled,true)
 await h.click('app-recheck');await flush();assert.equal(h.state.mounted,1);assert.equal(h.elements.get('app-principal').textContent,'admin');assert.equal(tries,2)
})
test('internal sign-out wins over delayed activation, sends once, and old inspection cannot revive it',async()=>{
 const active=deferred(),h=harness({activate:()=>active.promise,fetcher:path=>response(path==='/app/bootstrap'?bootstrap:path==='/auth/logout'?{logged_out:true}:{principal_id:'admin'})})
 await flush();assert.equal(h.state.mounted,1);assert.equal(h.elements.get('app-principal').textContent,'admin');assert.equal(h.elements.get('app-sign-out').disabled,false)
 h.state.options.client.clear();const first=h.state.options.onSignOut();const second=h.state.options.onSignOut();await Promise.all([first,second]);assert.equal(h.calls.filter(x=>x==='/auth/logout').length,1);assert.equal(h.elements.get('app-principal').textContent,'Not verified');assert.match(h.elements.get('app-status').textContent,/server session is revoked/)
 active.resolve();await flush();assert.equal(h.elements.get('app-principal').textContent,'Not verified');assert.match(h.elements.get('app-status').textContent,/server session is revoked/)
})

// Keep the real Overview else branch and real controller/client together. This
// harness supplements, rather than replacing, the management regressions above.
class OverviewElement{
 constructor(){this.textContent='';this.value='';this.hidden=false;this.disabled=false;this.children=[];this.handlers={};this.attributes={}}
 addEventListener(name,handler){this.handlers[name]=handler}
 setAttribute(name,value){this.attributes[name]=value;if(name==='hidden')this.hidden=true}
 replaceChildren(...children){this.children=children;this.value=''}
 append(...children){this.children.push(...children)}
 fire(name){return this.handlers[name]?.({preventDefault(){}})}
}
const overviewSpaces=[{id:'alpha',name:'Alpha',kind:'product',state:'active',revision:1},{id:'beta',name:'Beta',kind:'shared',state:'archived',revision:2},{id:'commons',name:'Commons',kind:'shared',state:'active',revision:1}]
const overviewStatus=(workspace='alpha',key='pipelines')=>({workspace_id:workspace,key,state:'disabled',revision:1})
function overviewHarness({fetcher,activationFailure=false}={}){
 const elements=new Map(),calls=[],events=[],listeners={},state={mounted:0,activations:0,cleared:0,listAtActivation:[],dataHiddenAtActivation:[]}
 for(const id of ['app-sign-in','app-sign-out','app-recheck','app-principal','app-status','app-login-note','app-responsibility-link','app-community-link','app-pipelines-link','app-assistant-link','app-workspaces','app-data',...['','form','workspace','key','check','clear','message','result','result-workspace','result-key','result-state','result-revision'].map(id=>'module-status'+(id?'-'+id:''))])elements.set(id,new OverviewElement())
 elements.get('app-data').hidden=true
 const doc={body:{dataset:{page:'overview'}},getElementById:id=>elements.get(id),createElement:()=>new OverviewElement()}
 vm.runInNewContext(source,{document:doc,window:{addEventListener(name,handler){listeners[name]=handler}},location:{protocol:'https:',origin:'https://app.example.invalid',search:'?workspace=beta&key=old',hash:'#module=old',assign(){}},createSessionClient:options=>createSessionClient({...options,cookie:()=>'__Host-ms-collab-csrf='+'A'.repeat(43),fetcher:async(path,options)=>{
  calls.push({path,options});events.push('request:'+path)
  if(fetcher)return fetcher(path,options)
  return response(path==='/app/bootstrap'?bootstrap:path==='/auth/session'?{principal_id:'reader'}:path==='/auth/logout'?{logged_out:true}:path==='/api/workspaces'?overviewSpaces:overviewStatus())
 }}),createResponsibilitySessionClient,validateBootstrap,authorizationTarget,validateWorkspaces,displayPolicyId,mountModuleStatus:({session})=>{
  state.mounted++;const controller=mountModuleStatus({session,doc})
  return {activate(spaces){state.activations++;events.push('activate-status');state.listAtActivation.push(elements.get('app-workspaces').children.map(item=>item.textContent));state.dataHiddenAtActivation.push(elements.get('app-data').hidden);controller.activate(spaces);if(activationFailure)throw Error('Synthetic activation failure')},clear(){state.cleared++;events.push('clear-status');controller.clear()}}
 },mountWorkspacePage(){throw Error('Overview must not mount the admin page')},mountResponsibilityPage(){throw Error('Overview must not mount a business module')}})
 const el=id=>elements.get(id),select=(workspace,key)=>{el('module-status-workspace').value=workspace;el('module-status-workspace').fire('change');el('module-status-key').value=key;el('module-status-key').fire('input')}
 return {elements,calls,events,state,listeners,el,select,click:id=>el(id).fire('click'),submit:()=>el('module-status-form').fire('submit')}
}

test('Overview owns exactly one workspace read per inspection and synchronously activates its separate status section',async()=>{
 const h=overviewHarness();await flush()
 assert.equal(h.state.mounted,1);assert.equal(h.state.activations,1)
 assert.deepEqual(h.calls.map(call=>call.path),['/app/bootstrap','/auth/session','/api/workspaces'])
 assert.deepEqual(h.state.listAtActivation[0],['Alpha · product · active · alpha','Beta · shared · archived · beta','Commons · shared · active · commons']);assert.deepEqual(h.state.dataHiddenAtActivation,[true])
 assert.equal(h.el('app-data').hidden,false);assert.equal(h.el('module-status').hidden,false);assert.equal(h.el('module-status-workspace').value,'');assert.equal(h.el('module-status-key').value,'')
 assert.deepEqual(h.el('module-status-workspace').children.map(option=>option.value),['','alpha','beta','commons'])
 assert.equal(h.el('app-principal').textContent,'reader');assert.equal(h.el('app-recheck').disabled,false);assert.equal(h.el('app-sign-out').disabled,false)
 h.select('alpha','pipelines');assert.equal(h.calls.length,3)
 await h.click('app-recheck')
 assert.equal(h.state.mounted,1);assert.equal(h.state.activations,2);assert.equal(h.calls.filter(call=>call.path==='/api/workspaces').length,2);assert.equal(h.calls.some(call=>/\/modules|\/members|module-catalog/.test(call.path)),false)
 assert.equal(h.el('module-status-workspace').value,'');assert.equal(h.el('module-status-key').value,'')
})

test('Overview empty workspace list keeps its original explanation and does not dispatch a status query',async()=>{
 const h=overviewHarness({fetcher:path=>response(path==='/app/bootstrap'?bootstrap:path==='/auth/session'?{principal_id:'reader'}:[])})
 await flush();assert.equal(h.el('app-workspaces').children.length,1);assert.match(h.el('app-workspaces').children[0].textContent,/No accessible workspaces/)
 assert.equal(h.el('module-status').hidden,false);assert.match(h.el('module-status-message').textContent,/No accessible workspaces/)
 assert.equal(h.el('module-status-check').disabled,true);await h.submit();assert.equal(h.calls.length,3)
})

for(const failure of ['session','list','invalid list','activation'])test(`Overview ${failure} failure clears the entire status section and existing list`,async()=>{
 const h=overviewHarness({activationFailure:failure==='activation',fetcher:path=>{
  if(path==='/app/bootstrap')return response(bootstrap)
  if(path==='/auth/session')return response(failure==='session'?{}:{principal_id:'reader'},failure==='session'?401:200)
  return failure==='list'?response({},503):response(failure==='invalid list'?[{id:'bad/id'}]:overviewSpaces)
 }})
 await flush()
 assert.equal(h.el('app-principal').textContent,'Not verified');assert.equal(h.el('app-workspaces').children.length,0);assert.equal(h.el('app-data').hidden,true);assert.equal(h.el('module-status').hidden,true)
 assert.equal(h.el('module-status-workspace').children.length,0);assert.equal(h.el('module-status-workspace').value,'');assert.equal(h.el('module-status-key').value,'');assert.equal(h.el('module-status-result').hidden,true)
 assert.equal(h.calls.some(call=>/\/modules|\/members|module-catalog/.test(call.path)),false)
})

test('Overview Recheck remains usable during a held status query and clears locally before inspecting',async()=>{
 const held=deferred(),h=overviewHarness({fetcher:path=>path.includes('/modules/')?held.promise:response(path==='/app/bootstrap'?bootstrap:path==='/auth/session'?{principal_id:'reader'}:overviewSpaces)})
 await flush();h.select('alpha','pipelines');const pending=h.submit()
 assert.equal(h.el('app-recheck').disabled,false);assert.equal(h.el('app-sign-out').disabled,false);assert.equal(h.el('module-status-check').disabled,true)
 const start=h.events.length,recheck=h.click('app-recheck')
 assert.equal(h.events[start],'clear-status');assert.equal(h.events[start+1],'request:/auth/session')
 assert.equal(h.el('module-status-key').value,'');assert.equal(h.el('module-status-workspace').value,'');assert.equal(h.calls.find(call=>call.path.includes('/modules/')).options.signal.aborted,true)
 await recheck;held.resolve(response(overviewStatus()));await pending
 assert.equal(h.calls.filter(call=>call.path==='/api/workspaces').length,2);assert.equal(h.calls.filter(call=>call.path.includes('/modules/')).length,1);assert.equal(h.el('module-status-result').hidden,true);assert.equal(h.el('module-status-key').value,'');assert.equal(h.el('app-principal').textContent,'reader')
})

test('Overview Sign out remains usable during status acquisition and sends only one real logout',async()=>{
 const held=deferred(),h=overviewHarness({fetcher:path=>path.includes('/modules/')?held.promise:response(path==='/app/bootstrap'?bootstrap:path==='/auth/session'?{principal_id:'reader'}:path==='/auth/logout'?{logged_out:true}:overviewSpaces)})
 await flush();h.select('alpha','pipelines');const pending=h.submit()
 assert.equal(h.el('app-sign-out').disabled,false)
 const first=h.click('app-sign-out'),second=h.click('app-sign-out');await Promise.all([first,second])
 assert.equal(h.calls.filter(call=>call.path==='/auth/logout').length,1);assert.equal(h.el('module-status').hidden,true);assert.equal(h.el('module-status-key').value,'');assert.equal(h.el('app-principal').textContent,'Not verified')
 held.resolve(response(overviewStatus()));await pending
 assert.equal(h.el('module-status-result').hidden,true);assert.equal(h.el('module-status-message').textContent,'');assert.match(h.el('app-status').textContent,/server session is revoked/)
})

test('Overview pagehide and synthetic persisted-pageshow handler clear without restoring or requesting',async()=>{
 const held=deferred(),h=overviewHarness({fetcher:path=>path.includes('/modules/')?held.promise:response(path==='/app/bootstrap'?bootstrap:path==='/auth/session'?{principal_id:'reader'}:overviewSpaces)})
 await flush();h.select('alpha','pipelines');const pending=h.submit();const count=h.calls.length
 h.listeners.pagehide();assert.equal(h.el('module-status').hidden,true);assert.equal(h.el('module-status-key').value,'');assert.equal(h.el('module-status-workspace').value,'')
 h.listeners.pageshow({persisted:true});assert.match(h.el('app-status').textContent,/Explicitly recheck/);assert.equal(h.calls.length,count)
 held.resolve(response(overviewStatus()));await pending
 assert.equal(h.el('module-status-result').hidden,true);assert.equal(h.el('module-status-message').textContent,'');assert.equal(h.calls.length,count)
 await h.click('app-recheck');assert.equal(h.el('module-status').hidden,false);assert.equal(h.el('module-status-key').value,'');assert.equal(h.el('module-status-workspace').value,'');assert.equal(h.calls.filter(call=>call.path.includes('/modules/')).length,1)
})

test('fresh Overview documents and non-persisted pageshow do not restore queries from URL or another document',async()=>{
 const first=overviewHarness();await flush();first.select('alpha','pipelines');await first.submit();assert.equal(first.el('module-status-result').hidden,false)
 const second=overviewHarness();await flush();second.listeners.pageshow({persisted:false})
 assert.equal(second.el('module-status-workspace').value,'');assert.equal(second.el('module-status-key').value,'');assert.equal(second.el('module-status-result').hidden,true);assert.equal(second.calls.length,3)
 second.select('beta','different');assert.equal(first.el('module-status-workspace').value,'alpha');assert.equal(first.el('module-status-key').value,'pipelines');assert.equal(first.el('module-status-result').hidden,false)
})

test('current status 401 invokes the real shell clear and cannot revive result or private inputs',async()=>{
 const h=overviewHarness({fetcher:path=>path.includes('/modules/')?response({},401):response(path==='/app/bootstrap'?bootstrap:path==='/auth/session'?{principal_id:'reader'}:overviewSpaces)})
 await flush();h.select('commons','pipelines');await h.submit()
 assert.equal(h.el('app-principal').textContent,'Not verified');assert.equal(h.el('module-status').hidden,true);assert.equal(h.el('module-status-workspace').children.length,0);assert.equal(h.el('module-status-workspace').value,'');assert.equal(h.el('module-status-key').value,'');assert.equal(h.el('module-status-message').textContent,'');assert.equal(h.el('module-status-result').hidden,true)
 assert.equal(h.el('app-recheck').disabled,false);assert.match(h.el('app-status').textContent,/session expired or was rejected/)
})

test('opaque current-principal label preserves ordinary ASCII and escapes legal control identities, then clears',async()=>{
 for(const principal_id of ['ordinary:id','opaque\0\u007f\\u{0}\uFEFF','😀'.repeat(200)]){
  const h=harness({fetcher:path=>response(path==='/app/bootstrap'?bootstrap:path==='/auth/logout'?{logged_out:true}:{principal_id})})
  await flush();assert.equal(h.elements.get('app-principal').textContent,displayPolicyId(principal_id))
  await h.click('app-sign-out');assert.equal(h.elements.get('app-principal').textContent,'Not verified')
 }
})
