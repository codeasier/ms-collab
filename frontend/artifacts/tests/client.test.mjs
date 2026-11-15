import test from 'node:test'
import assert from 'node:assert/strict'
import {createClient,validateResult,validateQuarters,validateScope,validKey} from '../client.mjs'
const scope={workspace_id:'alpha',connection_id:'store',bucket_id:'releases'}
const request={scope,selected:'2026Q1',definition:{path:'',recursive:false},page:1,pageSize:10}
const row={key:'builds/',name:'builds',parent:'',modified_at:null,size:0,is_dir:true,virtual:true}
const data={scope,quarter:'2026Q1',observed_at:'2026-01-03T00:00:00Z',quarter_available:true,catalog_revision:1,total:1,page:1,page_size:10,items:[row]}
test('scope and keys reject paths, URLs and wrong tuples',()=>{
 assert.deepEqual(validateScope(scope),scope)
 for(const value of ['../alpha','a/b','https://x','','alpha\n','alpha\r',1])assert.throws(()=>validateScope({...scope,workspace_id:value}))
 assert.throws(()=>validateScope({...scope,actor:'admin'}))
 for(const value of ['/root','../file','a/../file','https://x','a\\b'])assert.equal(validKey(value),false)
 assert.equal(validKey('builds/<svg onload=alert(1)>.txt'),true)
})
test('exact scope, quarter and request pagination are validated',()=>{
 assert.equal(validateResult(data,request),data)
 for(const values of [{scope:{...scope,workspace_id:'beta'}},{quarter:'2025Q4'},{page:2},{page_size:20},{total:0},{observed_at:'bad'},{catalog_revision:0},{quarter_available:false},{view_revision:2}])assert.throws(()=>validateResult({...data,...values},request))
 assert.throws(()=>validateResult(data,{...request,view:'saved'}))
 assert.equal(validateResult({...data,view_revision:1},{...request,view:'saved'}).view_revision,1)
})
test('malformed entry shape, duplicate keys and untrusted links fail',()=>{
 for(const values of [{key:'https://evil.invalid/'},{parent:'secret/'},{name:'other'},{size:-1},{size:2**60},{virtual:'yes'},{is_dir:false},{modified_at:'bad'}])assert.throws(()=>validateResult({...data,items:[{...row,...values}]},request))
 assert.throws(()=>validateResult({...data,total:2,items:[row,row]},request))
})
test('quarter discovery validates exact scope and duplicates',()=>{
 const value={scope,catalog_revision:1,quarters:['2026Q1','2025Q4']}
 assert.equal(validateQuarters(value,scope),value)
 for(const values of [{scope:{...scope,bucket_id:'other'}},{quarters:['2026Q1','2026Q1']},{quarters:['now']},{quarters:['2026Q1\n']},{catalog_revision:-1}])assert.throws(()=>validateQuarters({...value,...values},scope))
})
test('request authority stays in bearer header and routes cannot be supplied',async()=>{
 const calls=[];const client=createClient({fetcher:async(path,options)=>{calls.push([path,options]);return {ok:true,json:async()=>({data})}}})
 client.connect('synthetic-reader');await client.query(request)
 const [path,options]=calls[0];assert.equal(path,'/api/workspaces/alpha/artifacts/store/releases/query')
 assert.equal(options.headers.Authorization,'Bearer synthetic-reader');assert.equal(options.credentials,'omit');assert.equal(options.redirect,'error');assert.equal(options.cache,'no-store')
 assert.ok(!path.includes('synthetic-reader'));assert.ok(!options.body.includes('actor'));assert.equal(options.method,'POST')
 client.clear();await assert.rejects(()=>client.query(request),/Disconnected/)
})
test('missing view is not retried as broad catalog and raw errors are not exposed',async()=>{
 const calls=[];const client=createClient({fetcher:async(path)=>{calls.push(path);return {ok:false,status:404,json:async()=>({error:'private path or token'})}}});client.connect('synthetic-reader')
 await assert.rejects(()=>client.query({...request,view:'missing'}),error=>error.status===404&&!error.message.includes('private'))
 assert.equal(calls.length,1);assert.match(calls[0],/\/views\/missing\?/)
})
test('invalid credentials and parameters rejected before network',async()=>{
 const client=createClient({fetcher:()=>{throw new Error('network called')}})
 for(const value of ['',null,'with spaces','x'.repeat(8193)])assert.throws(()=>client.connect(value))
 client.connect('synthetic-reader');await assert.rejects(()=>client.query({...request,view:'../other'}),/Invalid query/)
 await assert.rejects(()=>client.query({...request,pageSize:1000}),/Invalid query/)
})
