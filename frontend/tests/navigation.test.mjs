import test from 'node:test'
import assert from 'node:assert/strict'
import { visibleNavigation } from '../navigation.mjs'

const a = { id: 'a', capability: 'a.read' }
const b = { id: 'b', capability: 'b.read' }
test('only an explicitly granted capability is visible', () => {
  assert.deepEqual(visibleNavigation([a, b], { 'a.read': true }), [a])
  for (const value of [false, 1, 'true', null, undefined]) {
    assert.deepEqual(visibleNavigation([a], { 'a.read': value }), [])
  }
})
test('empty, unknown and malformed capabilities fail closed', () => {
  for (const value of [null, undefined, {}, false]) assert.deepEqual(visibleNavigation([a], value), [])
  assert.deepEqual(visibleNavigation([null, {}, { id: 'x' }, { id: 'x', capability: '' }], {}), [])
  assert.deepEqual(visibleNavigation(null, {}), [])
  assert.deepEqual(visibleNavigation([a], Object.create({ 'a.read': true })), [])
})
test('module order and input objects are preserved', () => {
  const items = Object.freeze([a, b])
  assert.deepEqual(visibleNavigation(items, { 'a.read': true, 'b.read': true }), [a, b])
  assert.equal(items[0], a)
})
