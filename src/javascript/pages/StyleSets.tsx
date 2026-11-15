import { useEffect, useState } from 'react'
import BackButton from '../components/BackButton'
import { StyleSetPreview, useStyleSets } from '../components/StyleSetPicker'
import type { StyleSet } from '../api'
import { API_ORIGIN, readErrorDetail, thumbUrl } from '../library'
import './LoraDataset.css'
import './StyleSets.css'

/**
 * 絵柄セットのページ。絵柄だけの参照画像(Character Reference の style)と強さ・忠実度、絵柄タグ・ネガティブを
 * 名前を付けて保存し、キャラに選ばせる。タグだけでは同じ絵柄にならないので、気に入った絵を参照として毎回渡すと、
 * 外見の違うキャラでも同じタッチ・線・色使いで描ける。ギャラリーの画像からも作れる(画像を開いて「絵柄セットにする」)。
 */

interface CharacterLite {
  id: number
  name: string
  reference_image_path?: string | null
  style_set_id?: number | null
}

interface Draft {
  name: string
  strength: number
  fidelity: number
  style_tags: string
  negative_tags: string
  notes: string
}

const NEW_DRAFT: Draft = {
  name: '', strength: 0.8, fidelity: 1.0,
  style_tags: 'anime coloring, delicate thin lineart, soft shading, subtle gradients, very aesthetic, masterpiece',
  negative_tags: 'thick lineart, flat color', notes: '',
}

