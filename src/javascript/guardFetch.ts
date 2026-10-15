/**
 * コンテンツガードの守りを緩める変更(止める語を減らす・未成年の定義を外す など)を送るときの fetch。
 *
 * バックエンドは、パスワードで開けた合言葉(X-Guard-Token)が無いと 403(detail.code = guard_locked)を返す。
 * そのときはパスワードを聞く小さな画面を出し、合っていれば合言葉をもらってもう一度送る。合言葉はこのタブの
 * sessionStorage に、使える時間のあいだだけ置く(10分)。パスワードそのものはどこにも残さない。
 */

const TOKEN_KEY = 'nai_guard_token'

interface Stored {
  token: string
  until: number
}

function storedToken(): string | null {
  try {
    const raw = sessionStorage.getItem(TOKEN_KEY)
    if (!raw) return null
    const stored: Stored = JSON.parse(raw)
    return stored.until > Date.now() ? stored.token : null
  } catch {
    return null
  }
}

function storeToken(token: string, seconds: number) {
  try {
    // バックエンドより少し早めに切らす
    sessionStorage.setItem(TOKEN_KEY, JSON.stringify({ token, until: Date.now() + (seconds - 15) * 1000 }))
  } catch {
    // 保存できなくても、この1回は送れる
  }
}

export function forgetGuardToken() {
  try {
    sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    // 何もしない
  }
}

export function guardHeaders(): Record<string, string> {
  const token = storedToken()
  return token ? { 'X-Guard-Token': token } : {}
}

/** パスワードを聞く(入力は伏せ字)。やめたら null。 */
export function askGuardPassword(message: string): Promise<string | null> {
  return new Promise(resolve => {
    const dialog = document.createElement('dialog')
    dialog.className = 'guard-dialog'
    const form = document.createElement('form')
    form.method = 'dialog'
    const text = document.createElement('p')
    text.textContent = message
    const input = document.createElement('input')
    input.type = 'password'
    input.autocomplete = 'current-password'
    input.placeholder = 'コンテンツガードのパスワード'
    input.setAttribute('aria-label', 'コンテンツガードのパスワード')
    const row = document.createElement('div')
    const ok = document.createElement('button')
    ok.type = 'submit'
    ok.value = 'ok'
    ok.textContent = '開ける'
    const cancel = document.createElement('button')
    cancel.type = 'submit'
    cancel.value = 'cancel'
    cancel.textContent = 'やめる'
    row.append(ok, cancel)
    form.append(text, input, row)
    dialog.append(form)
    // 画面ごとの CSS に頼らないよう、見た目はここで決める
    Object.assign(dialog.style, {
      background: '#14142a', color: '#e8e8ff', border: '1px solid #3a3a6a', borderRadius: '10px',
      padding: '1rem', maxWidth: 'min(24rem, calc(100vw - 32px))',
    })
    Object.assign(input.style, {
      width: '100%', boxSizing: 'border-box', background: '#0a0a14', color: '#e8e8ff',
      border: '1px solid #2a2a50', borderRadius: '6px', padding: '0.5rem', fontSize: '1rem',
    })
    Object.assign(text.style, { fontSize: '0.9rem', lineHeight: '1.6', margin: '0 0 0.6rem' })
    Object.assign(row.style, { display: 'flex', gap: '0.5rem', marginTop: '0.75rem' })
    for (const button of [ok, cancel]) {
      Object.assign(button.style, {
        background: button === ok ? '#7b5cf6' : 'transparent', color: '#fff', border: '1px solid #3a3a6a',
        borderRadius: '6px', padding: '0.4rem 1rem', cursor: 'pointer',
      })
    }
    document.body.append(dialog)
    dialog.addEventListener('close', () => {
      const value = dialog.returnValue === 'ok' && input.value ? input.value : null
      dialog.remove()
      resolve(value)
    })
    dialog.showModal()
    input.focus()
  })
}

/** パスワードで開けて、合言葉を覚える。合わなければ Error。 */
export async function unlockGuard(password: string): Promise<void> {
  const res = await fetch('/api/content-guard/unlock', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  })
  const body = await res.json().catch(() => null)
  if (!res.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `HTTP ${res.status}`)
  storeToken(body.token, body.seconds)
}

function failed(message: string): Response {
  return new Response(JSON.stringify({ detail: message }), { status: 403, headers: { 'Content-Type': 'application/json' } })
}

/**
 * 合言葉を付けて送る。守りを緩める変更でパスワードが要ると言われたら、聞いてからもう一度送る。
 * やめた・合わなかったときは、detail が文字列の 403 を返す(呼ぶ側はふつうのエラーとして出せばよい)。
 */
export async function guardFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const send = () => fetch(url, { ...init, headers: { ...(init.headers as Record<string, string>), ...guardHeaders() } })
  const res = await send()
  if (res.status !== 403) return res
  const body = await res.clone().json().catch(() => null)
  const detail = body?.detail
  if (detail?.code !== 'guard_locked') return res
  if (!detail.password_set) return failed(`${detail.message}(管理ページのコンテンツガードの欄)`)
  forgetGuardToken()
  for (;;) {
    const password = await askGuardPassword(detail.message)
    if (password === null) return failed('パスワードを入れなかったので、変更しませんでした。')
    try {
      await unlockGuard(password)
      return send()
    } catch (e) {
      detail.message = `${e instanceof Error ? e.message : String(e)} もう一度入れてください。`
    }
  }
}
