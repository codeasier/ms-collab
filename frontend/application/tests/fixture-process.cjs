// Test-only ownership of a synthetic child process. A forced reap is a failed
// cleanup, never a successful retry or a substitute for browser assertions.
async function stopSyntheticFixture(child,{graceMs=5000,killMs=5000}={}){
 if(!child)return
 if(child.exitCode!==null||child.signalCode!==null){
  if(child.exitCode!==0)throw Error('Synthetic fixture exited unexpectedly')
  return
 }
 let onExit,onError
 const exited=new Promise((resolve,reject)=>{
  onExit=(code,signal)=>resolve({code,signal});onError=reject
  child.once('exit',onExit);child.once('error',onError)
 })
 async function bounded(ms){let timer;try{return await Promise.race([exited,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Synthetic fixture cleanup timed out')),ms)})])}finally{clearTimeout(timer)}}
 try{
  child.kill('SIGTERM')
  let result
  try{result=await bounded(graceMs)}catch(error){
   if(child.exitCode===null&&child.signalCode===null){child.kill('SIGKILL');await bounded(killMs)}
   throw error
  }
  if(result.code!==0||result.signal!==null)throw Error('Synthetic fixture did not stop cleanly')
 }finally{child.removeListener('exit',onExit);child.removeListener('error',onError)}
}
// Attempt every owned child even when an earlier cleanup fails. allSettled is
// intentional: a forced reap remains an error, but must not strand its siblings.
async function stopSyntheticFixtures(children,options){
 const results=await Promise.allSettled(children.map(child=>stopSyntheticFixture(child,options)))
 const failures=results.filter(result=>result.status==='rejected').map(result=>result.reason)
 if(failures.length)throw new AggregateError(failures,'Synthetic fixture batch cleanup failed')
}
module.exports={stopSyntheticFixture,stopSyntheticFixtures}