function toDraft(s: StyleSet): Draft {
  return { name: s.name, strength: s.strength, fidelity: s.fidelity, style_tags: s.style_tags, negative_tags: s.negative_tags, notes: s.notes }
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

async function send(method: string, path: string, body?: unknown): Promise<StyleSet | null> {
  const res = await fetch(`${API_ORIGIN}${path}`, {
    method, headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new Error(await readErrorDetail(res))
  return res.status === 204 ? null : res.json()
}

function DraftFields({ draft, onChange, disabled }: { draft: Draft; onChange: (d: Draft) => void; disabled: boolean }) {
  return (
    <>
      <label className="sts-field">
        <span>名前</span>
        <input className="lora-input" value={draft.name} disabled={disabled} maxLength={60}
          onChange={e => onChange({ ...draft, name: e.target.value })} />
      </label>
      <div className="sts-row">
        <label className="sts-field">
          <span>強さ {draft.strength.toFixed(2)}</span>
          <input type="range" min={0} max={1} step={0.05} value={draft.strength} disabled={disabled}
            onChange={e => onChange({ ...draft, strength: Number(e.target.value) })} />
        </label>
        <label className="sts-field">
          <span>忠実度 {draft.fidelity.toFixed(2)}</span>
          <input type="range" min={0} max={1} step={0.05} value={draft.fidelity} disabled={disabled}
            onChange={e => onChange({ ...draft, fidelity: Number(e.target.value) })} />
        </label>
      </div>
      <label className="sts-field">
        <span>絵柄タグ(プロンプトに足す)</span>
        <textarea className="lora-input" rows={2} value={draft.style_tags} disabled={disabled}
          onChange={e => onChange({ ...draft, style_tags: e.target.value })} />
      </label>
      <label className="sts-field">
        <span>ネガティブ(この絵柄にしたくない描き方)</span>
        <textarea className="lora-input" rows={1} value={draft.negative_tags} disabled={disabled}
          onChange={e => onChange({ ...draft, negative_tags: e.target.value })} />
      </label>
      <label className="sts-field">
        <span>メモ</span>
        <input className="lora-input" value={draft.notes} disabled={disabled}
          onChange={e => onChange({ ...draft, notes: e.target.value })} />
      </label>
    </>
  )
}

export default function StyleSets() {
  const { sets, error: loadError, reload } = useStyleSets()
  const [characters, setCharacters] = useState<CharacterLite[]>([])
  const [drafts, setDrafts] = useState<Record<number, Draft>>({})
  const [newDraft, setNewDraft] = useState<Draft>(NEW_DRAFT)
  const [newImage, setNewImage] = useState<string | null>(null)
  const [adding, setAdding] = useState<Record<number, string>>({})
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null)
  const [busy, setBusy] = useState(false)

  function loadCharacters() {
    fetch(`${API_ORIGIN}/api/story/characters`).then(r => (r.ok ? r.json() : [])).then(setCharacters).catch(() => {})
  }
  useEffect(loadCharacters, [])

  async function run(action: () => Promise<unknown>, done: string) {
    setBusy(true)
    setMessage(null)
    try {
      await action()
      setMessage({ text: done, error: false })
      reload()
      loadCharacters()
    } catch (e) {
      setMessage({ text: e instanceof Error ? e.message : String(e), error: true })
    } finally {
      setBusy(false)
    }
  }

  const create = () => run(async () => {
    if (!newImage) throw new Error('絵柄の参照にする画像を選んでください')
    await send('POST', '/api/style-sets', { ...newDraft, name: newDraft.name.trim(), image: newImage })
    setNewDraft(NEW_DRAFT)
    setNewImage(null)
  }, '絵柄セットを作りました')

  const save = (set: StyleSet) => run(async () => {
    await send('PUT', `/api/style-sets/${set.id}`, drafts[set.id])
    setDrafts(prev => { const next = { ...prev }; delete next[set.id]; return next })
  }, `「${set.name}」を保存しました`)

  const replaceImage = (set: StyleSet, file: File) => run(async () => {
    await send('PUT', `/api/style-sets/${set.id}`, { image: await readAsDataUrl(file) })
  }, `「${set.name}」の画像を差し替えました`)

  const remove = (set: StyleSet) => {
    if (!window.confirm(`絵柄セット「${set.name}」を消します。選んでいたキャラはセットなしに戻ります(キャラは消えません)。よろしいですか?`)) return
    void run(() => send('DELETE', `/api/style-sets/${set.id}`), `「${set.name}」を消しました`)
  }

  const addCharacter = (set: StyleSet) => {
    const id = Number(adding[set.id])
    if (!id) return
    void run(async () => {
      await send('PUT', `/api/style-sets/${set.id}/characters`, { character_ids: [id] })
      setAdding(prev => ({ ...prev, [set.id]: '' }))
    }, 'キャラをセットに入れました')
  }

  const removeCharacter = (set: StyleSet, characterId: number) =>
    void run(() => send('DELETE', `/api/style-sets/${set.id}/characters/${characterId}`), 'キャラをセットから外しました')

  return (
    <div className="lora-root">
      <header className="lora-header">
        <BackButton className="lora-back" />
        <span className="lora-header-title">絵柄セット</span>
      </header>
      <main className="sts-main">
      <p className="lora-hint">
        気に入った絵を「絵柄だけ」の参照として保存し、キャラに選ばせます。選んだキャラは、参照画像の候補・画像生成ページ・
        Claude の生成でこの絵柄になります(参照1枚ぶん Anlas が増えます。漫画のコマには足しません)。
        ギャラリーの画像を開いて「絵柄セットにする」からも作れます。
      </p>
      {loadError && <p className="lora-error">{loadError}</p>}
      {message && <p className={message.error ? 'lora-error' : 'sts-ok'} role="status">{message.text}</p>}

      {sets.map(set => {
        const draft = drafts[set.id] ?? toDraft(set)
        const dirty = drafts[set.id] !== undefined
        const outside = characters.filter(c => c.style_set_id !== set.id)
        return (
          <section key={set.id} className="sts-card">
            <div className="sts-media">
              <StyleSetPreview set={set} samples={0} />
              <label className="lora-btn lora-btn--secondary sts-file">
                画像を差し替える
                <input type="file" accept="image/*" hidden disabled={busy}
                  onChange={e => { const f = e.target.files?.[0]; if (f) void replaceImage(set, f); e.target.value = '' }} />
              </label>
            </div>
            <div className="sts-body">
              <DraftFields draft={draft} disabled={busy} onChange={d => setDrafts(prev => ({ ...prev, [set.id]: d }))} />
              <div className="sts-actions">
                <button type="button" className="lora-btn" disabled={busy || !dirty || !draft.name.trim()} onClick={() => void save(set)}>保存</button>
                <button type="button" className="lora-btn lora-btn--secondary" disabled={busy || !dirty}
                  onClick={() => setDrafts(prev => { const next = { ...prev }; delete next[set.id]; return next })}>元に戻す</button>
                <button type="button" className="lora-btn lora-btn--danger" disabled={busy} onClick={() => remove(set)}>消す</button>
              </div>
              <div className="sts-members">
                <span className="sts-members-title">この絵柄のキャラ(見本)</span>
                <div className="sts-member-list">
                  {set.members.length === 0 && <span className="sts-empty">まだいません</span>}
                  {set.members.map(m => (
                    <div key={m.id} className="sts-member">
                      {m.reference_image_path
                        ? <img src={thumbUrl(m.reference_image_path, 240)} alt={m.name} loading="lazy" />
                        : <div className="sts-noimg">参照画像なし</div>}
                      <span>{m.name}</span>
                      <button type="button" className="sts-x" disabled={busy} onClick={() => removeCharacter(set, m.id)}
                        title="このセットから外す">外す</button>
                    </div>
                  ))}
                </div>
                <div className="sts-add">
                  <select className="lora-select" value={adding[set.id] ?? ''} disabled={busy}
                    onChange={e => setAdding(prev => ({ ...prev, [set.id]: e.target.value }))}>
                    <option value="">キャラを選ぶ</option>
                    {outside.map(c => <option key={c.id} value={c.id}>{c.name}{c.style_set_id ? '(別のセットから移す)' : ''}</option>)}
                  </select>
                  <button type="button" className="lora-btn lora-btn--secondary" disabled={busy || !adding[set.id]}
                    onClick={() => addCharacter(set)}>セットに入れる</button>
                </div>
              </div>
            </div>
          </section>
        )
      })}

      <section className="sts-card sts-card--new">
        <div className="sts-media">
          {newImage ? <img className="ssp-main" src={newImage} alt="新しい絵柄セットの画像" /> : <div className="sts-noimg">画像を選んでください</div>}
          <label className="lora-btn lora-btn--secondary sts-file">
            画像を選ぶ
            <input type="file" accept="image/*" hidden disabled={busy}
              onChange={async e => { const f = e.target.files?.[0]; if (f) setNewImage(await readAsDataUrl(f)); e.target.value = '' }} />
          </label>
        </div>
        <div className="sts-body">
          <h2 className="sts-new-title">新しい絵柄セット</h2>
          <DraftFields draft={newDraft} disabled={busy} onChange={setNewDraft} />
          <div className="sts-actions">
            <button type="button" className="lora-btn" disabled={busy || !newImage || !newDraft.name.trim()} onClick={() => void create()}>
              作る
            </button>
          </div>
        </div>
      </section>
      </main>
    </div>
  )
}
