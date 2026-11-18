import {mountModuleStatus} from './module-status.mjs'
import {mountPipelineScopePicker} from './pipeline-scopes.mjs'
import {mountArtifactScopePicker} from './artifact-scopes.mjs'
import {mountConnectionsPage,mountConnectionPicker} from './connections.mjs'
import {mountGuiSmokePage} from '../gui-smoke/view.mjs'
import {createGuiSmokeSessionClient} from './gui-smoke-session.mjs'
import {mountAssetPage} from '../research-assets/browser.mjs'
import {createAssetSessionClient} from '../research-assets/client.mjs'
import {mountAssistantPage} from '/assistant/history.mjs'
import {createAssistantSessionClient} from '/assistant/client.mjs'
import {mountCommunityPage} from '../community/board.mjs'
import {createCommunitySessionClient} from './community-client.mjs'
import {createSessionClient,createResponsibilitySessionClient,createPipelineSessionClient,validateBootstrap,authorizationTarget} from './client.mjs'
import {validateWorkspaces,displayPolicyId} from '/manage/client.mjs'
import {mountWorkspacePage} from '/manage/admin.mjs'
import {mountPipelinePage} from '../pipelines/runs.mjs'
import {mountResponsibilityPage} from '../responsibility/browser.mjs'
import {mountArtifactDownload} from './artifact-download-ui.mjs'
import {createArtifactSessionClients} from './artifacts-session.mjs'
import {mountArtifactCatalogPage} from '/artifacts/browser.mjs'
import {mountArtifactAdministrationPage} from '/artifact-admin/browser.mjs'
const el=id=>document.getElementById(id)
let generation=0,bootstrap,page,overviewStatus,busy=false,principal,ready=false,initializing=false
const session=createSessionClient({onInvalid:clear})
const productEntry=el('app-product-view-entry')
const onProductPage=document.body.dataset.page==='artifact-product-views'
// Retire this document's authority at same-tab navigation intent, before a late
// response can invalidate a newer session. Native navigation still owns the link.
function onDeparture(event){
 if(event.defaultPrevented||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return
 clear('Leaving this page. Private page state has been cleared.')
}
productEntry?.addEventListener('click',onDeparture)
if(onProductPage){
 el('pv-return').addEventListener('click',onDeparture)
 el('app-artifacts-link').addEventListener('click',onDeparture)
}
// Download handoffs must also retire at navigation intent, before pagehide.
// Snapshot only this page's fixed navigation elements. A temporary Blob save
// anchor, a link in results, or a changed destination cannot trigger this path.
function installProductFileDeparture(){
 const paths=new Set(['/','/manage/','/connections/','/responsibility/','/community/','/pipelines/','/artifacts/','/artifact-admin/','/assistant/','/research-assets/','/gui-smoke/'])
 const links=new Map()
 for(const link of document.querySelectorAll('header.app-shell > a.app-brand[href], header.app-shell > nav > a[href], #pv-return')){
  const path=link.getAttribute('href')
  if(paths.has(path))links.set(link,path)
 }
 // Replace, not supplement, the two legacy direct handlers on this opt-in
 // document. By window bubbling, earlier canceled clicks are distinguishable.
 el('pv-return').removeEventListener('click',onDeparture)
 el('app-artifacts-link').removeEventListener('click',onDeparture)
 window.addEventListener('click',event=>{
  const link=event.target?.closest?.('a[href]'),path=links.get(link)
  if(!path||link.hasAttribute('target')||link.hasAttribute('download')||link.closest('[hidden], [inert]')||
     link.getAttribute('href')!==path||link.href!==location.origin+path)return
  onDeparture(event)
 })
}
function controls(){
 el('app-sign-in').disabled=busy||!ready||!bootstrap?.login_configured||Boolean(principal)
 el('app-sign-out').disabled=!principal
 el('app-recheck').disabled=busy||initializing
}
function clear(message='Session needs an explicit recheck. Any interrupted change may already have completed.',workspaceListSignal){
 if(productEntry)productEntry.hidden=true
 generation++;principal=undefined;busy=false;initializing=false;session.clear();page?.clear(message,workspaceListSignal);overviewStatus?.clear()
 el('app-principal').textContent='Not verified';el('app-workspaces')?.replaceChildren();el('app-data')?.setAttribute('hidden','')
 el('app-status').textContent=message;controls()
}
async function inspect(){
 if(!ready||initializing)return
 clear('Checking the current server session…');const turn=generation;busy=true;controls()
 try{
  const identity=await session.inspect();if(turn!==generation)return
  principal=identity.principal_id;el('app-principal').textContent=displayPolicyId(principal);controls()
  if(productEntry)productEntry.hidden=!bootstrap.ui_modules.includes('artifact-product-views')
  if(page)await page.activate()
  else{
   const spaces=validateWorkspaces(await session.request('/api/workspaces'));if(turn!==generation)return
   for(const space of spaces){const item=document.createElement('li');item.textContent=`${space.name} · ${space.kind} · ${space.state} · ${space.id}`;el('app-workspaces').append(item)}
   if(!spaces.length){const item=document.createElement('li');item.textContent='No accessible workspaces. Shared spaces still require explicit membership.';el('app-workspaces').append(item)}
   overviewStatus?.activate(spaces)
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
  const pipelines=el('app-pipelines-link');if(pipelines)pipelines.hidden=!bootstrap.ui_modules.includes('pipelines')
  const connections=el('app-connections-link');if(connections)connections.hidden=!bootstrap.ui_modules.includes('connections')
  const gui=el('app-gui-smoke-link');if(gui)gui.hidden=!bootstrap.ui_modules.includes('gui-smoke')
  const assets=el('app-research-assets-link');if(assets)assets.hidden=!bootstrap.ui_modules.includes('research-assets')
  const assistant=el('app-assistant-link');if(assistant)assistant.hidden=!bootstrap.ui_modules.includes('assistant')
  const responsibility=el('app-responsibility-link');responsibility.hidden=!bootstrap.ui_modules.includes('responsibility')
  for(const module of ['artifacts','artifact-admin']){const link=el('app-'+module+'-link');if(link)link.hidden=!bootstrap.ui_modules.includes(module)}
  if(document.body.dataset.page==='overview')overviewStatus=mountModuleStatus({session})
  else if(document.body.dataset.page==='manage')page=mountWorkspacePage({client:session,sessionMode:true,onSignOut:signOut})
  else if(document.body.dataset.page==='connections'){
   if(!bootstrap.ui_modules.includes('connections'))throw new Error('Connection management is not configured on this service.')
   page=mountConnectionsPage({session})
  }
  else if(document.body.dataset.page==='responsibility'){
   if(!bootstrap.ui_modules.includes('responsibility'))throw new Error('Responsibility is not configured on this service.')
   page=mountResponsibilityPage({client:createResponsibilitySessionClient(session),sessionMode:true,onSignOut:signOut})
  }
  else if(document.body.dataset.page==='gui-smoke'){
   if(!bootstrap.ui_modules.includes('gui-smoke'))throw new Error('GUI observations are not configured on this service.')
   page=mountGuiSmokePage({client:createGuiSmokeSessionClient(session)})
  }
  else if(document.body.dataset.page==='assistant'){
   if(!bootstrap.ui_modules.includes('assistant'))throw new Error('Saved history is not configured on this service.')
   page=mountAssistantPage({client:createAssistantSessionClient(session)})
  }
  else if(document.body.dataset.page==='community'){
   if(!bootstrap.ui_modules.includes('community'))throw new Error('Community is not configured on this service.')
   const board=mountCommunityPage({client:createCommunitySessionClient(session),sessionMode:true,onSignOut:signOut})
   if(bootstrap.ui_modules.includes('connections')){const picker=mountConnectionPicker({session});let activation=0;page={clear:message=>{activation++;picker.clear();board.clear(message)},activate:async()=>{const turn=activation;await picker.activate();if(turn===activation)await board.activate()}}}else page=board
  }
  else if(document.body.dataset.page==='pipelines'){
   if(!bootstrap.ui_modules.includes('pipelines'))throw new Error('Pipelines are not configured on this service.')
   let picker
   const board=mountPipelinePage({client:createPipelineSessionClient(session),sessionMode:true,onSignOut:signOut,onBusyChange:()=>picker?.sync()})
   picker=mountPipelineScopePicker({session,isOpening:()=>board.loading});let activation=0
   page={clear:message=>{activation++;picker.clear();board.clear(message)},activate:async()=>{const turn=activation;await picker.activate();if(turn===activation)await board.activate()}}
  }
  else if(onProductPage){
   if(!bootstrap.ui_modules.includes('artifact-product-views'))throw new Error('Product views are not configured on this service.')
   const {mountArtifactProductView}=await import('./artifact-product-view.mjs')
   if(turn!==generation)return
   let download=null
   if(bootstrap.ui_modules.includes('artifact-product-file-downloads')){
    const template=el('pv-download-template')
    template.replaceWith(template.content.cloneNode(true))
    download=mountArtifactDownload({session,productFile:true})
   }
   page=mountArtifactProductView({session,download})
   if(download)installProductFileDeparture()
  }
  else if(['artifacts','artifact-admin'].includes(document.body.dataset.page)){
   const module=document.body.dataset.page;if(!bootstrap.ui_modules.includes(module))throw new Error('This artifact page is not configured on this service.')
   const clients=createArtifactSessionClients(session)
   const download=module==='artifacts'&&bootstrap.ui_modules.includes('artifact-downloads')?mountArtifactDownload({session}):null
   if(download)el('download-notice').hidden=false
   const board=module==='artifacts'?mountArtifactCatalogPage({client:clients.catalog,sessionMode:true,onSignOut:signOut,onDownload:download?.select,clearDownload:()=>download?.clear()}):mountArtifactAdministrationPage({client:clients.administration,sessionMode:true,onSignOut:signOut})
   const picker=mountArtifactScopePicker({session,managed:module==='artifact-admin'});let activation=0
   page={clear:message=>{activation++;picker.clear();board.clear(message)},activate:async()=>{const turn=activation;await picker.activate();if(turn===activation)await board.activate()}}
  }
  else if(document.body.dataset.page==='research-assets'){
   if(!bootstrap.ui_modules.includes('research-assets'))throw new Error('Research assets are not configured on this service.')
   page=mountAssetPage({client:createAssetSessionClient(session),onSignOut:signOut})
  }
  ready=true;initializing=false;busy=false;el('app-recheck').textContent='Recheck session';controls();await inspect()
 }catch(error){if(turn===generation){clear(error.message||'Application configuration unavailable.');if(!ready)el('app-recheck').textContent='Retry setup'}}
}
start()
