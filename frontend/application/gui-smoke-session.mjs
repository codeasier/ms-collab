// Copyright (c) 2026 codeasier; SPDX-License-Identifier: MIT
import {scopeOf,queryOptions,validateList,validateSummary,validateDetail,ViewError} from '../gui-smoke/client.mjs'
const identity=value=>{if(typeof value!=='string'||! /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value)||/[\x00-\x1f\x7f]/.test(value))throw new ViewError();return value}
const version=value=>{if(!Number.isSafeInteger(value)||value<0)throw new ViewError();return value}
function base(raw){const scope=scopeOf(raw);for(const value of Object.values(scope))identity(value);return {scope,path:`/api/workspaces/${scope.workspace_id}/gui-smoke/${scope.connection_id}/${scope.repository_id}`}}
export function createGuiSmokeSessionClient(session){
 if(!session||typeof session.request!=='function')throw new ViewError()
 return Object.freeze({
  async list(raw,options,signal){const {scope,path}=base(raw),query=queryOptions(options,options.offset,options.revision);return validateList(await session.request(path+'/tasks?'+new URLSearchParams(query),{signal}),scope,query)},
  async summary(raw,revision,signal){const {scope,path}=base(raw),query=queryOptions({},0,version(revision));return validateSummary(await session.request(path+'/summary?'+new URLSearchParams({revision:query.revision}),{signal}),scope,query.revision)},
  async detail(raw,id,revision,signal){const {scope,path}=base(raw),query=queryOptions({},0,version(revision));return validateDetail(await session.request(path+'/tasks/'+identity(id)+'?'+new URLSearchParams({revision:query.revision}),{signal}),scope,id,query.revision)}
 })
}
