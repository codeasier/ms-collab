import test from 'node:test'
import assert from 'node:assert/strict'
import {recordedTaskElapsed} from '../runs.mjs'

const elapsed = (start, finish, observed, status = 'failure') =>
  recordedTaskElapsed({status, started_at: start, finished_at: finish}, observed)

test('recorded task elapsed uses microseconds, offsets and the observation boundary for all terminal outcomes', () => {
  const start = '2000-02-29T23:59:59.999999Z'
  const finish = '2000-03-01T01:00:00.000001+01:00'
  const observed = '2000-02-29T19:00:00.000001-05:00'
  for (const status of ['success', 'failure', 'cancelled', 'timed_out', 'skipped']) {
    assert.equal(elapsed(start, finish, observed, status), '0.000002s', status)
  }
  assert.equal(elapsed(finish, observed, observed), '0s') // Same instant, different dates/zones.
  assert.equal(elapsed(start, finish, '2000-03-01T00:00:00.000000Z'), 'unavailable')
  assert.equal(elapsed(finish, '2000-03-01T00:00:00Z', observed), 'unavailable')
  for (const status of ['queued', 'running', 'unknown', 'completed', null]) {
    assert.equal(elapsed(start, finish, observed, status), 'unavailable', String(status))
  }
})

test('recorded elapsed keeps exact fractions across second, minute, hour and distant calendar boundaries', () => {
  for (const [start, finish, expected] of [
    ['2026-01-01T00:00:00Z', '2026-01-01T00:00:00.100000Z', '0.1s'],
    ['2026-01-01T00:00:00Z', '2026-01-01T00:00:59.999999Z', '59.999999s'],
    ['2026-01-01T00:00:00Z', '2026-01-01T00:01:00Z', '1m 0s'],
    ['2026-01-01T00:00:00.7Z', '2026-01-01T00:01:03Z', '1m 2.3s'],
    ['2026-01-01T00:00:00Z', '2026-01-01T01:00:00Z', '1h 0m 0s'],
    ['2026-01-01T00:00:00Z', '2026-01-02T01:02:03.000001Z', '25h 2m 3.000001s'],
    ['1969-12-31T23:59:59.999999Z', '1970-01-01T00:00:00.000001Z', '0.000002s'],
    ['0099-12-31T23:59:59.999999Z', '0100-01-01T00:00:00Z', '0.000001s'],
    ['9999-12-31T23:59:59.999998Z', '9999-12-31T23:59:59.999999Z', '0.000001s'],
    ['0001-01-01T00:00:00Z', '9999-12-31T23:59:59.999999Z', '87649415h 59m 59.999999s'],
  ]) assert.equal(elapsed(start, finish, finish), expected, start + ' to ' + finish)
})

test('missing or non-contract timestamps never produce an elapsed number by calendar normalization or rounding', () => {
  const valid = '2024-02-29T00:00:00Z'
  for (const missing of [null, undefined, '', 0]) {
    assert.equal(elapsed(missing, valid, valid), 'unavailable')
    assert.equal(elapsed(valid, missing, valid), 'unavailable')
    assert.equal(elapsed(valid, valid, missing), 'unavailable')
  }
  // Use the same bad literal for all three: rejecting an accidental inversion
  // cannot hide a parser that quietly accepts, rounds or normalizes the date.
  for (const bad of [
    '1900-02-29T00:00:00Z', '2024-04-31T00:00:00Z', '2024-00-01T00:00:00Z',
    '0000-01-01T00:00:00Z', '0001-01-01T00:00:00+00:01', '9999-12-31T23:59:59-00:01',
    '2024-02-29T24:00:00Z', '2024-02-29T00:60:00Z', '2024-02-29T23:59:60Z',
    '2024-02-29T00:00:00+24:00', '2024-02-29T00:00:00+00:60',
    '2024-02-29T00:00:00.0000001Z', '2024-02-29T00:00:00', '2024-02-29',
    '2024-02-29 00:00:00Z', '2024-02-29T00:00:00Z\n',
  ]) assert.equal(elapsed(bad, bad, bad), 'unavailable', JSON.stringify(bad))
})
