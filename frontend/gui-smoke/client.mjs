// Copyright (c) 2026 codeasier; SPDX-License-Identifier: MIT
// Same-origin cookie transport and a strict saved-observation contract. No tokens.
export const STATES = ['pending','running','success','failed','cancelled','unknown'];
export const SOURCES = ['web','scheduled','api','retry','unknown'];
const ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const encoder = new TextEncoder();
export class ViewError extends Error { constructor(status = 0) { super('safe_view_error'); this.status = status; } }
const fail = () => { throw new ViewError(); };
const object = value => value && typeof value === 'object' && !Array.isArray(value);
function exact(value, keys) { if (!object(value) || Object.keys(value).length !== keys.length || keys.some(k => !Object.hasOwn(value,k))) fail(); return value; }
function number(value, max = Number.MAX_SAFE_INTEGER) { if (!Number.isSafeInteger(value) || value < 0 || value > max) fail(); return value; }
function text(value, limit) { if (typeof value !== 'string' || !value.trim() || encoder.encode(value).length > limit || /[\x00-\x1f\x7f]/.test(value)) fail(); return value; }
function id(value) { if (typeof value !== 'string' || !ID.test(value)) fail(); return value; }
function date(value) { if (typeof value !== 'string' || value.length > 64 || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value))) fail(); return value; }
function choice(value, options) { if (!options.includes(value)) fail(); return value; }
function boolean(value) { if (typeof value !== 'boolean') fail(); return value; }
export function scopeOf(value) { exact(value,['workspace_id','connection_id','repository_id']); for (const v of Object.values(value)) id(v); return {...value}; }
const sameScope = (a,b) => Object.keys(a).every(k => a[k] === b[k]);
function envelope(value, scope, extra, revision) {
  exact(value,['scope','available','source','capabilities',...extra]);
  if (!sameScope(scopeOf(value.scope),scope)) fail();
  boolean(value.available);
  const source = exact(value.source,['kind','revision','observed_at','live_provider_verified','full_history_complete','execution_enabled']);
  if (source.kind !== 'stored_observation' || source.live_provider_verified !== false || source.full_history_complete !== false || source.execution_enabled !== false) fail();
  number(source.revision);
  if (value.available) { if (!source.revision) fail(); date(source.observed_at); }
  else if (source.revision !== 0 || source.observed_at !== null) fail();
  if (revision != null && source.revision !== revision) fail();
  const caps = exact(value.capabilities,['read','submit','cancel','retry','reproduce','refresh','download']);
  if (caps.read !== true || Object.entries(caps).some(([k,v]) => k !== 'read' && v !== false)) fail();
  return value;
}
export function validateList(value, scope, options) {
  envelope(value,scope,['total','offset','limit','items','next_offset'],options.revision);
  number(value.total,500); number(value.offset,500); number(value.limit,100);
  if (!value.limit || value.offset !== options.offset || value.limit !== options.limit || !Array.isArray(value.items)) fail();
  if (value.items.length !== Math.min(value.limit,Math.max(value.total-value.offset,0))) fail();
  if (!value.available && value.total !== 0) fail();
  const next = value.offset+value.items.length < value.total ? value.offset+value.items.length : null;
  if (value.next_offset !== next) fail();
  const ids = new Set();
  for (const row of value.items) {
    exact(row,['id','status','source','branch','created_at','details_available','stages_available','summary_available']);
    id(row.id); choice(row.status,STATES); choice(row.source,SOURCES); text(row.branch,400); date(row.created_at);
    if (ids.has(row.id) || row.details_available !== true) fail();
    ids.add(row.id); boolean(row.stages_available); boolean(row.summary_available);
    for (const key of ['status','source','branch']) if (options[key] && row[key] !== options[key]) fail();
  }
  return value;
}
export function validateSummary(value, scope, revision) {
  envelope(value,scope,['total','states','interpretation'],revision);
  number(value.total,500); exact(value.states,STATES);
  if (Object.values(value.states).reduce((n,v) => n+number(v,500),0) !== value.total || (!value.available && value.total !== 0)) fail();
  if (value.interpretation !== 'published_snapshot_only;not_live_queue_or_execution_proof') fail();
  return value;
}
function timing(row, observed, state) {
  for (const k of ['started_at','finished_at']) if (row[k] !== null) { date(row[k]); if (Date.parse(row[k]) > observed) fail(); }
  const start = row.started_at === null ? null : Date.parse(row.started_at);
  const end = row.finished_at === null ? null : Date.parse(row.finished_at);
  if (start !== null && end !== null && end < start) fail();
  if ((state === 'pending' && (start !== null || end !== null)) || (state === 'running' && end !== null)) fail();
  if (start === null || end === null) { if (row.duration_seconds !== null) fail(); }
  else if (typeof row.duration_seconds !== 'number' || !Number.isFinite(row.duration_seconds) || row.duration_seconds < 0 || Math.abs(row.duration_seconds-(end-start)/1000) > .002) fail();
}
export function validateDetail(value, scope, identity, revision) {
  envelope(value,scope,['task'],revision);
  if (!value.available) fail();
  const row = exact(value.task,['id','status','source','branch','created_at','started_at','finished_at','commit','requested_commit','summary','stages','duration_seconds']);
  if (id(row.id) !== identity) fail(); choice(row.status,STATES); choice(row.source,SOURCES); text(row.branch,400); date(row.created_at);
  const observed = Date.parse(value.source.observed_at), created = Date.parse(row.created_at);
  if (created > observed) fail();
  timing(row,observed,row.status);
  for (const k of ['started_at','finished_at']) if (row[k] !== null && Date.parse(row[k]) < created) fail();
  for (const k of ['commit','requested_commit']) if (row[k] !== null && (typeof row[k] !== 'string' || !/^[a-f0-9]{40}$/.test(row[k]))) fail();
  if (row.status === 'pending' && row.commit !== null) fail();
  if (row.summary !== null) {
    exact(row.summary,['passed','failed','skipped','total']); for (const v of Object.values(row.summary)) number(v,1000000);
    if (row.summary.passed+row.summary.failed+row.summary.skipped !== row.summary.total || (row.status === 'success' && row.summary.failed)) fail();
  }
  if (row.stages !== null) {
    if (!Array.isArray(row.stages) || row.stages.length > 20) fail();
    const seen = new Set();
    for (const stage of row.stages) {
      exact(stage,['id','name','status','started_at','finished_at','duration_seconds']); id(stage.id); text(stage.name,400);
      choice(stage.status,[...STATES,'skipped']); if (seen.has(stage.id)) fail(); seen.add(stage.id);
      timing(stage,observed,stage.status);
    }
  }
  return value;
}
export function queryOptions(filters = {}, offset = 0, revision = undefined) {
  const options = {offset:number(offset,500),limit:number(Number(filters.limit ?? 20),100)};
  if (!options.limit) fail();
  for (const k of ['status','source','branch']) if (filters[k]) options[k] = k === 'branch' ? text(filters[k],400) : choice(filters[k],k === 'status' ? STATES : SOURCES);
  if (revision != null) options.revision = number(revision);
  if (offset && revision == null) fail();
  return options;
}
export function messageFor(error) {
  return ({401:'Your session is not active. Sign in again.',403:'This scope is not available for your current access.',404:'This scope or task is not available for your current access.',409:'This workspace or module is not active.',412:'The published snapshot changed. Load the first page again.',428:'Reload the first page before continuing.',503:'The service is unavailable. No task data is shown.'})[error?.status] ?? 'The request or scoped response could not be verified. No task data is shown.';
}
async function jsonResponse(response) {
  if (!response.ok) throw new ViewError(response.status);
  if (response.redirected || !/^application\/json(?:;|$)/i.test(response.headers.get('content-type') ?? '')) fail();
  const reader = response.body?.getReader();
  if (!reader) fail();
  const chunks = []; let size = 0;
  try {
    for (;;) { const {done,value} = await reader.read(); if (done) break; size += value.byteLength; if (size > 1024*1024) { await reader.cancel(); fail(); } chunks.push(value); }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk,offset); offset += chunk.length; }
  let value;
  try { value = JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes)); } catch { fail(); }
  exact(value,['data']); return value.data;
}
function csrfHeaders(csrf) {
  if (csrf == null) return {};
  if (typeof csrf !== 'string' || !/^[A-Za-z0-9_-]{20,512}$/.test(csrf)) fail();
  return {'X-CSRF-Token':csrf};
}
export class Client {
  constructor(fetcher = globalThis.fetch) { this.fetcher = fetcher; }
  async request(path, signal, options = {}) {
    return jsonResponse(await this.fetcher(path,{method:'GET',credentials:'same-origin',mode:'same-origin',redirect:'error',cache:'no-store',signal,...options}));
  }
  base(scope) { const s=scopeOf(scope); return `/api/workspaces/${s.workspace_id}/gui-smoke/${s.connection_id}/${s.repository_id}`; }
  async session(signal) { const value=await this.request('/auth/session',signal); exact(value,['principal_id']); if (typeof value.principal_id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(value.principal_id)) fail(); return value.principal_id; }
  async signIn(csrf) {
    const value=await this.request('/auth/start',undefined,{method:'POST',headers:{'Content-Type':'application/json','X-MS-Collab-Login':'1',...csrfHeaders(csrf)},body:'{}'});
    exact(value,['authorization_url']); let target; try { target=new URL(value.authorization_url); } catch { fail(); }
    if (target.protocol !== 'https:' || target.username || target.password || target.hash || target.href.length > 16384 || target.searchParams.get('response_type') !== 'code' || target.searchParams.get('code_challenge_method') !== 'S256') fail();
    return target.href;
  }
  async signOut(csrf) {
    if (csrf == null) throw new ViewError(401);
    const value=await this.request('/auth/logout',undefined,{method:'POST',headers:{'Content-Type':'application/json',...csrfHeaders(csrf)},body:'{}'});
    exact(value,['logged_out']); if (value.logged_out !== true) fail();
  }
  async list(scope,options,signal) { return validateList(await this.request(this.base(scope)+'/tasks?'+new URLSearchParams(options),signal),scope,options); }
  async summary(scope,revision,signal) { return validateSummary(await this.request(this.base(scope)+'/summary?'+new URLSearchParams({revision}),signal),scope,revision); }
  async detail(scope,identity,revision,signal) { return validateDetail(await this.request(this.base(scope)+'/tasks/'+id(identity)+'?'+new URLSearchParams({revision}),signal),scope,identity,revision); }
}
const sameSnapshot = (a,b) => a.available === b.available && a.source.revision === b.source.revision && a.source.observed_at === b.source.observed_at;
export class ViewController {
  constructor(client, changed) { this.client=client; this.changed=changed; this.epoch=0; this.abort=null; this.view=null; this.state={phase:'idle'}; }
  emit(state) { this.state=state; this.changed(state); }
  begin() { this.abort?.abort(); this.abort=new AbortController(); return {epoch:++this.epoch,signal:this.abort.signal}; }
  clear() { this.begin(); this.view=null; this.emit({phase:'idle'}); }
  async load(rawScope, filters = {}, offset = 0) {
    const old=this.view, lane=this.begin(); this.view=null; this.emit({phase:'loading'});
    try {
      const scope=scopeOf(rawScope), revision=offset ? old?.list.source.revision : undefined;
      const options=queryOptions(filters,offset,revision);
      if (offset && (!old || !sameScope(old.scope,scope) || ['status','source','branch','limit'].some(k => (old.options[k] ?? '') !== (options[k] ?? '')))) fail();
      const list=await this.client.list(scope,options,lane.signal); if (lane.epoch !== this.epoch) return false;
      if (offset && (!sameSnapshot(old.list,list) || old.list.total !== list.total)) fail();
      const summary=await this.client.summary(scope,list.source.revision,lane.signal); if (lane.epoch !== this.epoch) return false;
      if (!sameSnapshot(list,summary) || list.total > summary.total || (!options.status && !options.source && !options.branch && list.total !== summary.total) || (options.status && !options.source && !options.branch && list.total !== summary.states[options.status])) fail();
      this.view={scope,options,list,summary}; this.emit({phase:'list',view:this.view}); return true;
    } catch (error) { if (lane.epoch === this.epoch) { this.view=null; this.emit({phase:'error',error}); } return false; }
  }
  async detail(identity) {
    const view=this.view; if (!view || !view.list.items.some(row => row.id === identity)) return false;
    const lane=this.begin(); this.emit({phase:'loading-detail',view});
    try {
      const detail=await this.client.detail(view.scope,identity,view.list.source.revision,lane.signal);
      if (lane.epoch !== this.epoch) return false;
      if (!sameSnapshot(view.list,detail)) fail();
      this.emit({phase:'detail',view,detail}); return true;
    } catch (error) { if (lane.epoch === this.epoch) { this.view=null; this.emit({phase:'error',error}); } return false; }
  }
  back() { this.begin(); if (this.view) this.emit({phase:'list',view:this.view}); else this.emit({phase:'idle'}); }
  page(offset) { if (!this.view) return false; return this.load(this.view.scope,this.view.options,offset); }
}
