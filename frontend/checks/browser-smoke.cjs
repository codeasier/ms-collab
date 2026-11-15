/* Copyright (c) 2026 codeasier. MIT; see ../../LICENSE. */
// Optional acceptance check. Install the CI-pinned Playwright into a temporary
// location and expose it via NODE_PATH; the application has no npm dependencies.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { spawn } = require('node:child_process')
const { chromium } = require('playwright')

async function within(promise, message) {
  let timer
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(message)), 10000)
    })])
  } finally { clearTimeout(timer) }
}

async function startServer() {
  const child = spawn('python3', ['-m', 'ms_collab', '--port', '0'], {
    cwd: path.resolve(__dirname, '../..'),
    env: { ...process.env, PYTHONPATH: 'backend' },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  child.stderr.resume()
  const origin = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { child.kill(); reject(new Error('Demo startup timed out')) }, 15000)
    let output = ''
    child.stdout.on('data', chunk => {
      output += chunk.toString()
      const match = output.match(/http:\/\/127\.0\.0\.1:\d+/)
      if (match) { clearTimeout(timer); resolve(match[0]) }
    })
    child.once('error', error => { clearTimeout(timer); reject(error) })
    child.once('exit', code => { clearTimeout(timer); reject(new Error(`Demo exited before startup: ${code}`)) })
  })
  return { child, origin }
}

