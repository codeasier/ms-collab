import test from 'node:test'
import assert from 'node:assert/strict'
import {spawn,execFileSync} from 'node:child_process'
import {fileURLToPath} from 'node:url'
import {once} from 'node:events'
import {createRequire} from 'node:module'
const {stopSyntheticFixture}=createRequire(import.meta.url)('./fixture-process.cjs')
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

test('standalone community, connection and assistant browser entry points parse before hosted acceptance',()=>{
 for(const file of ['community-browser.cjs','connections.cjs','assistant-browser.cjs'])execFileSync(process.execPath,['--check',fileURLToPath(new URL(file,import.meta.url))],{stdio:'pipe'})
})
