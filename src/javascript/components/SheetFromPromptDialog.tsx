import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { apiFetch } from '../api'
import type { Character } from './StoryCharacters'
import './SheetFromPromptDialog.css'

/** キャラシートに反映する元の入力(画像生成ページの入力欄、または画像に埋め込まれていた設定) */
export interface SheetSource {
  prompt: string
  negative: string
  seed: number | null
  /** キャラごとのプロンプト(あれば、そのキャラの容姿として使う) */
  characters: { prompt: string; negative: string }[]
}

interface Props {
  source: SheetSource
  token: string
  onClose: () => void
  /** 保存できた。created は新しく作ったか(false なら上書き) */
  onSaved: (character: Character, created: boolean) => void
}

interface Parts { appearance: string; outfit: string; style: string; other: string }
type FieldKey = 'appearance_tags' | 'outfit_tags' | 'style_tags' | 'negative_tags' | 'seed'
interface Field { use: boolean; value: string }

const FIELDS: { key: FieldKey; label: string; hint: string }[] = [
  { key: 'appearance_tags', label: '容姿', hint: '変えない見た目(人数・髪・目・体)' },
  { key: 'outfit_tags', label: '普段の服装', hint: '服・小物' },
  { key: 'style_tags', label: '画風', hint: '品質・画風のタグ' },
  { key: 'negative_tags', label: 'ネガティブ', hint: 'このキャラで避けたいもの' },
  { key: 'seed', label: '基準シード', hint: '同じ見た目で出すためのシード値' },
]
const EMPTY: Parts = { appearance: '', outfit: '', style: '', other: '' }

function joinTags(...parts: string[]): string {
  const seen = new Set<string>()
  const out: string[] = []
  for (const tag of parts.join(',').split(',').map(t => t.trim()).filter(Boolean)) {
    if (!seen.has(tag.toLowerCase())) {
      seen.add(tag.toLowerCase())
      out.push(tag)
    }
  }
  return out.join(', ')
}

/**
 * 入力値をキャラシートに反映するダイアログ。プロンプトを容姿・服装・画風に分けた下書きを出し、確かめて直してから、
 * 新しいキャラとして追加するか、既存のキャラに上書きする。反映するのはチェックした項目だけ(上書きでは、
 * チェックしていない項目は今の値のまま)。表情・ポーズ・場所などは場面ごとに変わるので、キャラシートには入れない。
 */
