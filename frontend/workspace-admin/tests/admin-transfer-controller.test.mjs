import nodeTest from 'node:test'
import assert from 'node:assert/strict'
import {webcrypto} from 'node:crypto'
import fs from 'node:fs'
import vm from 'node:vm'
import {createSessionClient,validateBootstrap} from '../../application/client.mjs'
import {mountWorkspacePage} from '../admin.mjs'
import {createClient,displayPolicyId} from '../client.mjs'
const test=(name,body)=>nodeTest(name,{timeout:10000},body)
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve}}
const flush=()=>new Promise(resolve=>setImmediate(resolve))
const reply=(data,status=200)=>({ok:status>=200&&status<300,status,text:async()=>JSON.stringify(status===200?{data}:{error:data}),json:async()=>status===200?{data}:{error:data}})
const space=id=>({id,name:id==='alpha'?'Alpha':'Beta',kind:'product',state:'active',revision:1})
const actor='actor\0\u007f\\u{0}\uFEFF'+'😀'.repeat(180)
const member=(principal_id,workspace_id='alpha')=>({workspace_id,principal_id,role:principal_id===actor?'admin':'viewer',state:'active',revision:1})
const reviewed=(replacement='reader',workspace='alpha',revision=1)=>({workspace_id:workspace,actor_id:actor,replacement_id:replacement,actor_revision:revision,replacement_revision:revision,precondition:'ats1-'+'a'.repeat(64)})
const committed=(replacement='reader',workspace='alpha',revision=2)=>({workspace_id:workspace,actor_id:actor,replacement_id:replacement,actor_role:'developer',replacement_role:'admin',actor_revision:revision,replacement_revision:revision,state:'committed'})
class Element {
 constructor(id='',tag=''){this.id=id;this.tagName=tag;this.textContent='';this.value='';this.hidden=false;this.disabled=false;this.open=false;this.children=[];this.dataset={};this.handlers={};this.attributes={}}
 addEventListener(name,handler){(this.handlers[name]??=[]).push(handler)}
 setAttribute(name,value){this.attributes[name]=value;if(name==='hidden')this.hidden=true}
 append(...children){for(const child of children){child.parent=this;this.children.push(child)}}
 replaceChildren(...children){this.children=[];this.value='';this.append(...children)}
 get options(){return this.children}
 showModal(){this.open=true}
 close(){this.open=false}
 reset(){this.value=''}
 closest(selector){return selector==='#transfer-dialog'&&this.id.startsWith('transfer-')?this:undefined}
 async fire(name,event={}){for(const handler of this.handlers[name]||[])await handler({preventDefault(){},target:this,...event})}
}
function harness(t,{respond,cookieMode=false,shellMode=false,members=[member(actor),member('reader'),member('second')]}={}){
 const ids=['members','panel','title','state','session','status','workspace','connect','credential','disconnect','reload','editor','edit-form','editing','edit-status','role','member-state','save','cancel','create-dialog','create-form','create-open','space-name','space-kind','initial-admin','initial-admin-review','initial-admin-selection','create-save','create-cancel','create-status','action-dialog','action-form','action-title','action-description','action-status','action-save','action-cancel','invite-dialog','invite-form','invite-open','invite-principal','invite-role','invite-review','invite-selection','invite-status','invite-save','invite-cancel','inbox-panel','inbox','inbox-status','operator-panel','control-spaces','module-panel','modules','module-key','module-status','install-form','install','invitation-panel','invitations','invitation-status',...['open','dialog','form','target','review','selection','confirm','close','status','reference','outcome'].map(x=>'transfer-'+x)]
 if(shellMode)ids.push(...['sign-in','sign-out','recheck','principal','status','login-note','responsibility-link','community-link'].map(id=>'app-'+id))
 const elements=new Map(ids.map(id=>[id,new Element(id)])),listeners={},calls=[]
 const doc={body:{dataset:{page:'manage'}},getElementById:id=>elements.get(id),createElement:tag=>new Element('',tag),querySelectorAll:()=>[...elements.values()].filter(e=>/^(?:transfer-|invite-|create-|initial-admin|action-|role|member-state|save$|cancel$)/.test(e.id))}
 const saved=Object.fromEntries(['document','window','location','crypto'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]))
 const globals={document:doc,window:{addEventListener(name,handler){(listeners[name]??=[]).push(handler)}},location:{protocol:'https:',hostname:'synthetic.invalid',origin:'https://synthetic.invalid'},crypto:globalThis.crypto||webcrypto}
 for(const [key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{value,writable:true,configurable:true})
 t.after(()=>{for(const [key,descriptor]of Object.entries(saved)){if(descriptor===undefined)delete globalThis[key];else Object.defineProperty(globalThis,key,descriptor)}})
 const fetcher=async(path,options)=>{
  const call={path,options};calls.push(call)
  const custom=respond?.(call,calls);if(custom!==undefined)return custom
  if(path==='/app/bootstrap')return reply({service:'ms-collab',ui_modules:['workspaces'],login_configured:false,authorization_endpoint:null})
  if(path==='/auth/session')return reply({principal_id:actor})
  if(path==='/api/control/capabilities')return reply({manage_workspaces:false})
  if(path==='/api/workspaces')return reply([space('alpha'),space('beta')])
  if(path==='/api/invitations')return reply([])
  const scope=path.split('/')[3]
  if(path.endsWith('/members'))return reply(members.map(m=>({...m,workspace_id:scope})))
  if(path.endsWith('/capabilities'))return reply({available:true})
  if(path.includes('/selections/'))return reply(reviewed(decodeURIComponent(path.split('/').at(-1)),scope))
  if(path.endsWith('/admin-transfers'))return reply(committed(JSON.parse(options.body).replacement_id,scope))
  return reply([])
 }
 const el=id=>elements.get(id),cookie=()=>'__Host-ms-collab-csrf='+'A'.repeat(43)
 let page,client
 if(shellMode){
  // Exercise the production shell's own onInvalid and clear wiring, not a
  // guessed callback reproducing those two steps.
  const shell=fs.readFileSync(new URL('../../application/shell.mjs',import.meta.url),'utf8').replace(/^import .*\n/gm,'')
  vm.runInNewContext(shell,{...globals,validateBootstrap,displayPolicyId,createSessionClient:options=>(client=createSessionClient({...options,fetcher,cookie})),mountWorkspacePage:options=>(page=mountWorkspacePage(options))})
 }else{
  client=cookieMode?createSessionClient({fetcher,cookie,onInvalid:(...args)=>page.clear(...args)}):createClient({fetcher})
  if(!cookieMode)client.connect('synthetic-only')
  page=mountWorkspacePage({client,sessionMode:true})
  if(cookieMode){const activate=page.activate;page.activate=async()=>{await client.inspect();return activate()}}
 }
 const choose=async(value='reader')=>{el('transfer-target').value=value;await el('transfer-target').fire('change')}
 const open=async(value='reader')=>{await el('transfer-open').fire('click');await choose(value)}
 return{get page(){return page},get client(){return client},calls,listeners,el,choose,open,click:id=>el(id).fire('click'),review:()=>el('transfer-review').fire('click'),submit:()=>el('transfer-form').fire('submit'),posts:()=>calls.filter(c=>c.options.method==='POST'&&c.path.endsWith('/admin-transfers'))}
}
test('mounted administration requires authorized members, capability, explicit Review then Confirm; success only refreshes ordinary workspaces',async t=>{
 const h=harness(t);await h.page.activate()
 assert.ok(h.calls.findIndex(c=>c.path.endsWith('/members'))<h.calls.findIndex(c=>c.path.endsWith('/admin-transfers/capabilities')))
 await h.open();await h.submit();assert.equal(h.posts().length,0);await h.review();assert.equal(h.posts().length,0)
 assert.match(h.el('transfer-selection').textContent,/you become developer and lose management access/);assert.ok(h.el('transfer-selection').textContent.includes(displayPolicyId(actor)))
 const before=h.calls.length;await h.submit()
 assert.equal(h.posts().length,1);assert.equal(h.el('transfer-dialog').dataset.state,'committed');assert.equal(h.el('panel').hidden,true)
 for(const id of ['members','modules','invitations','control-spaces','inbox'])assert.equal(h.el(id).children.length,0)
 assert.deepEqual(h.calls.slice(before).map(c=>c.path),['/api/workspaces/alpha/admin-transfers','/api/workspaces'])
 assert.match(h.el('transfer-outcome').textContent,/committed/);assert.equal(h.el('workspace').value,'')
 const next=h.calls.length;h.el('workspace').value='beta';await h.el('workspace').fire('change');assert.equal(h.el('panel').hidden,false);assert.equal(h.el('title').textContent,'Beta')
 assert.deepEqual(h.calls.slice(next).map(call=>call.path),['/api/workspaces/beta/members','/api/workspaces/beta/admin-transfers/capabilities','/api/workspaces/beta/modules','/api/workspaces/beta/module-catalog','/api/workspaces/beta/invitations'])
 assert.equal(h.el('module-panel').hidden,false);assert.equal(h.el('invitation-panel').hidden,false);assert.equal(h.calls.slice(next).some(call=>call.path.includes('/alpha/')),false)
})
test('missing or malformed capability never enables transfer and review failures never submit',async t=>{
 let capability=true
 const h=harness(t,{respond:({path})=>path.endsWith('/admin-transfers/capabilities')?(capability?reply('not_found',404):reply({available:'true'})):path.includes('/selections/')?reply('replacement_ineligible',409):undefined})
 await h.page.activate();assert.equal(h.el('transfer-open').hidden,true);await h.open();assert.equal(h.el('transfer-dialog').open,false)
 capability=false;await h.page.activate();assert.equal(h.el('transfer-open').hidden,true)
 assert.equal(h.posts().length,0)
})
test('failed Review cannot enable Confirm, and exact412 requires explicit new review while retaining unused key',async t=>{
 let reviewFails=true,postCount=0
 const h=harness(t,{respond:({path})=>path.includes('/selections/')&&reviewFails?reply('replacement_ineligible',409):path.endsWith('/admin-transfers')&&++postCount===1?reply('stale_revision',412):undefined})
 await h.page.activate();await h.open();await h.review();await h.submit();assert.equal(h.posts().length,0);assert.equal(h.el('transfer-confirm').disabled,true)
 reviewFails=false;await h.review();await h.submit();assert.equal(h.el('transfer-dialog').dataset.state,'definite-no-write');assert.equal(h.el('transfer-confirm').disabled,true)
 await h.submit();assert.equal(h.posts().length,1);await h.review();assert.equal(h.posts().length,1);await h.submit()
 assert.equal(h.posts().length,2);assert.equal(h.posts()[0].options.headers['Idempotency-Key'],h.posts()[1].options.headers['Idempotency-Key'])
})
test('unknown remains sticky through403 404409412 with same tuple and reference, then exact replay commits',async t=>{
 let index=0
 const h=harness(t,{respond:({path})=>path.endsWith('/admin-transfers')?([()=>Promise.reject(Error('raw private failure marker')),()=>reply('forbidden',403),()=>reply('not_found',404),()=>reply('idempotency_conflict',409),()=>reply('admin_transfer_selection_changed',412),()=>reply(committed())][index++])():undefined})
 await h.page.activate();await h.open();await h.review();await h.submit()
 const reference=h.el('transfer-reference').textContent
 assert.ok(reference.includes(displayPolicyId(actor)));assert.match(h.el('transfer-status').textContent,/Result unknown/);assert.doesNotMatch(h.el('transfer-status').textContent,/raw private failure/)
 for(let i=0;i<4;i++){await h.submit();assert.equal(h.el('transfer-dialog').dataset.state,'unknown');assert.equal(h.el('transfer-reference').textContent,reference);assert.equal(h.el('transfer-confirm').disabled,false)}
 assert.equal(h.el('members').children.length,0);assert.equal(h.el('modules').children.length,0);assert.equal(h.el('invitations').children.length,0);assert.equal(h.el('transfer-dialog').open,true);assert.deepEqual(h.el('transfer-target').children.map(option=>option.value),['reader'])
 await h.submit();assert.equal(h.el('transfer-dialog').dataset.state,'committed')
 const {body,headers}=h.posts()[0].options
 for(const call of h.posts()){assert.equal(call.options.body,body);assert.deepEqual(call.options.headers,headers)}
})
test('malformed body, unexpected success status, uncertain503 and current abort are unknown, never raw errors',async t=>{
 let count=0
 const h=harness(t,{respond:({path})=>path.endsWith('/admin-transfers')?[
  ()=>({ok:true,status:200,text:async()=>{throw Error('private body marker')}}),
  ()=>({ok:true,status:202,text:async()=>JSON.stringify({data:committed()})}),
  ()=>reply('admin_transfer_unavailable',503),
  ()=>Promise.reject(Object.assign(Error('private abort marker'),{name:'AbortError'}))
 ][count++]():undefined})
 await h.page.activate();await h.open();await h.review()
 for(let i=0;i<4;i++){await h.submit();assert.equal(h.el('transfer-dialog').dataset.state,'unknown');assert.doesNotMatch(h.el('transfer-status').textContent,/private.*marker/)}
})
test('committed outcome survives ordinary-list refresh failure without any administrator reread',async t=>{
 let lists=0
 const h=harness(t,{respond:({path})=>path==='/api/workspaces'&&++lists>1?reply('unavailable',503):undefined})
 await h.page.activate();await h.open();await h.review();await h.submit()
 assert.equal(h.el('transfer-dialog').dataset.state,'committed');assert.match(h.el('transfer-outcome').textContent,/committed/);assert.match(h.el('status').textContent,/ordinary workspace list/)
 assert.equal(h.calls.filter(c=>c.path.endsWith('/members')).length,1)
})
for(const outcome of ['late200','late401','late decode error'])test(`${outcome} old review and finally cannot replace or unlock a newer busy review`,async t=>{
 const old=deferred(),newer=deferred();let reviews=0
 const h=harness(t,{respond:({path})=>path.includes('/selections/')?(++reviews===1?old.promise:newer.promise):undefined})
 await h.page.activate();await h.open();const first=h.review();await h.choose('second');const second=h.review()
 old.resolve(outcome==='late401'?reply('authentication_required',401):outcome==='late decode error'?{ok:true,status:200,text:async()=>{throw Error('private decode marker')}}:reply(reviewed()))
 await first;assert.equal(h.el('transfer-review').disabled,true);assert.equal(h.el('transfer-confirm').disabled,true);assert.equal(h.el('transfer-selection').textContent,'');assert.equal(h.el('transfer-dialog').dataset.state,'reviewing')
 newer.resolve(reply(reviewed('second')));await second;assert.match(h.el('transfer-selection').textContent,/Replacement: second/);assert.equal(h.el('transfer-confirm').disabled,false)
})
for(const change of ['input','close','scope','reload','pagehide','persisted pageshow','clear'])test(`${change} clears ownership, tuple and busy state without automatically retrying late transfer`,async t=>{
 const held=deferred();const h=harness(t,{respond:({path})=>path.endsWith('/admin-transfers')?held.promise:undefined})
 await h.page.activate();await h.open();await h.review();const pending=h.submit()
 if(change==='input')await h.choose('second')
 else if(change==='close')await h.click('transfer-close')
 else if(change==='scope'){h.el('workspace').value='beta';await h.el('workspace').fire('change')}
 else if(change==='reload')await h.click('reload')
 else if(change==='pagehide')for(const fn of h.listeners.pagehide)fn()
 else if(change==='persisted pageshow')for(const fn of h.listeners.pageshow)fn({persisted:true})
 else h.page.clear()
 held.resolve(reply(committed()));await pending
 assert.equal(h.posts().length,1);assert.equal(h.el('transfer-reference').textContent,'');assert.equal(h.el('transfer-selection').textContent,'');assert.equal(h.el('transfer-confirm').disabled,true)
 assert.notEqual(h.el('transfer-dialog').dataset.state,'committed');assert.doesNotMatch(h.el('transfer-outcome').textContent,/transfer committed/)
 if(['input','close'].includes(change))assert.match(h.el(change==='input'?'transfer-status':'transfer-outcome').textContent,/not canceled on the server/)
})
for(const lateKind of ['modules','invitations'])for(const denied of [false,true])test(`${denied?'unknown403':'success'} invalidates ignored-abort late ${lateKind} before it can refill admin DOM`,async t=>{
 const held=deferred(),entered=deferred();let posts=0
 const h=harness(t,{respond:({path})=>path===`/api/workspaces/alpha/${lateKind}`?(entered.resolve(),held.promise):denied&&path.endsWith('/admin-transfers')?(++posts===1?Promise.reject(Error('dropped')):reply('forbidden',403)):undefined})
 const activate=h.page.activate();await entered.promise
 await h.open();await h.review();await h.submit();if(denied)await h.submit()
 assert.equal(h.el('panel').hidden,true)
 held.resolve(reply(lateKind==='modules'?[{workspace_id:'alpha',key:'board',state:'disabled',capabilities:['read'],revision:1}]:[{id:'invite1',workspace_id:'alpha',workspace_name:'Alpha',principal_id:'reader',role:'viewer',state:'pending',created:1,expires:100,revision:1}]))
 await activate
 for(const id of ['members','modules','invitations'])assert.equal(h.el(id).children.length,0)
 assert.equal(h.el('module-panel').hidden,true);assert.equal(h.el('invitation-panel').hidden,true)
 if(denied){assert.equal(h.el('transfer-dialog').dataset.state,'unknown');assert.equal(h.el('transfer-confirm').disabled,false);assert.match(h.el('transfer-reference').textContent,/Local request reference/)}else assert.match(h.el('transfer-outcome').textContent,/committed/)
})
test('non-route member identities remain safely visible but programmatic Edit target input and submit never fetch',async t=>{
 const opaque=['a/b','a\\b','a%2Fb','a%5Cb','a\0\u007f\uFEFF']
 const h=harness(t,{members:[member(actor),member('reader'),...opaque.map(value=>member(value))]});await h.page.activate()
 for(const raw of opaque){
  const row=h.el('members').children.find(item=>item.children[0].textContent.startsWith(displayPolicyId(raw)+' ·'))
  assert.ok(row);const button=row.children[1].children[0];assert.equal(button.disabled,true)
  const before=h.calls.length;await button.fire('click');await h.el('edit-form').fire('submit');assert.equal(h.el('editor').open,false);assert.equal(h.calls.length,before)
  await h.open(raw);await h.review();await h.submit();assert.equal(h.calls.length,before)
 }
 await h.open();await h.review();const before=h.calls.length;h.el('transfer-target').value='a/b';await h.submit();assert.equal(h.calls.length,before)
})
test('true current401 clears private page state and retained unknown request instead of declaring rollback',async t=>{
 let posts=0;const h=harness(t,{respond:({path})=>path.endsWith('/admin-transfers')?(++posts===1?Promise.reject(Error('dropped')):reply('authentication_required',401)):undefined})
 await h.page.activate();await h.open();await h.review();await h.submit();await h.submit()
 assert.equal(h.el('session').hidden,true);assert.equal(h.el('transfer-dialog').open,false);assert.equal(h.el('transfer-reference').textContent,'');assert.equal(h.el('members').children.length,0)
 assert.match(h.el('status').textContent,/may already have completed/);assert.doesNotMatch(h.el('status').textContent,/rolled back/)
})

