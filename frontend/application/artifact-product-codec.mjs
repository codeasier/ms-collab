// Metadata from one approved publication. Neither the relative key nor its
// observation is an authorization, a provider URL, or a download capability.
const scopeFields = ['workspace_id', 'connection_id', 'bucket_id']
const baseFields = ['format', 'quarter', 'path', 'mode', 'page', 'page_size', 'if_observation']
const fileFields = ['recursive', 'keyword', 'start_time', 'end_time']
const resultFields = ['format', 'kind', 'match', 'scope', 'view_id', 'quarter', 'path', 'mode',
  'observed_at', 'quarter_available', 'catalog_revision', 'observation', 'page', 'page_size',
  'total', 'items', 'source']
const rowFields = ['key', 'name', 'parent', 'modified_at', 'size', 'is_dir', 'virtual']
const sourceFields = ['kind', 'full_history_complete', 'live_provider_verified', 'download_available']
const id = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/
const quarter = /^[1-9][0-9]{3}Q[1-4]$/
const observation = /^[0-9a-f]{64}$/
const time = /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?(Z|([+-])([0-9]{2}):([0-9]{2}))$/
const full = (re, value) => typeof value === 'string' && re.exec(value)?.[0] === value
const bounded = (n, min, max) => Number.isSafeInteger(n) && n >= min && n <= max
const scalarText = value => typeof value === 'string' && !/[\u0000-\u001f\u007f\ud800-\udfff]/u.test(value)
const encoder = new TextEncoder()
const invalidRequest = () => new Error('Invalid product view request. Check the scope and filters.')
const invalidResult = () => new Error('The product view response could not be verified.')

// Reject missing, inherited, extra, non-enumerable, symbol and accessor fields.
// Never compare comma-joined field names (one literal field can contain commas).
function fields(value, required, optional = []) {
  if (value === null || typeof value !== 'object' ||
      ![Object.prototype, null].includes(Object.getPrototypeOf(value))) return false
  const keys = Reflect.ownKeys(value)
  return keys.length >= required.length && keys.length <= required.length + optional.length &&
    required.every(k => Object.hasOwn(value, k)) && keys.every(k => {
      if (!required.includes(k) && !optional.includes(k)) return false
      const property = Object.getOwnPropertyDescriptor(value, k)
      return property.enumerable && Object.hasOwn(property, 'value')
    })
}

function relativeKey(value, directory, root = false) {
  if (typeof value !== 'string' || value.length > 1024 || !scalarText(value)) return false
  if (value === '') return root
  const parts = value.split('/')
  return !value.startsWith('/') && !value.includes('\\') && !value.includes('://') &&
    value.endsWith('/') === directory && encoder.encode(value).length <= 1024 &&
    parts.length <= 32 && !parts.some(p => p === '.' || p === '..')
}

export function productInstantMicros(value) {
  const fail = () => { throw new Error('Use a valid ISO time with an explicit UTC offset and at most six fractional digits.') }
  if (typeof value !== 'string' || value.length > 32) fail()
  const m = time.exec(value)
  if (!m || m[0] !== value) fail()
  const [year, month, day, h, minute, second] = m.slice(1, 7).map(Number)
  const offsetHour = Number(m[10] || 0), offsetMinute = Number(m[11] || 0)
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > 31 || h > 23 ||
      minute > 59 || second > 59 || offsetHour > 23 || offsetMinute > 59) fail()
  // Date only calculates integral calendar seconds. setUTCFullYear, unlike
  // Date.UTC(year,...), handles years 0001..0099 without mapping them to 19xx.
  const date = new Date(0)
  date.setUTCFullYear(year, month - 1, day)
  if (date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) fail()
  date.setUTCHours(h, minute, second, 0)
  const offset = (m[9] === '-' ? -1 : 1) * (offsetHour * 60 + offsetMinute) * 60
  const seconds = date.getTime() / 1000 - offset
  // Python datetime's bounded UTC domain: 0001-01-01 through 9999-12-31.
  if (seconds < -62135596800 || seconds > 253402300799) fail()
  return BigInt(seconds) * 1000000n + BigInt((m[7] || '').padEnd(6, '0'))
}

export function lastSevenUtcDates(date) {
  if (!(date instanceof Date) || !Number.isFinite(date.getTime())) throw invalidRequest()
  const first = new Date(date.getTime())
  first.setUTCDate(first.getUTCDate() - 6)
  if (date.getUTCFullYear() < 1 || date.getUTCFullYear() > 9999 || first.getUTCFullYear() < 1) throw invalidRequest()
  return {
    start_time: first.toISOString().slice(0, 10) + 'T00:00:00Z',
    end_time: date.toISOString().slice(0, 10) + 'T23:59:59.999999Z',
  }
}

