// Test-only single-owner routing. Never register overlapping page/context routes.
function createScenarioRouter({origin,onExternal=()=>{}}){
 const rules=new Map(),active=new Set()
 return {
  set(path,handler){
   if(typeof path!=='string'||!path.startsWith('/')||path.includes('?')||typeof handler!=='function')throw new Error('Invalid scenario route')
   rules.set(path,handler)
  },
  remove(path){rules.delete(path)},
  async handle(route){
   const url=new URL(route.request().url())
   // Snapshot the selected scenario before awaiting anything. Removing/replacing
   // it affects future requests only, never ownership of an in-flight request.
   const scenario=url.origin===origin?rules.get(url.pathname):undefined
   const operation=Promise.resolve().then(async()=>{
    if(url.origin!==origin){onExternal();return await route.abort()}
    if(scenario)return await scenario(route)
    return await route.continue()
   })
   active.add(operation)
   try{return await operation}finally{active.delete(operation)}
  },
  async drain(){while(active.size)await Promise.all([...active])},
 }
}
module.exports={createScenarioRouter}