for(const outcome of ['late200','late401','late412','late malformed'])test(`${outcome} old submit cannot replace or unlock a newer same-session submit`,async t=>{
 const old=deferred(),newer=deferred();let posts=0
 const h=harness(t,{respond:({path})=>path.endsWith('/admin-transfers')?(++posts===1?old.promise:newer.promise):undefined})
 await h.page.activate();await h.open();await h.review();const first=h.submit()
 await h.choose('second');await h.review();const second=h.submit()
 old.resolve(outcome==='late200'?reply(committed()):outcome==='late401'?reply('authentication_required',401):outcome==='late412'?reply('stale_revision',412):{ok:true,status:200,text:async()=>'{malformed private'})
 await first;assert.equal(h.el('transfer-dialog').dataset.state,'submitting');assert.equal(h.el('transfer-confirm').disabled,true);assert.match(h.el('transfer-selection').textContent,/Replacement: second/);assert.doesNotMatch(h.el('transfer-status').textContent,/unknown|private|rejected/)
 newer.resolve(reply(committed('second')));await second;assert.equal(h.el('transfer-dialog').dataset.state,'committed')
})
test('success invalidates older ignored-abort member load from another scope without stale refill or finally unlock',async t=>{
 const held=deferred(),entered=deferred();let alphaReads=0
 const h=harness(t,{respond:({path})=>path==='/api/workspaces/alpha/members'&&++alphaReads===2?(entered.resolve(),held.promise):undefined})
 await h.page.activate();const reload=h.click('reload');await entered.promise
 h.el('workspace').value='beta';await h.el('workspace').fire('change');await h.open();await h.review();await h.submit()
 assert.equal(h.el('panel').hidden,true);held.resolve(reply([member('old-private-reader')]));await reload
 assert.equal(h.el('members').children.length,0);assert.equal(h.el('panel').hidden,true);assert.equal(h.el('workspace').disabled,false);assert.match(h.el('transfer-outcome').textContent,/committed/)
})