export function productChoicesPath(scope) {
  if (!fields(scope, scopeFields) || !scopeFields.every(k => full(id, scope[k]))) throw invalidRequest()
  const {workspace_id: w, connection_id: c, bucket_id: b} = scope
  return `/api/workspaces/${w}/artifacts/${c}/${b}/product-views`
}

export function validateProductChoices(data, scope) {
  if (!fields(data, ['format', 'kind', 'scope', 'catalog_revision', 'views', 'quarters']) ||
      data.format !== 1 || data.kind !== 'product-view-choices' ||
      !fields(data.scope, scopeFields) || !scopeFields.every(k => data.scope[k] === scope[k]) ||
      !bounded(data.catalog_revision, 1, Number.MAX_SAFE_INTEGER) ||
      !Array.isArray(data.views) || data.views.length > 128 ||
      !Array.isArray(data.quarters) || data.quarters.length > 500 ||
      (!data.views.length && data.quarters.length)) throw invalidResult()
  let previous = ''
  for (const item of data.views) {
    if (!fields(item, ['view_id', 'label']) || !full(id, item.view_id) ||
        item.view_id <= previous || typeof item.label !== 'string' ||
        item.label !== item.label.trim() || [...item.label].length < 1 || [...item.label].length > 80 ||
        /[^ \p{L}\p{M}\p{N}\p{P}\p{S}]/u.test(item.label)) throw invalidResult()
    previous = item.view_id
  }
  if (data.quarters.some(q => !full(quarter, q)) ||
      new Set(data.quarters).size !== data.quarters.length) throw invalidResult()
  return data
}

export function makeProductRequest(scope, viewId, body) {
  if (!fields(scope, scopeFields) || !scopeFields.every(k => full(id, scope[k])) ||
      !full(id, viewId) || !fields(body, ['format', 'quarter'], [...baseFields.slice(2), ...fileFields]) ||
      body.format !== 1 || !full(quarter, body.quarter)) throw invalidRequest()
  const def = (k, fallback) => Object.hasOwn(body, k) ? body[k] : fallback
  const mode = def('mode', 'tree'), page = def('page', 1)
  if (!['tree', 'files'].includes(mode) || !relativeKey(def('path', ''), true, true) ||
      !bounded(page, 1, 1000000) || ![10, 20, 50].includes(def('page_size', 50)) ||
      ((page > 1 || Object.hasOwn(body, 'if_observation')) && !full(observation, body.if_observation)) ||
      (mode === 'tree' && fileFields.some(k => Object.hasOwn(body, k))) ||
      typeof def('recursive', false) !== 'boolean') throw invalidRequest()
  if (Object.hasOwn(body, 'keyword')) {
    const s = body.keyword
    // Python text(...,256) + strip(): preserve literal whitespace when nonempty.
    // U+0085 is whitespace; FEFF is not. C0/DEL and lone surrogates are invalid.
    if (typeof s !== 'string' || s.length > 512 || !scalarText(s) || [...s].length > 256 || !/[^\p{White_Space}]/u.test(s)) throw invalidRequest()
  }
  const start = Object.hasOwn(body, 'start_time') ? productInstantMicros(body.start_time) : undefined
  const end = Object.hasOwn(body, 'end_time') ? productInstantMicros(body.end_time) : undefined
  if (start !== undefined && end !== undefined && start > end) throw new Error('The start time must not be after the end time.')
  const {workspace_id: w, connection_id: c, bucket_id: b} = scope
  // Validated fields are all primitives. Preserve optional-field presence and
  // exact values, including literal slashes, Unicode and fractional precision.
  return Object.freeze({
    scope: Object.freeze({...scope}), viewId,
    path: `/api/workspaces/${w}/artifacts/${c}/${b}/product-views/${viewId}`,
    body: Object.freeze({...body}),
  })
}

function codePointOrder(a, b) {
  let i = 0, j = 0
  while (i < a.length && j < b.length) {
    const x = a.codePointAt(i), y = b.codePointAt(j)
    if (x !== y) return x < y ? -1 : 1
    i += x > 0xffff ? 2 : 1
    j += y > 0xffff ? 2 : 1
  }
  return (a.length - i) - (b.length - j)
}
function rowOrder(a, b) {
  return Number(b.is_dir) - Number(a.is_dir) || codePointOrder(a.name, b.name) || codePointOrder(a.key, b.key)
}

