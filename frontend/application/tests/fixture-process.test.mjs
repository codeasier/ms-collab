import test from 'node:test'
import assert from 'node:assert/strict'
import {spawn,execFileSync} from 'node:child_process'
import {fileURLToPath} from 'node:url'
import {once} from 'node:events'
import {createRequire} from 'node:module'
const {stopSyntheticFixture,stopSyntheticFixtures}=createRequire(import.meta.url)('./fixture-process.cjs')
async function fixture(ignore){
 const child=spawn(process.execPath,['-e',`process.on('SIGTERM',()=>{${ignore?'':'process.exit(0)'}});process.stdout.write('ready');setInterval(()=>{},1000)`],{stdio:['ignore','pipe','pipe']})
 await once(child.stdout,'data');return child
}
test('synthetic fixture exits gracefully and is reaped with no remaining listeners',async()=>{
 const child=await fixture(false)
 try{await stopSyntheticFixture(child);assert.equal(child.exitCode,0);assert.equal(child.signalCode,null);assert.equal(child.listenerCount('exit'),0);await stopSyntheticFixture(child)}finally{if(child.exitCode===null&&child.signalCode===null){child.kill('SIGKILL');await once(child,'exit')}}
})
test('ignored SIGTERM is force-reaped but still fails acceptance, never passes by retry',async()=>{
 const child=await fixture(true)
 try{await assert.rejects(stopSyntheticFixture(child,{graceMs:25,killMs:3000}),/cleanup timed out/);assert.equal(child.signalCode,'SIGKILL');assert.equal(child.listenerCount('exit'),0)}finally{if(child.exitCode===null&&child.signalCode===null){child.kill('SIGKILL');await once(child,'exit')}}
})

test('standalone unified, community, connection and assistant browser entry points parse before hosted acceptance',()=>{
 for(const file of ['browser.cjs','community-browser.cjs','connections.cjs','assistant-browser.cjs'])execFileSync(process.execPath,['--check',fileURLToPath(new URL(file,import.meta.url))],{stdio:'pipe'})
})


test('batch cleanup reaps every child even when the first ignores SIGTERM, and still fails',async()=>{
 const children=[]
 try{
  children.push(await fixture(true));children.push(await fixture(false))
  await assert.rejects(stopSyntheticFixtures(children,{graceMs:500,killMs:3000}),error=>{
   assert.ok(error instanceof AggregateError);assert.equal(error.errors.length,1)
   assert.match(error.errors[0].message,/cleanup timed out/);return true
  })
  assert.equal(children[0].signalCode,'SIGKILL')
  assert.equal(children[1].exitCode,0);assert.equal(children[1].signalCode,null)
  for(const child of children)assert.equal(child.listenerCount('exit'),0)
 }finally{
  await Promise.all(children.map(async child=>{
   if(child.exitCode===null&&child.signalCode===null){const exited=once(child,'exit');child.kill('SIGKILL');await exited}
  }))
 }
})

test('batch cleanup retains every failure rather than succeeding after one child was reaped',async()=>{
 const children=[]
 try{
  children.push(await fixture(true));children.push(await fixture(true))
  await assert.rejects(stopSyntheticFixtures(children,{graceMs:25,killMs:3000}),error=>{
   assert.ok(error instanceof AggregateError);assert.equal(error.errors.length,2)
   for(const failure of error.errors)assert.match(failure.message,/cleanup timed out/)
   return true
  })
  for(const child of children){assert.equal(child.signalCode,'SIGKILL');assert.equal(child.listenerCount('exit'),0)}
 }finally{
  await Promise.all(children.map(async child=>{
   if(child.exitCode===null&&child.signalCode===null){const exited=once(child,'exit');child.kill('SIGKILL');await exited}
  }))
 }
})