export default function SheetFromPromptDialog({ source, token, onClose, onSaved }: Props) {
  const [characters, setCharacters] = useState<Character[]>([])
  // どのプロンプトを容姿の元にするか(キャラごとのプロンプトの番号。-1 は全体のプロンプト)
  const [from, setFrom] = useState(source.characters.some(c => c.prompt.trim()) ? 0 : -1)
  const [other, setOther] = useState('')
  const [fields, setFields] = useState<Record<FieldKey, Field>>({
    appearance_tags: { use: false, value: '' },
    outfit_tags: { use: false, value: '' },
    style_tags: { use: false, value: '' },
    negative_tags: { use: false, value: '' },
    seed: { use: source.seed != null, value: source.seed != null ? String(source.seed) : '' },
  })
  const [mode, setMode] = useState<'new' | 'overwrite'>('new')
  const [name, setName] = useState('')
  const [targetId, setTargetId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    apiFetch<Character[]>(token, '/api/story/characters').then(setCharacters).catch(() => {})
  }, [token])

  // 元にするプロンプトを選び直したら、分け直す。画風は全体のプロンプトからも取る(品質のタグは全体の側にある)
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    const chosen = from >= 0 ? source.characters[from] : null
    const split = (prompt: string) => (prompt.trim()
      ? apiFetch<Parts>(token, '/api/story/characters/split-prompt', { prompt })
      : Promise.resolve(EMPTY))
    Promise.all([split(source.prompt), chosen ? split(chosen.prompt) : Promise.resolve(null)])
      .then(([main, own]) => {
        if (cancelled) return
        const look = own ?? main
        // ネガティブ: キャラごとのものがあればそれ。全体のネガティブは品質用のものが多いので、入れるかは選んでもらう
        const ownNegative = chosen?.negative.trim() ?? ''
        const style = joinTags(main.style, own?.style ?? '')
        setFields(prev => ({
          ...prev,
          appearance_tags: { use: !!look.appearance, value: look.appearance },
          outfit_tags: { use: !!look.outfit, value: look.outfit },
          style_tags: { use: !!style, value: style },
          negative_tags: ownNegative
            ? { use: true, value: ownNegative }
            : { use: false, value: source.negative.trim() },
        }))
        setOther(joinTags(look.other, own ? main.other : ''))
      })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [from, source, token])

  const target = useMemo(() => characters.find(c => c.id === targetId) ?? null, [characters, targetId])
  const chosenKeys = FIELDS.map(f => f.key).filter(key => fields[key].use && fields[key].value.trim())
  const seedInvalid = fields.seed.use && fields.seed.value.trim() !== ''
    && !/^\d+$/.test(fields.seed.value.trim())
  const ready = chosenKeys.length > 0 && !seedInvalid && !loading
    && (mode === 'new' ? name.trim() !== '' : targetId !== null)

  function currentOf(key: FieldKey): string {
    if (!target) return ''
    const value = target[key]
    return value == null || value === '' ? '(なし)' : String(value)
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!ready || busy) return
    setBusy(true)
    setError(null)
    const sheet: Record<string, string | number> = {}
    for (const key of chosenKeys) {
      sheet[key] = key === 'seed' ? Number(fields.seed.value.trim()) : fields[key].value.trim()
    }
    try {
      if (mode === 'new') {
        onSaved(await apiFetch<Character>(token, '/api/story/characters/new', { name: name.trim(), ...sheet }), true)
      } else {
        const res = await fetch(`/api/story/characters/${targetId}/sheet`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
          body: JSON.stringify(sheet),
        })
        const data = await res.json().catch(() => ({ detail: res.statusText }))
        if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${res.status}`)
        onSaved(data as Character, false)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  return (
    <div className="sfp-overlay" onClick={busy ? undefined : onClose}>
      <form className="sfp-dialog" role="dialog" aria-modal="true" aria-labelledby="sfp-title"
        onClick={e => e.stopPropagation()} onSubmit={e => void submit(e)}>
        <h2 id="sfp-title">キャラシートに反映</h2>
        <p className="sfp-hint">
          入力値を、キャラシートの項目に分けて入れます。下書きを確かめて、必要なら直してください。
          反映するのはチェックした項目だけです。
        </p>

        {source.characters.some(c => c.prompt.trim()) && (
          <label className="sfp-row">
            <span>容姿の元にするプロンプト</span>
            <select value={from} onChange={e => setFrom(Number(e.target.value))} disabled={busy}>
              {source.characters.map((c, i) => c.prompt.trim() && (
                <option key={i} value={i}>キャラ{i + 1}: {c.prompt.slice(0, 40)}</option>
              ))}
              <option value={-1}>全体のプロンプト</option>
            </select>
          </label>
        )}

        <div className="sfp-fields" aria-busy={loading}>
          {FIELDS.map(({ key, label, hint }) => (
            <div key={key} className="sfp-field">
              <label className="sfp-check">
                <input type="checkbox" checked={fields[key].use} disabled={busy}
                  onChange={e => setFields({ ...fields, [key]: { ...fields[key], use: e.target.checked } })} />
                <strong>{label}</strong>
                <span className="sfp-hint">{hint}</span>
              </label>
              {key === 'seed' ? (
                <input type="text" inputMode="numeric" value={fields[key].value} disabled={busy} aria-label={label}
                  placeholder="(シード値なし)"
                  onChange={e => setFields({ ...fields, [key]: { use: e.target.value.trim() !== '', value: e.target.value } })} />
              ) : (
                <textarea rows={2} spellCheck={false} value={fields[key].value} disabled={busy} aria-label={label}
                  onChange={e => setFields({ ...fields, [key]: { ...fields[key], value: e.target.value } })} />
              )}
              {mode === 'overwrite' && target && fields[key].use && (
                <p className="sfp-current">今の値: {currentOf(key)}</p>
              )}
            </div>
          ))}
          {other && (
            <p className="sfp-other">
              <strong>キャラシートに入れないタグ</strong>(表情・ポーズ・場所など。場面ごとに変わるため): {other}
            </p>
          )}
          {seedInvalid && <p className="sfp-error">基準シードは数字で入れてください。</p>}
        </div>

        <div className="sfp-target" role="radiogroup" aria-label="反映先">
          <label className="sfp-check">
            <input type="radio" name="sfp-mode" checked={mode === 'new'} onChange={() => setMode('new')} disabled={busy} />
            <strong>新しいキャラとして追加</strong>
          </label>
          {mode === 'new' && (
            <input className="sfp-name" value={name} maxLength={100} placeholder="キャラの名前" disabled={busy}
              aria-label="新しいキャラの名前" onChange={e => setName(e.target.value)} />
          )}
          <label className="sfp-check">
            <input type="radio" name="sfp-mode" checked={mode === 'overwrite'} onChange={() => setMode('overwrite')}
              disabled={busy || characters.length === 0} />
            <strong>既存のキャラに上書き</strong>
            <span className="sfp-hint">チェックしていない項目は、今の値のまま</span>
          </label>
          {mode === 'overwrite' && (
            <select className="sfp-name" value={targetId ?? ''} disabled={busy} aria-label="上書きするキャラ"
              onChange={e => setTargetId(e.target.value ? Number(e.target.value) : null)}>
              <option value="">キャラを選択...</option>
              {characters.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          )}
        </div>

        {error && <p className="sfp-error" role="alert">{error}</p>}
        <div className="sfp-actions">
          <button type="button" onClick={onClose} disabled={busy}>やめる</button>
          <button type="submit" className="sfp-primary" disabled={!ready || busy}>
            {busy ? '保存中…' : mode === 'new' ? '追加する' : `上書きする(${chosenKeys.length}項目)`}
          </button>
        </div>
      </form>
    </div>
  )
}
