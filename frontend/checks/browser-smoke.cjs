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
    await page.setViewportSize({ width: 390, height: 844 })
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
    await page.screenshot({ path: path.join(output, 'mobile.png'), fullPage: true })
    assert.deepEqual(errors, [])
    assert.deepEqual(externalRequests, [])
    console.log('PASS: desktop/narrow layout, membership-visible spaces, repeated reload, stale response suppression, error recovery; no external requests or page errors.')
  } finally {
    try { if (browser) await browser.close() } finally { child.kill() }
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1 })
