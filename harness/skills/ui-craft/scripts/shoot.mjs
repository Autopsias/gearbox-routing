#!/usr/bin/env node
// Frames of a real page for a ui-craft REVIEW: a full-page shot and a first-screen
// shot per page and width, plus one measured line each (also in <out>/shots.json).
// Uses Stageshow's own browser launcher from the work copy (scripts/stage.sh), so an
// upstream fix reaches us.
//
// node shoot.mjs --stage <work copy> <url|file> --out <dir> [--name page]
//   [--widths 1366,390] [--init seed.js] [--wait <selector>] [--delay 400]
//   [--pages "#home,#white,/about"]   shoot these, relative to <url>
//   [--crawl [--max 150]]            follow every same-origin link from <url> instead
//   [--list]                         print the pages found, shoot nothing
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const o = { name: 'page', widths: '1366,390', delay: '400', wait: 'body', max: '150' }
let target
const a = process.argv.slice(2)
for (let i = 0; i < a.length; i++) {
  const m = /^--(stage|out|name|widths|init|wait|delay|pages|max)$/.exec(a[i])
  if (m) o[m[1]] = a[++i]
  else if (a[i] === '--crawl' || a[i] === '--list') o[a[i].slice(2)] = true
  else if (!target) target = a[i]
  else { console.error(`shoot: unexpected argument ${a[i]}`); process.exit(2) }
}
if (!o.stage || !target || (!o.out && !o.list)) {
  console.error('usage: node shoot.mjs --stage <dir> <url|file> --out <dir> [--name] [--widths] [--init] [--wait] [--delay] [--pages a,b | --crawl [--max n]] [--list]')
  process.exit(2)
}
const url = /^[a-z]+:\/\//.test(target) ? target : pathToFileURL(resolve(target)).href
const { launch } = await import(pathToFileURL(resolve(o.stage, 'scripts/browser.mjs')).href)
const init = o.init ? readFileSync(o.init, 'utf8') : null
const widths = o.widths.split(',').map(Number)
const browser = await launch()

async function open(href, w) {
  const phone = w < 600
  const ctx = await browser.newContext({ viewport: { width: w, height: phone ? 844 : 900 }, deviceScaleFactor: phone ? 2 : 1 })
  if (init) await ctx.addInitScript(init)
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto(href, { waitUntil: 'load' })
  await page.locator(o.wait).first().waitFor({ state: 'visible', timeout: 10000 })
  await page.evaluate(() => document.fonts.ready)
  await page.waitForTimeout(Number(o.delay))
  return { ctx, page, errors }
}

const start = new URL(url).hash || ''
const rootUrl = url.replace(/#.*$/, '')
const root = new URL(rootUrl)
const rootDir = root.pathname.replace(/[^/]*$/, '')
// Every page we open must stay where <url> is: same protocol and host, and under the
// start file's folder (file:// has one shared origin, so an origin test alone would let
// a link reach any local file).
const inScope = (u) => u.protocol === root.protocol && u.host === root.host && u.pathname.startsWith(rootDir)
const resolve_ = (p) => { const u = new URL(p, rootUrl); if (!inScope(u)) throw new Error(`out of scope: ${u.href}`); return u.href }

// In-scope links, as keys relative to <url>: '#x' for the same document, else the path.
async function links(page) {
  const out = new Set()
  for (const href of await page.evaluate(() => [...document.querySelectorAll('a[href]')]
    .map((a) => a.getAttribute('href')).filter((h) => h && h !== '#' && !/^(mailto|tel|javascript):/i.test(h))
    .map((h) => new URL(h, location.href).href))) {
    const u = new URL(href)
    if (!inScope(u)) continue
    if (u.pathname === root.pathname) { if (u.hash.length > 1) out.add(u.hash) }
    else out.add(u.pathname + u.search)
  }
  return [...out]
}
let pages = o.pages ? o.pages.split(',').map((s) => s.trim()) : [start]
if (o.crawl) {
  // shortcut: crawls at the first width only; a link that exists only at phone width is missed.
  const seen = new Set([start]), queue = [start]
  while (queue.length && seen.size < Number(o.max)) {
    const p = queue.shift()
    const { ctx, page } = await open(resolve_(p), widths[0])
    for (const l of await links(page)) if (!seen.has(l) && seen.size < Number(o.max)) { seen.add(l); queue.push(l) }
    await ctx.close()
  }
  pages = [...seen]
  console.log(`crawl: ${pages.length} pages${seen.size >= Number(o.max) ? ` (stopped at --max ${o.max})` : ''}`)
}
if (o.list) { console.log(pages.join('\n')); await browser.close(); process.exit(0) }

mkdirSync(o.out, { recursive: true })
const slug = (p) => p.replace(/^[#/]+/, '').replace(/[^\w-]+/g, '_') || 'top'
const shots = []
let failed = false
for (const p of pages) {
  for (const w of widths) {
    const { ctx, page, errors } = await open(resolve_(p), w)
    const m = await page.evaluate(() => {
      const d = document.documentElement
      return {
        screens: Number((d.scrollHeight / innerHeight).toFixed(1)),
        words: (document.body.innerText.match(/\S+/g) || []).length,
        overflow: d.scrollWidth > innerWidth ? d.scrollWidth - innerWidth : 0,
        pinned: [...document.querySelectorAll('body *')].filter((e) => /fixed|sticky/.test(getComputedStyle(e).position)).length,
        h1: document.querySelector('h1')?.innerText.trim().slice(0, 80) ?? '(no h1)',
      }
    })
    const name = pages.length > 1 ? `${o.name}-${slug(p)}` : o.name
    const base = resolve(o.out, `${name}-${w}`)
    await page.screenshot({ path: `${base}.png`, fullPage: true })
    await page.screenshot({ path: `${base}-top.png` })
    shots.push({ page: p, width: w, frame: `${name}-${w}.png`, ...m, errors })
    console.log(`${name} @${w}: ${m.screens} screens, ${m.words} words, h1 "${m.h1}"` +
      (m.overflow ? `, HORIZONTAL OVERFLOW ${m.overflow}px` : '') +
      (m.pinned ? `, ${m.pinned} fixed/sticky` : '') +
      (errors.length ? `, PAGE ERRORS: ${errors.join(' | ')}` : ''))
    if (errors.length) failed = true
    await ctx.close()
  }
}
await browser.close()
writeFileSync(resolve(o.out, 'shots.json'), JSON.stringify(shots, null, 1))
if (shots.some((s) => s.pinned)) console.log('fixed/sticky parts: judge them on -top frames; a full-page shot draws them once')
if (!existsSync(o.out)) process.exit(2)
process.exit(failed ? 1 : 0)
