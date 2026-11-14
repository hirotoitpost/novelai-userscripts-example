// ヘルプの動画を録る(make_media.py から呼ぶ)。見本の環境の画面を Playwright で操作して録画する。
//   node record.mjs <scenario> <url> <out.webm>
// 見やすいよう、カーソルの丸と、いまの手順の字幕を画面に重ねる。
import { chromium } from 'playwright'
import { copyFile, mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'

const [, , scenario, url, out] = process.argv
const VIEW = { width: 960, height: 720 }

const overlay = () => {
  window.addEventListener('DOMContentLoaded', () => {
    const style = document.createElement('style')
    style.textContent = `
      #demo-cursor { position: fixed; z-index: 99999; width: 22px; height: 22px; margin: -11px 0 0 -11px; border-radius: 50%;
        background: rgba(255, 214, 10, 0.55); border: 2px solid #ffd60a; pointer-events: none; transition: transform .12s; left: -50px; top: -50px; }
      #demo-cursor.down { transform: scale(1.8); background: rgba(255, 214, 10, 0.85); }
      #demo-caption { position: fixed; z-index: 99998; left: 50%; top: 12px; transform: translateX(-50%); max-width: 90%;
        padding: 8px 16px; border-radius: 10px; background: rgba(10, 10, 20, 0.88); color: #fff; font-size: 18px; font-weight: 700;
        box-shadow: 0 4px 18px rgba(0,0,0,.5); pointer-events: none; display: none; }`
    document.head.appendChild(style)
    const cursor = document.createElement('div')
    cursor.id = 'demo-cursor'
    const caption = document.createElement('div')
    caption.id = 'demo-caption'
    document.body.append(cursor, caption)
    window.addEventListener('mousemove', e => { cursor.style.left = `${e.clientX}px`; cursor.style.top = `${e.clientY}px` }, true)
    window.addEventListener('mousedown', () => cursor.classList.add('down'), true)
    window.addEventListener('mouseup', () => cursor.classList.remove('down'), true)
  })
}

const browser = await chromium.launch()
const videoDir = await mkdtemp(path.join(tmpdir(), 'nai-video-'))
const context = await browser.newContext({ viewport: VIEW, locale: 'ja-JP', recordVideo: { dir: videoDir, size: VIEW } })
await context.addInitScript(() => {
  localStorage.setItem('nai_token', 'demo')
  localStorage.setItem('nai_admin_tab', JSON.stringify('ops'))
})
await context.addInitScript(overlay)
// 見本のトークン('demo')を NovelAI に確かめに行かない(401 だとログアウトされる)。見本の画面はこれしか使わない
await context.route('**/api/user/subscription', route => route.fulfill({
  status: 200, contentType: 'application/json', body: JSON.stringify({ tier: 3, active: true }),
}))
const page = await context.newPage()
const wait = ms => page.waitForTimeout(ms)

async function caption(text) {
  await page.evaluate(t => {
    const el = document.getElementById('demo-caption')
    if (!el) return
    el.textContent = t
    el.style.display = t ? 'block' : 'none'
  }, text)
  await wait(text ? 1200 : 200)
}

async function point(locator) {
  await locator.scrollIntoViewIfNeeded()
  const box = await locator.boundingBox()
  if (!box) throw new Error('要素が見えません')
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 18 })
  await wait(250)
}

async function click(locator) {
  await point(locator)
  await locator.click()
  await wait(500)
}

async function type(locator, text) {
  await click(locator)
  await locator.pressSequentially(text, { delay: 90 })
  await wait(500)
}

const section = page.locator('#data-backup')

async function open() {
  await page.goto(`${url}/admin?tab=ops#data-backup`)
  await section.waitFor()
  await page.waitForFunction(() => document.querySelectorAll('#data-backup table tr').length > 0)
  // この欄だけを見せる(ほかの欄には、見本の環境の一時フォルダのパス(利用者名が入る)が出るものがある。リポジトリは公開)
  await section.evaluate(el => {
    for (let node = el; node && node !== document.body; node = node.parentElement) {
      for (const sibling of node.parentElement?.children ?? []) {
        if (sibling !== node && !['demo-cursor', 'demo-caption'].includes(sibling.id)) sibling.style.display = 'none'
      }
    }
    el.style.marginTop = '56px'
    window.scrollTo({ top: 0 })
  })
  await wait(800)
}

async function save() {
  await click(section.getByRole('button', { name: '設定を保存' }))
  await section.getByText('設定を保存しました。').waitFor()
  await wait(800)
}

async function backupNow() {
  await click(section.getByRole('button', { name: '今すぐバックアップ' }))
  await section.getByText('バックアップ: 終わりました').waitFor({ timeout: 60000 })
  await wait(1000)
}

const scenarios = {
  'add-disk': async () => {
    await open()
    await caption('写し先が1つだけだと、2か所にそろいません')
    await wait(1800)
    await caption('1. 「写し先を足す」を押す')
    await click(section.getByRole('button', { name: '写し先を足す' }))
    await caption('2. 足すディスクのフォルダを入れる')
    await type(section.getByLabel('写し先 2 のフォルダ'), 'Y:\\backup')
    await caption('3. 「設定を保存」を押す')
    await save()
    await caption('4. 「今すぐバックアップ」を押す')
    await backupNow()
    await caption('2か所にそろいました(赤い警告が消える)')
    await point(section.locator('table').first())
    await wait(2500)
  },
  'remove-disk': async () => {
    await open()
    await caption('1. 外す写し先の「外す準備」にチェック')
    await click(section.getByText('外す準備').first())
    await caption('2. 「設定を保存」を押す')
    await save()
    await caption('3. 「今すぐバックアップ」で、ほかの写し先へ写す')
    await backupNow()
    await caption('「この写し先は外せます」と出たら…')
    await point(section.getByText('この写し先は外せます', { exact: false }).first())
    await wait(1800)
    await caption('4. その行の「外す」→「設定を保存」')
    await click(section.getByRole('button', { name: '外す', exact: true }).first())
    await save()
    await caption('外せました')
    await wait(2000)
  },
}

if (!scenarios[scenario]) throw new Error(`知らない動画です: ${scenario}`)
try {
  await scenarios[scenario]()
  await caption('')
} catch (error) {
  // 失敗したときの画面を残す(<out>.png)
  await page.screenshot({ path: `${out}.png`, fullPage: true }).catch(() => {})
  console.error(`失敗した画面: ${out}.png / ${page.url()}`)
  throw error
} finally {
  const video = page.video()
  await context.close()
  if (video) await copyFile(await video.path(), out)
  await browser.close()
}
