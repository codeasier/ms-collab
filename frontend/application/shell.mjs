import {mountCommunityPage} from '../community/board.mjs'
import {createCommunitySessionClient} from './community-client.mjs'
import {createSessionClient,createResponsibilitySessionClient,validateBootstrap,authorizationTarget} from './client.mjs'
import {validateWorkspaces} from '/manage/client.mjs'
import {mountWorkspacePage} from '/manage/admin.mjs'
import {mountResponsibilityPage} from '../responsibility/browser.mjs'
const el=id=>document.getElementById(id)
let generation=0,bootstrap,page,busy=false,principal,ready=false,initializing=false
const session=createSessionClient({onInvalid:message=>clear(message)})
function controls(){
 el('app-sign-in').disabled=busy||!ready||!bootstrap?.login_configured||Boolean(principal)
 el('app-sign-out').disabled=!principal
 el('app-recheck').disabled=busy||initializing
}
function clear(message='Session needs an explicit recheck. Any interrupted change may already have completed.'){
 generation++;principal=undefined;busy=false;initializing=false;session.clear();page?.clear(message)
 el('app-principal').textContent='Not verified';el('app-workspaces')?.replaceChildren();el('app-data')?.setAttribute('hidden','')
 el('app-status').textContent=message;controls()
}
async function inspect(){
 if(!ready||initializing)return
 clear('Checking the current server session…');const turn=generation;busy=true;controls()
 try{
  const identity=await session.inspect();if(turn!==generation)return
  principal=identity.principal_id;el('app-principal').textContent=principal;controls()
  if(page)await page.activate()
  else{
   const spaces=validateWorkspaces(await session.request('/api/workspaces'));if(turn!==generation)return
   for(const space of spaces){const item=document.createElement('li');item.textContent=`${space.name} · ${space.kind} · ${space.state} · ${space.id}`;el('app-workspaces').append(item)}
   if(!spaces.length){const item=document.createElement('li');item.textContent='No accessible workspaces. Shared spaces still require explicit membership.';el('app-workspaces').append(item)}
   el('app-data').hidden=false
  }
  if(turn!==generation)return
  el('app-status').textContent='Current session verified. Every module request rechecks current identity and workspace permissions.'
 }catch(error){if(turn===generation)clear(error.message||'Session could not be verified.')}
 finally{if(turn===generation){busy=false;controls()}}
}
async function signOut(){
 if(!principal)return
 clear('Signing out. Private page state has been cleared.');const turn=generation;busy=true;controls()
 try{const result=await session.logout();if(turn!==generation)return;if(!result||Object.keys(result).join()!=='logged_out'||result.logged_out!==true)throw new Error('Logout could not be verified.');clear('Signed out. The server session is revoked.')}
 catch(error){if(turn===generation)clear(error.status===401?'Session is no longer valid. Recheck or sign in explicitly.':'Logout is not confirmed. Private page state remains cleared. Explicitly recheck before continuing.')}
}
async function signIn(){
 if(busy||principal||!bootstrap?.login_configured)return
 clear('Starting the configured identity-provider flow…');const turn=generation;busy=true;controls()
 try{const result=await session.start();if(turn!==generation)return;const target=authorizationTarget(result,bootstrap,location.origin);location.assign(target)}
 catch(error){if(turn===generation)clear(error.message||'Sign-in could not be started. Nothing was retried.')}
}
el('app-sign-in').addEventListener('click',signIn);el('app-sign-out').addEventListener('click',signOut);el('app-recheck').addEventListener('click',()=>{if(busy||initializing)return;return ready?inspect():start()})
window.addEventListener('pagehide',()=>clear())
window.addEventListener('pageshow',event=>{if(event.persisted)clear('Page restored without private state. Explicitly recheck the session to continue.')})
async function start(){
 if(initializing)return
 initializing=true;busy=true;controls();const turn=generation
 try{
  if(location.protocol!=='https:')throw new Error('This application requires HTTPS.')
  const configured=validateBootstrap(await session.bootstrap(),location.origin);if(turn!==generation)return;bootstrap=configured
  el('app-login-note').textContent=bootstrap.login_configured?'Sign-in uses only the explicitly configured identity provider. No provider protocol or production readiness is implied.':'Sign-in is unavailable: no reviewed bound-code identity provider is configured. Existing valid sessions may be rechecked; no default identity is supplied.'
  el('app-community-link').hidden=!bootstrap.ui_modules.includes('community')
  const responsibility=el('app-responsibility-link');responsibility.hidden=!bootstrap.ui_modules.includes('responsibility')
  if(document.body.dataset.page==='manage')page=mountWorkspacePage({client:session,sessionMode:true,onSignOut:signOut})
  else if(document.body.dataset.page==='responsibility'){
   if(!bootstrap.ui_modules.includes('responsibility'))throw new Error('Responsibility is not configured on this service.')
   page=mountResponsibilityPage({client:createResponsibilitySessionClient(session),sessionMode:true,onSignOut:signOut})
  }
  else if(document.body.dataset.page==='community'){
   if(!bootstrap.ui_modules.includes('community'))throw new Error('Community is not configured on this service.')
   page=mountCommunityPage({client:createCommunitySessionClient(session),sessionMode:true,onSignOut:signOut})
  }
  ready=true;initializing=false;busy=false;el('app-recheck').textContent='Recheck session';controls();await inspect()
 }catch(error){if(turn===generation){clear(error.message||'Application configuration unavailable.');if(!ready)el('app-recheck').textContent='Retry setup'}}
}
start()
