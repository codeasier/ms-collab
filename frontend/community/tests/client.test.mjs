import test from 'node:test'
import assert from 'node:assert/strict'
import {sharedLabelView} from '../board.mjs'
import {scopeFrom,baseURL,createClient,validateCapabilities,validatePage,loadSnapshot,validateMutation} from '../client.mjs'
const scope=scopeFrom('alpha','source','sample/project')
const row=(number=1)=>({...scope,kind:'issue',number,title:'<img src=x onerror=alert(1)>',state:'open',updated_at:'2026-01-01T00:00:00Z',labels:['<b>plain</b>'],fingerprint:number.toString(16).padStart(64,'0'),approved:number===1})
const page=(items,offset=0,total=items.length)=>({schema:'ms-collab/community-board/v1',workspace_id:'alpha',coverage:'observed_subset',scope,revision:2,available:true,sharing:'explicit_content_approval',view:'review',sources:[{...scope,observed_at:'2026-01-02T00:00:00Z'}],offset,limit:100,total,items})
test('scope cannot add origin, query, encoded traversal or path segments',()=>{
 assert.equal(baseURL(scope),'/api/workspaces/alpha/community/source/sample/project')
 for(const args of [['../','source','a/b'],['a','https://x','b/c'],['a','c','x/y/z'],['a','c','x/%2e%2e'],['a','c','x/y?token=a']])assert.throws(()=>scopeFrom(...args))
})
test('credential is memory-only and requests are same-origin without cookies or redirects',async()=>{
 const calls=[], client=createClient({fetcher:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({data:{}})}}})
 client.connect('synthetic-token');await client.request(scope,'capabilities');await client.request(scope,'approve',{body:{fingerprints:[]},revision:2})
 assert.ok(!JSON.stringify(calls.map(c=>c.url)).includes('synthetic-token'));assert.equal(calls[0].options.headers.Authorization,'Bearer synthetic-token');assert.equal(calls[0].options.credentials,'omit');assert.equal(calls[0].options.redirect,'error');assert.equal(calls[1].options.headers['If-Match'],'"2"')
 client.clear();await assert.rejects(client.request(scope,'items'));assert.throws(()=>client.connect('a b'))
})
test('capabilities are explicit and response scope cannot be switched',()=>{
 const data={scope,revision:2,capabilities:{items:true,review:false,approve:false,refresh:false}}
 validateCapabilities(data,scope)
 for(const change of [{scope:{...scope,workspace_id:'other'}},{revision:0},{capabilities:{items:true}},{capabilities:{items:true,review:false,approve:true,refresh:false}}])assert.throws(()=>validateCapabilities({...data,...change},scope))
})
test('review requires fingerprints, consistent versions, bounded data and safe row identity',()=>{
 validatePage(page([row()]),scope,'review',2,0)
 for(const change of [{revision:3},{view:'items'},{total:10001},{offset:1},{items:[{...row(),workspace_id:'beta'}]},{items:[{...row(),fingerprint:'not-digest'}]},{items:[{...row(),kind:'issue',state:'merged'}]}])assert.throws(()=>validatePage({...page([row()]),...change},scope,'review',2,0))
})
test('all review pages load before full approval replacement and retain off-page approvals',async()=>{
 const first=Array.from({length:100},(_,i)=>row(i+1)), last={...row(101),approved:true}, calls=[]
 const snapshot=await loadSnapshot({request:async(_s,_v,{offset})=>{calls.push(offset);return offset?page([last],100,101):page(first,0,101)}},scope,'review',2)
 assert.deepEqual(calls,[0,100]);assert.equal(snapshot.items.length,101);assert.equal(snapshot.items.filter(x=>x.approved).length,2)
 await assert.rejects(loadSnapshot({request:async(_s,_v,{offset})=>offset?{...page([last],100,101),revision:3}:page(first,0,101)},scope,'review',2))
 await assert.rejects(loadSnapshot({request:async(_s,_v,{offset})=>offset?page([row(1)],100,101):page(first,0,101)},scope,'review',2))
})
test('empty snapshot and mutation results remain scoped',async()=>{
 assert.equal((await loadSnapshot({request:async()=>({scope,revision:2,available:false,items:[]})},scope,'items',2)).available,false)
 validateMutation({scope,revision:3,approved_count:1},scope,2,'approve',1)
 assert.throws(()=>validateMutation({scope,revision:3,approved_count:2},scope,2,'approve',1))
})
test('service error text is fixed and never echoes upstream private payload',async()=>{
 const client=createClient({fetcher:async()=>({ok:false,status:412,json:()=>{throw Error('must not read')}})})
 client.connect('synthetic');await assert.rejects(client.request(scope,'capabilities'),/changed elsewhere/)
})

test('abandoned snapshot stops before new page requests even if transport cancellation fails',async()=>{
 for(const abortSignal of [false,true]){
  let resolve,calls=0,current=true;const controller=new AbortController()
  const client={request:()=>{calls++;return new Promise(r=>resolve=r)}}
  const loading=loadSnapshot(client,scope,'review',2,controller.signal,()=>current)
  current=false;if(abortSignal)controller.abort()
  resolve(page(Array.from({length:100},(_,i)=>row(i+1)),0,101))
  await assert.rejects(loading,e=>e.name==='AbortError');assert.equal(calls,1)
 }
 const controller=new AbortController();controller.abort();let called=0
 await assert.rejects(loadSnapshot({request:()=>{called++}},scope,'review',2,controller.signal),e=>e.name==='AbortError');assert.equal(called,0)
})

