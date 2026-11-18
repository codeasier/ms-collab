import test from 'node:test'
import assert from 'node:assert/strict'
import {mountModuleStatus} from '../module-status.mjs'
import {createSessionClient} from '../client.mjs'

const record=(workspace='alpha',key='pipelines',state='disabled',revision=1)=>({workspace_id:workspace,key,state,revision})
const spaces=()=>[{id:'alpha',name:'Alpha <script>literal</script>',kind:'product',state:'active',revision:1},{id:'beta',name:'Beta',kind:'shared',state:'archived',revision:2},{id:'commons',name:'Commons',kind:'shared',state:'active',revision:1}]
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}
const response=(data,status=200)=>({ok:status===200,status,text:async()=>JSON.stringify({data})})
class Element{
 constructor(){this.value='';this.textContent='';this.hidden=false;this.disabled=false;this.children=[];this.handlers={};this.attributes={}}
 addEventListener(name,handler){this.handlers[name]=handler}
 append(...items){this.children.push(...items)}
 replaceChildren(...items){this.children=items;this.value=''}
 setAttribute(key,value){this.attributes[key]=value}
 fire(name){return this.handlers[name]?.({preventDefault(){}})}
}
function harness(read=async(workspace,key)=>record(workspace,key),supplied){
 const elements=new Map(),calls=[],lookups=[]
 for(const id of ['','form','workspace','key','check','clear','message','result','result-workspace','result-key','result-state','result-revision'])elements.set(id,new Element())
 const doc={getElementById:id=>{lookups.push(id);assert.ok(id==='module-status'||id.startsWith('module-status-'));return elements.get(id.replace(/^module-status-?/,''))},createElement:()=>new Element()}
 const session=supplied||{readModuleStatus:(workspace,key,options)=>{calls.push({workspace,key,options});return read(workspace,key,options)},request(){throw Error('No directory request permitted')},inspect(){throw Error('No session inspection permitted')},clear(){throw Error('No global clear permitted')}}
 const controller=mountModuleStatus({session,doc}),el=id=>elements.get(id)
 const select=(workspace,key)=>{el('workspace').value=workspace;el('workspace').fire('change');el('key').value=key;el('key').fire('input')}
 return {controller,elements,calls,lookups,el,select,submit:()=>el('form').fire('submit'),clear:()=>el('clear').fire('click')}
}

test('activation consumes a copied supplied list synchronously with no automatic request',()=>{
 const h=harness(),list=spaces()
 assert.equal(h.el('').hidden,true);assert.equal(h.controller.activate(list),undefined)
 assert.equal(h.el('').hidden,false);assert.equal(h.el('workspace').value,'');assert.equal(h.el('key').value,'');assert.equal(h.el('result').hidden,true);assert.equal(h.el('check').disabled,true)
 assert.deepEqual(h.el('workspace').children.map(option=>option.value),['','alpha','beta','commons'])
 assert.equal(h.el('workspace').children[1].textContent,'Alpha <script>literal</script> · alpha')
 list[0].id='elsewhere';list[0].name='Changed';list.length=0
 h.select('alpha','pipelines');assert.equal(h.el('check').disabled,false);assert.equal(h.calls.length,0)
 h.controller.clear();h.controller.clear()
 assert.equal(h.el('').hidden,true);assert.equal(h.el('workspace').children.length,0);assert.equal(h.el('workspace').value,'');assert.equal(h.el('key').value,'');assert.equal(h.el('message').textContent,'');assert.equal(h.calls.length,0)
})

test('empty accessible workspaces and invalid programmatic queries never call the transport',async()=>{
 const h=harness();h.controller.activate([])
 assert.match(h.el('message').textContent,/No accessible workspaces/);assert.equal(h.el('workspace').disabled,true);assert.equal(h.el('key').disabled,true)
 h.select('alpha','pipelines');await h.submit();assert.equal(h.calls.length,0)
 h.controller.activate(spaces())
 for(const key of ['', ' pipelines','pipelines ','pipelines\n','a/b','a%2Fb','a?x=1','a'.repeat(201),'<script>']){
  h.select('alpha',key);assert.equal(h.el('check').disabled,true);await h.submit()
 }
 h.select('unknown','pipelines');await h.submit();assert.equal(h.calls.length,0)
 h.controller.clear();h.select('alpha','pipelines');await h.submit();assert.equal(h.calls.length,0)
})

