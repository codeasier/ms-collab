import assert from 'node:assert/strict'
import test from 'node:test'
import { boardURL, validateBoard } from '../board.mjs'

const filters = { kind: '', state: '', q: '', limit: 10, offset: 0 }
const fixture = () => ({ mode: 'synthetic-demo', schema: 'ms-collab/community-board/v1',
  coverage: 'observed_subset', workspace_id: 'product-alpha', total: 1, offset: 0, limit: 10,
  sources: [{ workspace_id: 'product-alpha', connection_id: 'demo', repository: 'example/compiler', observed_at: '2026-01-01T00:00:00Z' }],
  items: [{ workspace_id: 'product-alpha', connection_id: 'demo', repository: 'example/compiler',
    kind: 'issue', state: 'open', number: 1, title: '<img src=x>', updated_at: '2026-01-01T00:00:00Z', labels: ['demo'] }] })

test('request only includes bounded filters, never caller identity', () => {
  const url = new URL(boardURL('product-alpha', { ...filters, q: 'a&b', actor: 'admin' }), 'http://example.invalid')
  assert.equal(url.searchParams.get('q'), 'a&b')
  assert.equal(url.searchParams.has('actor'), false)
  for (const changed of [{ limit: 0 }, { limit: 101 }, { offset: -1 }, { kind: 'bad' }, { q: 'a'.repeat(201) }]) {
    assert.throws(() => boardURL('product-alpha', { ...filters, ...changed }))
  }
  assert.throws(() => boardURL('../commons', filters))
})
test('scoped response keeps untrusted text as text and validates pagination', () => {
  const data = fixture()
  assert.equal(validateBoard(data, 'product-alpha', filters), data)
  assert.equal(data.items[0].title, '<img src=x>')
  for (const key of ['offset', 'limit']) {
    assert.throws(() => validateBoard({ ...data, [key]: 99 }, 'product-alpha', filters))
  }
})
test('rejects cross-scope rows, sources, response and invalid envelopes', () => {
  for (const section of ['items', 'sources']) {
    const data = fixture(); data[section][0].workspace_id = 'product-beta'
    assert.throws(() => validateBoard(data, 'product-alpha', filters))
  }
  assert.throws(() => validateBoard(fixture(), 'commons', filters))
  for (const changed of [{ mode: 'production' }, { total: -1 }, { items: null }, { sources: null }, { coverage: 'complete' }]) {
    assert.throws(() => validateBoard({ ...fixture(), ...changed }, 'product-alpha', filters))
  }
})