test('real Cookie current transfer401 calls page clear, losing private unknown tuple and session authority',async t=>{
 let posts=0;const h=harness(t,{cookieMode:true,respond:({path})=>path.endsWith('/admin-transfers')?(++posts===1?Promise.reject(Error('dropped')):reply('authentication_required',401)):undefined})
 await h.page.activate();await h.open();await h.review();await h.submit();assert.equal(h.el('transfer-dialog').dataset.state,'unknown');await h.submit()
 assert.equal(h.el('session').hidden,true);assert.equal(h.el('transfer-dialog').open,false);assert.equal(h.el('transfer-reference').textContent,'');assert.equal(h.el('members').children.length,0)
 assert.match(h.el('status').textContent,/may already have completed/);await assert.rejects(h.client.request('/api/workspaces'),/Session recheck required/)
})

for(const source of ['unknown','submitting'])test(`paired input and change retain the transfer abandonment warning: ${source}`,async t=>{
 const held=deferred(),h=harness(t,{respond:({path})=>path.endsWith('/admin-transfers')?(source==='unknown'?Promise.reject(Error('dropped')):held.promise):undefined})
 await h.page.activate();await h.open();await h.review();const pending=h.submit()
 if(source==='unknown')await pending
 assert.equal(h.el('transfer-dialog').dataset.state,source)
 const target=h.el('transfer-target');target.value='second'
 await target.fire('input');const warning=h.el('transfer-status').textContent
 assert.match(warning,/not canceled on the server/);assert.equal(h.el('transfer-reference').textContent,'')
 await target.fire('change');assert.equal(h.el('transfer-status').textContent,warning)
 if(source==='submitting'){held.resolve(reply(committed()));await pending}
 assert.equal(h.el('transfer-status').textContent,warning);assert.equal(h.el('transfer-confirm').disabled,true)
 await h.submit();assert.equal(h.posts().length,1);await h.review();assert.match(h.el('transfer-selection').textContent,/Replacement: second/)
})