test('only explicit Check renders the exact four values and each repeat obtains a new observation',async()=>{
 const h=harness(async(workspace,key)=>record(workspace,key,'enabled',Number.MAX_SAFE_INTEGER));h.controller.activate(spaces())
 const key='Module._:-'.padEnd(200,'z');h.select('commons',key)
 assert.equal(h.calls.length,0);await h.submit()
 assert.equal(h.calls.length,1);assert.equal(h.calls[0].workspace,'commons');assert.equal(h.calls[0].key,key)
 assert.equal(h.el('result').hidden,false);assert.equal(h.el('result-workspace').textContent,'commons');assert.equal(h.el('result-key').textContent,key);assert.equal(h.el('result-state').textContent,'enabled');assert.equal(h.el('result-revision').textContent,String(Number.MAX_SAFE_INTEGER))
 assert.equal(h.el('check').disabled,false);assert.equal(h.el('clear').disabled,false);assert.equal(h.el('form').attributes['aria-busy'],'false')
 await h.submit();assert.equal(h.calls.length,2)
 assert.equal(h.calls[0].options.isCurrent(),false);assert.equal(h.calls[1].options.isCurrent(),false)
})

test('duplicate submits are ignored while busy but Clear remains available and allows a new request',async()=>{
 const first=deferred(),second=deferred();let count=0
 const h=harness(()=>++count===1?first.promise:second.promise);h.controller.activate(spaces());h.select('alpha','pipelines')
 const old=h.submit();await h.submit()
 assert.equal(h.calls.length,1);assert.equal(h.el('check').disabled,true);assert.equal(h.el('clear').disabled,false);assert.equal(h.el('workspace').disabled,false);assert.equal(h.el('key').disabled,false)
 h.clear()
 assert.equal(h.calls[0].options.signal.aborted,true);assert.equal(h.calls[0].options.isCurrent(),false);assert.equal(h.el('workspace').value,'');assert.equal(h.el('key').value,'');assert.equal(h.el('message').textContent,'');assert.equal(h.el('workspace').children.length,4)
 h.select('beta','pipelines');const next=h.submit();assert.equal(h.calls.length,2)
 first.resolve(record());await old
 assert.equal(h.el('check').disabled,true);assert.equal(h.el('form').attributes['aria-busy'],'true');assert.equal(h.el('result').hidden,true)
 second.resolve(record('beta','pipelines','disabling',2));await next
 assert.equal(h.el('result-state').textContent,'disabling');assert.equal(h.el('check').disabled,false)
})

test('editing workspace or key invalidates observations without requests, including away and back',async()=>{
 const h=harness();h.controller.activate(spaces());h.select('alpha','pipelines');await h.submit()
 h.el('workspace').value='beta';h.el('workspace').fire('change')
 assert.equal(h.el('key').value,'pipelines');assert.equal(h.el('result').hidden,true);assert.equal(h.el('result-key').textContent,'');assert.equal(h.calls.length,1)
 h.el('workspace').value='alpha';h.el('workspace').fire('change');assert.equal(h.el('result').hidden,true)
 await h.submit();h.el('key').value='other';h.el('key').fire('input');h.el('key').value='pipelines';h.el('key').fire('input')
 assert.equal(h.el('result').hidden,true);assert.equal(h.el('result-state').textContent,'');assert.equal(h.calls.length,2)
})

for(const kind of ['success','409','401','malformed','body rejection','fetch rejection'])test(`ignored abort and old ${kind} cannot render or release newer busy ownership`,async()=>{
 const old=deferred(),newer=deferred();let count=0
 const h=harness(()=>++count===1?old.promise:newer.promise);h.controller.activate(spaces());h.select('alpha','pipelines')
 const prior=h.submit();h.select('beta','pipelines');const next=h.submit()
 assert.equal(h.calls[0].options.signal.aborted,true);assert.equal(h.calls[0].options.isCurrent(),false);assert.equal(h.calls[1].options.isCurrent(),true)
 if(kind==='success')old.resolve(record())
 else if(kind==='malformed')old.resolve({secret:'private payload'})
 else old.reject(Object.assign(new Error('private '+kind),{status:Number(kind)||undefined}))
 await prior
 assert.equal(h.el('check').disabled,true);assert.equal(h.el('form').attributes['aria-busy'],'true');assert.equal(h.el('result').hidden,true);assert.match(h.el('message').textContent,/Checking the stored/);assert.equal(h.calls[1].options.isCurrent(),true)
 await h.submit();assert.equal(h.calls.length,2)
 newer.resolve(record('beta','pipelines','disabling',2));await next
 assert.equal(h.el('result-workspace').textContent,'beta');assert.equal(h.el('result-state').textContent,'disabling');assert.equal(h.el('check').disabled,false)
})