const reader={items:true,review:false,approve:false,refresh:false}
const noFilters={kind:'',state:'',query:''}
const sharedRow=(number,labels=[],extra={})=>({...scope,kind:'issue',number,title:'Fixture',state:'open',updated_at:'2026-01-01T00:00:00Z',labels,...extra})
const project=(items,filter=noFilters,exact=null)=>sharedLabelView('items',reader,{available:true,items},filter,exact)

test('approved label counts span all loaded pages, count each row once and distinguish issue and pull numbers',async()=>{
 const first=Array.from({length:100},(_,i)=>sharedRow(i+1,i===0?['all','both','both']:['all']))
 const last=[sharedRow(1,['both'],{kind:'pull'}),sharedRow(101,['both','tail'])], offsets=[]
 const snapshot=await loadSnapshot({request:async(_scope,view,{offset})=>{
  assert.equal(view,'items');offsets.push(offset)
  return {...page(offset===0?first:last,offset,102),view:'items'}
 }},scope,'items',2)
 const ranked=sharedLabelView('items',reader,snapshot,noFilters,'both')
 assert.deepEqual(offsets,[0,100]);assert.equal(ranked.baseCount,102);assert.equal(ranked.distinctCount,3)
 assert.deepEqual(ranked.top,[{label:'all',count:100},{label:'both',count:3},{label:'tail',count:1}])
 assert.deepEqual(ranked.rows.map(r=>[r.kind,r.number]),[['issue',1],['pull',1],['issue',101]])
})

test('label ranking follows kind state and ordinary query first; exact selection never matches a title',()=>{
 const rows=[sharedRow(1,['blue','base'],{title:'Topic retained'}),sharedRow(2,['base','green'],{title:'Topic blue'}),
  sharedRow(3,['blue','closed-only'],{state:'closed',title:'Topic'}),sharedRow(4,['blue','unrelated']),
  sharedRow(1,['blue','pull-only'],{kind:'pull',title:'Topic'}),sharedRow(5,['topic via label','blue'])]
 const filters={query:' TOPIC ',kind:'issue',state:'open'}, all=project(rows,filters), selected=project(rows,filters,'blue')
 const counts=[{label:'base',count:2},{label:'blue',count:2},{label:'green',count:1},{label:'topic via label',count:1}]
 assert.deepEqual(all.rows.map(r=>r.number),[1,2,5]);assert.deepEqual(selected.rows.map(r=>r.number),[1,5])
 for(const view of [all,selected]){assert.equal(view.baseCount,3);assert.equal(view.distinctCount,4);assert.deepEqual(view.top,counts)}
})

test('label identity preserves case, spaces, Unicode spelling and ordinary object-key strings with stable ties',()=>{
 const labels=['😀','\uE000','é','e\u0301','constructor','__proto__','toString','bug','Bug',' bug ']
 const rows=labels.map((s,i)=>sharedRow(i+1,[s]))
 const ordered=[' bug ','Bug','__proto__','bug','constructor','e\u0301','toString','é','😀','\uE000']
 const top=ordered.map(label=>({label,count:1}));assert.deepEqual(project(rows).top,top)
 assert.deepEqual(project([...rows].reverse()).top,top)
 for(const [i,label] of labels.entries())assert.deepEqual(project(rows,noFilters,label).rows.map(r=>r.number),[i+1],label)
 assert.deepEqual(project(rows,noFilters,'BUG').rows,[])
})

test('rank pages of ten keep full exact filtering, including off-page labels with literal line breaks',()=>{
 const rows=Array.from({length:12},(_,i)=>sharedRow(i+1,['tag-'+String(12-i).padStart(2,'0')]))
 const raw='z\r\nlabel';rows.push(sharedRow(13,[raw]))
 const expected=Array.from({length:10},(_,i)=>({label:'tag-'+String(i+1).padStart(2,'0'),count:1}))
 const snapshot={available:true,items:rows}, second=sharedLabelView('items',reader,snapshot,noFilters,raw,1)
 assert.deepEqual(second.top,[{label:'tag-11',count:1},{label:'tag-12',count:1},{label:raw,count:1}])
 assert.deepEqual(second.rows.map(r=>r.number),[13]);assert.equal(second.baseCount,13);assert.equal(second.distinctCount,13)
 for(const [label,numbers] of [['tag-12',[1]],[raw,[13]],['zlabel',[]],['not-listed',[]],['',[]]]){
  const result=project(rows,noFilters,label);assert.equal(result.baseCount,13);assert.equal(result.distinctCount,13)
  assert.deepEqual(result.top,expected);assert.deepEqual(result.rows.map(r=>r.number),numbers)
 }
 assert.deepEqual(project([]),{baseCount:0,distinctCount:0,top:[],rows:[]})
})

test('shared label controls reject review material and oversized or duplicate snapshots even after filtering',()=>{
 const first=sharedRow(1,['public']), allowed={items:true,review:true,approve:true,refresh:true}
 const use=(view,capabilities,snapshot)=>sharedLabelView(view,capabilities,snapshot,{...noFilters,query:'public'})
 const ok={available:true,items:[first]}
 assert.equal(use('review',allowed,ok),null);assert.equal(use('items',{...allowed,items:false},ok),null)
 assert.equal(use('items',reader,{available:false,items:[]}),null)
 for(const extra of [{approved:false},{fingerprint:'a'.repeat(64)}]){
  assert.equal(use('items',allowed,{available:true,items:[first,sharedRow(2,['other'],extra)]}),null)
 }
 assert.equal(use('items',reader,{available:true,items:[first,{...first,labels:['other']}]}),null)
 const max=[first,...Array.from({length:199},(_,i)=>sharedRow(i+2,['other']))]
 assert.equal(use('items',reader,{available:true,items:max}).baseCount,1)
 assert.equal(use('items',reader,{available:true,items:[...max,sharedRow(201,['other'])]}),null)
})
