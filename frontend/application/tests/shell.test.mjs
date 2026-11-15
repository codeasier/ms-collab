import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import {createSessionClient,createResponsibilitySessionClient,validateBootstrap,authorizationTarget} from '../client.mjs'
import {validateWorkspaces} from '../../workspace-admin/client.mjs'
const source=fs.readFileSync(new URL('../shell.mjs',import.meta.url),'utf8').replace(/^import .*\n/gm,'')
const bootstrap={service:'ms-collab',ui_modules:['workspaces','responsibility'],login_configured:true,authorization_endpoint:'https://identity.example.invalid/authorize'}
const response=(data,status=200)=>({ok:status===200,status,text:async()=>JSON.stringify({data})})
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}}
const flush=()=>new Promise(r=>setImmediate(r))
function harness({fetcher,activate=async()=>{}}){
 const elements=new Map(),calls=[],state={mounted:0,cleared:0,options:null}
 for(const id of ['app-sign-in','app-sign-out','app-recheck','app-principal','app-status','app-login-note','app-responsibility-link','app-community-link','app-pipelines-link','app-assistant-link'])elements.set(id,{textContent:'',hidden:false,disabled:false,handlers:{},addEventListener(name,fn){this.handlers[name]=fn},setAttribute(){},replaceChildren(){}})
 const document={body:{dataset:{page:'manage'}},getElementById:id=>elements.get(id),createElement:()=>({})}
 vm.runInNewContext(source,{document,window:{addEventListener(){}},location:{protocol:'https:',origin:'https://app.example.invalid',assign(){}},createSessionClient:options=>createSessionClient({...options,cookie:()=>'__Host-ms-collab-csrf='+'A'.repeat(43),fetcher:async(path,options)=>{calls.push(path);return fetcher(path,options)}}),createResponsibilitySessionClient,validateBootstrap,authorizationTarget,validateWorkspaces,mountWorkspacePage:options=>{state.mounted++;state.options=options;return {activate,clear(){state.cleared++;options.client.clear()}}},mountResponsibilityPage(){throw new Error('unexpected module')}})
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