for(const kind of ['syntax','fetch','body',401,403,404,409,503])test(`current ${kind} failure clears prior success and renders only fixed safe messages`,async()=>{
 let fail=false
 const error=Object.assign(new Error('PRIVATE URL / payload / principal'),{status:typeof kind==='number'?kind:undefined})
 if(kind==='syntax')error.name='SyntaxError'
 const h=harness(async()=>{if(fail)throw error;return record()});h.controller.activate(spaces());h.select('alpha','pipelines');await h.submit()
 assert.equal(h.el('result').hidden,false);fail=true;await h.submit()
 assert.equal(h.el('result').hidden,true);assert.equal(h.el('result-state').textContent,'');assert.equal(h.el('message').textContent.includes('PRIVATE'),false);assert.equal(h.el('message').textContent.includes('payload'),false);assert.equal(h.el('check').disabled,false);assert.equal(h.calls.length,2)
 if(kind===404)assert.equal(h.el('message').textContent,'This workspace/module is unavailable to your current identity.')
 if(kind===409)assert.equal(h.el('message').textContent.includes('disabled'),false)
})

test('controller fails closed on full-instance or foreign-tuple data even from an untrusted adapter',async()=>{
 for(const data of [{...record(),capabilities:['read']},record('beta'),{...record(),revision:0},{...record(),state:'ready'}]){
  const h=harness(async()=>data);h.controller.activate(spaces());h.select('alpha','pipelines');await h.submit()
  assert.equal(h.el('result').hidden,true);assert.match(h.el('message').textContent,/could not be verified/)
 }
})

test('deactivation and reactivation abandon pending results and do not restore any query',async()=>{
 const held=deferred(),h=harness(()=>held.promise);h.controller.activate(spaces());h.select('alpha','pipelines');const pending=h.submit()
 h.controller.clear();h.controller.activate(spaces())
 assert.equal(h.calls[0].options.signal.aborted,true);assert.equal(h.el('workspace').value,'');assert.equal(h.el('key').value,'');assert.equal(h.el('result').hidden,true)
 held.resolve(record());await pending
 assert.equal(h.el('result').hidden,true);assert.equal(h.el('result-state').textContent,'');assert.equal(h.el('check').disabled,true);assert.equal(h.calls.length,1)
})

test('real transport plus controller fence late same-session 401 while a newer response remains held',async()=>{
 const old=deferred(),newer=deferred(),invalid=[];let count=0,h
 const session=createSessionClient({onInvalid:message=>{invalid.push(message);h.controller.clear()},fetcher:async path=>path==='/auth/session'?response({principal_id:'reader'}):++count===1?old.promise:newer.promise})
 await session.inspect();h=harness(undefined,session);h.controller.activate(spaces());h.select('alpha','pipelines')
 const prior=h.submit();h.select('beta','pipelines');const next=h.submit()
 old.resolve(response({},401));await prior
 assert.equal(invalid.length,0);assert.equal(h.el('').hidden,false);assert.equal(h.el('form').attributes['aria-busy'],'true');assert.equal(h.el('check').disabled,true)
 newer.resolve(response(record('beta','pipelines','disabling',2)));await next
 assert.equal(h.el('result-state').textContent,'disabling');assert.equal(invalid.length,0)
})

test('current transport 401 clears the section without a catch or finally reviving it',async()=>{
 let h,invalid=0
 const session=createSessionClient({onInvalid:()=>{invalid++;h.controller.clear()},fetcher:async path=>path==='/auth/session'?response({principal_id:'reader'}):response({},401)})
 await session.inspect();h=harness(undefined,session);h.controller.activate(spaces());h.select('commons','pipelines');await h.submit()
 assert.equal(invalid,1);assert.equal(h.el('').hidden,true);assert.equal(h.el('workspace').value,'');assert.equal(h.el('key').value,'');assert.equal(h.el('message').textContent,'');assert.equal(h.el('result').hidden,true);assert.equal(h.el('check').disabled,true)
})

for(const kind of ['malformed JSON','body rejection','fetch rejection'])test(`real current ${kind} never displays transport/parser sentinel content`,async()=>{
 const sentinel='STATUS_TEST_DO_NOT_DISPLAY',session=createSessionClient({fetcher:async path=>{
  if(path==='/auth/session')return response({principal_id:'reader'})
  if(kind==='fetch rejection')throw Error(sentinel)
  return {ok:true,status:200,text:async()=>{if(kind==='body rejection')throw Error(sentinel);return '{"private":"'+sentinel+'"'}}
 }})
 await session.inspect();const h=harness(undefined,session);h.controller.activate(spaces());h.select('alpha','pipelines');await h.submit()
 assert.equal(h.el('result').hidden,true);assert.equal(h.el('message').textContent,'The module status could not be verified. Check again explicitly; nothing was retried.')
 for(const element of h.elements.values())assert.equal(element.textContent.includes(sentinel),false)
})