for(const scenario of ['owned refresh','unrelated list','new scope','pagehide'])test(`real Cookie shell attributes confirmed transfer history only to the immediate refresh401: ${scenario}`,async t=>{
 const entered=deferred(),held=deferred();let lists=0
 const unauthorized=()=>reply('authentication_required',401)
 const h=harness(t,{shellMode:true,respond:({path})=>{
  if(path!=='/api/workspaces')return
  if(++lists===2){entered.resolve();return held.promise}
  if(scenario==='unrelated list'&&lists===3)return unauthorized()
 }})
 await flush();assert.equal(h.el('app-principal').textContent,displayPolicyId(actor));await h.open();await h.review()
 const pending=h.submit();await entered.promise;assert.match(h.el('transfer-outcome').textContent,/committed/)
 if(scenario==='unrelated list')await assert.rejects(h.client.request('/api/workspaces',{signal:new AbortController().signal}),e=>e.status===401)
 if(scenario==='new scope'){h.el('workspace').value='beta';await h.el('workspace').fire('change');assert.equal(h.el('title').textContent,'Beta')}
 if(scenario==='pagehide')for(const fn of h.listeners.pagehide)fn()
 held.resolve(unauthorized());await pending
 assert.equal(h.el('app-principal').textContent,'Not verified');assert.equal(h.el('app-sign-out').disabled,true);assert.equal(h.el('session').hidden,true)
 for(const id of ['members','modules','invitations','workspace','control-spaces','inbox','transfer-target'])assert.equal(h.el(id).children.length,0)
 for(const id of ['transfer-outcome','transfer-selection','transfer-reference','title'])assert.equal(h.el(id).textContent,'')
 assert.equal(h.el('transfer-dialog').open,false);assert.equal(h.el('transfer-confirm').disabled,true)
 assert.equal(h.posts().length,1);await assert.rejects(h.client.request('/api/workspaces'),/Session recheck required/)
 if(scenario==='owned refresh'){
  assert.equal(h.el('status').textContent,'Your session expired. The administrator transfer was already confirmed committed.')
  await h.click('app-recheck');assert.equal(h.el('app-principal').textContent,displayPolicyId(actor));assert.doesNotMatch(h.el('status').textContent,/confirmed committed/)
 }else{
  assert.doesNotMatch(h.el('status').textContent,/confirmed committed/)
  if(scenario!=='pagehide')assert.match(h.el('status').textContent,/session expired or was rejected/)
 }
})