async function main() {
  const { child, origin } = await startServer()
  let browser
  try {
    browser = await chromium.launch({ headless: true })
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
    const externalRequests = []
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin === origin) return route.continue()
      externalRequests.push(route.request().url())
      return route.abort()
    })
    const page = await context.newPage()
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto(origin)
    await page.locator('#workspace-panel:not([hidden])').waitFor()
    assert.equal(await page.locator('#workspace-title').textContent(), 'Product Alpha')
    assert.equal(await page.locator('#open-items').textContent(), '5')
    await page.waitForFunction(() => document.querySelector('#board-items').children.length === 2)
    assert.match(await page.locator('#board-items').textContent(), /Improve compiler diagnostics/)
    assert.equal(await page.locator('#board-items a').count(), 0)
    assert.equal(await page.locator('#workspace option').count(), 2)
    assert.equal(await page.locator('#workspace option[value="product-beta"]').count(), 0)
    await page.locator('#workspace').selectOption('commons')
    await page.waitForFunction(() => document.querySelector('#workspace-title').textContent === 'Shared collaboration')
    assert.equal(await page.locator('#open-items').textContent(), '1')
    assert.match(await page.locator('#workspace-kind').textContent(), /membership required/)

    await page.locator('#retry').click()
    await page.locator('#retry').click()
    await page.waitForFunction(() => document.querySelector('#workspace-title').textContent === 'Product Alpha')
    let markSeen, releaseOld, markHandled
    let intercepted = 0
    const oldSeen = new Promise(resolve => { markSeen = resolve })
    const oldGate = new Promise(resolve => { releaseOld = resolve })
    const oldHandled = new Promise(resolve => { markHandled = resolve })
    await page.route('**/api/workspaces/product-alpha/overview', async route => {
      intercepted += 1
      markSeen()
      await oldGate
      await route.continue().catch(() => {}) // Superseded request may already be aborted.
      markHandled()
    })
    await page.locator('#workspace').selectOption('commons')
    await page.locator('#workspace').selectOption('product-alpha')
    // Prove an overview is actually in flight, rather than cancelling its earlier
    // module discovery and accidentally testing no late response at all.
    await within(oldSeen, 'Old overview was not intercepted')
    await page.locator('#workspace').selectOption('commons')
    await page.waitForFunction(() => document.querySelector('#workspace-title').textContent === 'Shared collaboration')
    releaseOld()
    await within(oldHandled, 'Old request was not released')
    assert.equal(intercepted, 1)
    assert.equal(await page.locator('#workspace-title').textContent(), 'Shared collaboration')
    assert.equal(await page.locator('#open-items').textContent(), '1')

    // Query filtering, empty state and pagination use real local HTTP.
    await page.locator('#workspace').selectOption('product-alpha')
    await page.waitForFunction(() => document.querySelector('#board-items').children.length === 2)
    await page.locator('#board-limit').selectOption('1')
    await page.locator('#board-filters button').click()
    await page.waitForFunction(() => document.querySelector('#board-page').textContent === '1–1 of 2')
    assert.match(await page.locator('#board-items').textContent(), /Improve compiler/)
    // Unapplied draft filters must not change the next-page request.
    await page.locator('#board-query').fill('not-yet-applied')
    await page.locator('#board-next').click()
    await page.waitForFunction(() => document.querySelector('#board-page').textContent === '2–2 of 2')
    assert.match(await page.locator('#board-items').textContent(), /Add synthetic parser fixtures/)
    await page.locator('#board-filters button').click()
    await page.waitForFunction(() => document.querySelector('#board-status').textContent === 'No matching observed items.')
    assert.equal(await page.locator('#board-items').textContent(), '')
    assert.equal(await page.locator('#board-next').isDisabled(), true)
    await page.locator('#board-query').fill('synthetic')
    await page.locator('#board-kind').selectOption('pull')
    await page.locator('#board-state').selectOption('merged')
    await page.locator('#board-filters button').click()
    await page.waitForFunction(() => document.querySelector('#board-page').textContent === '1–1 of 1')
    assert.match(await page.locator('#board-items').textContent(), /Add synthetic parser fixtures/)

    // Late board responses may not repopulate a newer workspace.
    let boardSeenResolve, boardRelease, boardHandledResolve
    const boardSeen = new Promise(resolve => { boardSeenResolve = resolve })
    const boardGate = new Promise(resolve => { boardRelease = resolve })
    const boardHandled = new Promise(resolve => { boardHandledResolve = resolve })
    await page.route('**/api/workspaces/product-alpha/community-board?*', async route => {
      boardSeenResolve()
      await boardGate
      await route.continue().catch(() => {})
      boardHandledResolve()
    })
    await page.locator('#board-filters button').click()
    await within(boardSeen, 'Old board was not intercepted')
    await page.locator('#workspace').selectOption('commons')
    await page.waitForFunction(() => document.querySelector('#board-items').textContent.includes('Document contribution workflow'))
    boardRelease()
    await within(boardHandled, 'Old board was not released')
    assert.equal(await page.locator('#board-items').textContent().then(value => value.includes('compiler')), false)
    assert.equal(await page.locator('#board-kind').inputValue(), '')
    await page.unroute('**/api/workspaces/product-alpha/community-board?*')

    // A failed board load clears old rows, preserves the shell, and retries.
    await page.route('**/api/workspaces/commons/community-board?*', route => route.fulfill({ status: 503, body: '{}' }))
    await page.locator('#board-filters button').click()
    await page.waitForFunction(() => document.querySelector('#board-status').textContent.includes('retry'))
    assert.equal(await page.locator('#board-items').textContent(), '')
    assert.equal(await page.locator('#workspace-panel').isVisible(), true)
    await page.unroute('**/api/workspaces/commons/community-board?*')
    await page.locator('#board-filters button').click()
    await page.waitForFunction(() => document.querySelector('#board-items').textContent.includes('Document contribution workflow'))

    // Untrusted titles are text; a wrong workspace envelope is rejected entirely.
    await page.route('**/api/workspaces/commons/community-board?*', async route => {
      const response = await route.fetch()
      const data = await response.json()
      data.items[0].title = '<img src=x onerror=alert(1)>'
      await route.fulfill({ response, json: data })
    })
    await page.locator('#board-filters button').click()
    await page.waitForFunction(() => document.querySelector('#board-items').textContent.includes('<img'))
    assert.equal(await page.locator('#board-items img').count(), 0)
    await page.unroute('**/api/workspaces/commons/community-board?*')
    await page.route('**/api/workspaces/commons/community-board?*', async route => {
      const response = await route.fetch()
      const data = await response.json()
      data.items[0].workspace_id = 'product-beta'
      await route.fulfill({ response, json: data })
    })
    await page.locator('#board-filters button').click()
    await page.waitForFunction(() => document.querySelector('#board-status').textContent.includes('Invalid scoped board'))
    assert.equal(await page.locator('#board-items').textContent(), '')
    await page.unroute('**/api/workspaces/commons/community-board?*')
    await page.locator('#board-filters button').click()
    await page.waitForFunction(() => document.querySelector('#board-items').textContent.includes('Document contribution workflow'))

    const output = path.join(process.env.RUNNER_TEMP || os.tmpdir(), 'ms-collab-browser')
    fs.mkdirSync(output, { recursive: true })
    await page.screenshot({ path: path.join(output, 'desktop.png'), fullPage: true })
    await page.unroute('**/api/workspaces/product-alpha/overview')
    await page.route('**/api/workspaces/product-alpha/modules', route => route.fulfill({ status: 503, body: '{}' }))
    await page.locator('#workspace').selectOption('product-alpha')
    await page.waitForFunction(() => document.querySelector('#status').textContent.includes('retry'))
    assert.equal(await page.locator('#workspace-panel').getAttribute('hidden'), '')
    await page.unroute('**/api/workspaces/product-alpha/modules')
    await page.locator('#retry').click()
    await page.waitForFunction(() => document.querySelector('#workspace-title').textContent === 'Product Alpha')
    await page.reload()
    await page.locator('#workspace-panel:not([hidden])').waitFor()
    assert.equal(await page.locator('#workspace-title').textContent(), 'Product Alpha')
    await page.waitForFunction(() => document.querySelector('#board-items').children.length === 2)
    await page.setViewportSize({ width: 390, height: 844 })
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
    await page.screenshot({ path: path.join(output, 'mobile.png'), fullPage: true })
    assert.deepEqual(errors, [])
    assert.deepEqual(externalRequests, [])
    console.log('PASS: desktop/narrow layout, membership-visible spaces, repeated reload, stale response suppression, error recovery; board filtering, pagination, stale requests, escaped text and scope envelope; no external requests or page errors.')
  } finally {
    try { if (browser) await browser.close() } finally { child.kill() }
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1 })