export function validateProductResult(data, request) {
  if (!fields(request, ['scope','viewId','path','body'])) throw invalidResult()
  const expected = makeProductRequest(request.scope, request.viewId, request.body)
  if (request.path !== expected.path) throw invalidResult()
  const {quarter, mode = 'tree', path = '', page = 1, page_size = 50, if_observation,
    recursive = false, keyword, start_time, end_time} = expected.body
  if (!fields(data, resultFields) || data.format !== 1 || data.kind !== 'product-view' ||
      data.match !== 'relative-key-ascii-substring-v1' || !fields(data.scope, scopeFields) ||
      !scopeFields.every(k => expected.scope[k] === data.scope[k]) || data.view_id !== expected.viewId ||
      data.quarter !== quarter || data.mode !== mode || data.path !== path || data.page !== page ||
      data.page_size !== page_size || typeof data.quarter_available !== 'boolean' ||
      !bounded(data.catalog_revision, 1, Number.MAX_SAFE_INTEGER) || !full(observation, data.observation) ||
      (if_observation !== undefined && data.observation !== if_observation) ||
      !bounded(data.total, 0, 500) || (!data.quarter_available && data.total !== 0) ||
      !Array.isArray(data.items) || data.items.length !== Math.max(0, Math.min(page_size, data.total - (page - 1) * page_size)) ||
      !fields(data.source, sourceFields) || data.source.kind !== 'stored_index' ||
      data.source.full_history_complete !== false || data.source.live_provider_verified !== false ||
      data.source.download_available !== false) throw invalidResult()
  // No source offset or token is exposed: never invent the server's quarter or
  // matching rule from UTC, a view name, or a provider/bucket guess.
  try {
    const observed = productInstantMicros(data.observed_at)
    const start = start_time === undefined ? undefined : productInstantMicros(start_time)
    const end = end_time === undefined ? undefined : productInstantMicros(end_time)
    const seen = new Set()
    let previous
    for (const row of data.items) {
      if (!fields(row, rowFields) || typeof row.is_dir !== 'boolean' || row.virtual !== row.is_dir ||
          !relativeKey(row.key, row.is_dir) || !row.key.startsWith(path) || seen.has(row.key) ||
          !bounded(row.size, 0, Number.MAX_SAFE_INTEGER)) throw invalidResult()
      const stem = row.is_dir ? row.key.slice(0, -1) : row.key
      const slash = stem.lastIndexOf('/')
      if (row.parent !== stem.slice(0, slash + 1) || row.name !== stem.slice(slash + 1) ||
          ((mode === 'tree' || !recursive) && row.parent !== path)) throw invalidResult()
      if (row.is_dir) {
        if (mode !== 'tree' || row.size !== 0 || row.modified_at !== null) throw invalidResult()
      } else {
        const modified = productInstantMicros(row.modified_at)
        if (/^\.[sS][hH][aA]256$/.test(row.key.slice(-7)) || modified > observed ||
            (keyword !== undefined && !row.key.includes(keyword)) ||
            (start !== undefined && modified < start) || (end !== undefined && modified > end)) throw invalidResult()
      }
      if (previous && rowOrder(previous, row) >= 0) throw invalidResult()
      seen.add(row.key)
      previous = row
    }
  } catch { throw invalidResult() }
  return data
}

// A preset names an exact file-filter query, not a list, link or download grant.
export function validProductPresetId(value) { return full(id,value) }
export function productPresetPath(scope,viewId,selected,name) {
  if (!validProductPresetId(name)) throw invalidRequest()
  const r=makeProductRequest(scope,viewId,{format:1,quarter:selected})
  return r.path+'/quarters/'+selected+'/presets/'+name
}
const presetRequired=['format','quarter','mode']
const presetOptional=['path',...fileFields]
export function productPresetWrite(scope,viewId,body) {
  if (!fields(body,[...presetRequired,'if_observation'],presetOptional)||body.mode!=='files') throw invalidRequest()
  return makeProductRequest(scope,viewId,body).body
}
export function validateProductPreset(data,scope,viewId,selected,name,saved) {
  if (!fields(data,['format','kind','preset_id','query','result'])||data.format!==1||
      data.kind!=='product-preset'||data.preset_id!==name||
      !fields(data.query,presetRequired,presetOptional)||data.query.mode!=='files'||data.query.quarter!==selected) throw invalidResult()
  const query=data.query
  if(saved&&(Object.keys(saved).length!==Object.keys(query).length+1||
      Object.keys(query).some(k=>!Object.hasOwn(saved,k)||query[k]!==saved[k]))) throw invalidResult()
  const req=makeProductRequest(scope,viewId,{...query,...(saved?{if_observation:saved.if_observation}:{})})
  validateProductResult(data.result,req)
  if(!data.result.quarter_available) throw invalidResult()
  return data
}
