import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fileUrl } from '../library'
import FullscreenViewer from './FullscreenViewer'
import './FaceSheet.css'
import './BodyBase.css'

/**
 * キャラシートの素体。服を着せる前の体(モデリングでいう素体)を、キャラの顔・髪・目のまま部位ごとに描いて見る。
 * 素体(肌の色・体格・身長・プロポーション など)はキャラとは別に一覧で持ち、ここで作る・直す。キャラにはどれを当てるか選ぶ。
 * 素体スーツ(全年齢)はどのキャラにも、裸の素体は成人キャラだけに当てられる(サーバーが判定する)。
 */

type Field = 'sex' | 'skin_tone' | 'build' | 'height' | 'proportion' | 'chest' | 'muscle'

interface Choice { value: string; label: string; tags: string }

interface Options {
  /** 設定の項目(プルダウン)。選択肢は DB にあり、素体の設定のページで編集する */
  fields: { key: Field; label: string }[]
  options: Record<Field, Choice[]>
  kinds: { value: 'suit' | 'nude'; label: string }[]
  groups: { id: string; label: string }[]
}

interface Base extends Record<Field, string> {
  id: number
  name: string
  kind: 'suit' | 'nude'
  extra_tags: string
  negative_tags: string
  notes: string
  used_by?: number
}

interface Part {
  key: string
  label: string
  group: string
  check: string
  available: boolean
  path: string | null
  updated: number | null
}

interface CharacterBody {
  base: Base | null
  body_tags: string
  nude_allowed: boolean
  nude_reason: string | null
  /** 未成年の手がかりがあるキャラ。素体スーツを控えめに描く(胸・曲線の設定を使わない) */
  modest: boolean
  parts: Part[]
}

interface Props {
  characterId: number
  characterName: string
  token: string | null
  rootName: string
  disabled?: boolean
}

const EMPTY: Omit<Base, 'id'> = {
  name: '', kind: 'suit', sex: 'female', skin_tone: '', build: '', height: '', proportion: '', chest: '', muscle: '',
  extra_tags: '', negative_tags: '', notes: '',
}

async function detail(res: Response): Promise<string> {
  const data = await res.json().catch(() => null)
  return typeof data?.detail === 'string' ? data.detail : `HTTP ${res.status}`
}

export default function BodyBase({ characterId, characterName, token, rootName, disabled }: Props) {
  const navigate = useNavigate()
  const [options, setOptions] = useState<Options | null>(null)
  const [bases, setBases] = useState<Base[]>([])
  const [body, setBody] = useState<CharacterBody | null>(null)
  // 編集中の素体(id が無ければ新しく作る)
  const [editing, setEditing] = useState<(Omit<Base, 'id'> & { id?: number }) | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [useReference, setUseReference] = useState(false)
  const [running, setRunning] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [zoomKey, setZoomKey] = useState<string | null>(null)

  function loadBases() {
    fetch('/api/lora-dataset/body-bases').then(r => (r.ok ? r.json() : [])).then(setBases).catch(() => setBases([]))
  }
  function loadBody() {
    fetch(`/api/lora-dataset/character/${characterId}/body-base?root_name=${encodeURIComponent(rootName)}`)
      .then(r => (r.ok ? r.json() : null))
      .then(setBody)
      .catch(() => setBody(null))
  }
  useEffect(() => {
    fetch('/api/lora-dataset/body-bases/options').then(r => (r.ok ? r.json() : null)).then(setOptions).catch(() => {})
    loadBases()
  }, [])
  useEffect(loadBody, [characterId, rootName])
  useEffect(() => {
    setZoomKey(null)
    setPicked(new Set())
    setStatus(null)
    setError(null)
  }, [characterId])

  async function assign(baseId: number | null) {
    setError(null)
    const res = await fetch(`/api/lora-dataset/character/${characterId}/body-base?root_name=${encodeURIComponent(rootName)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ body_base_id: baseId }),
    })
    if (!res.ok) {
      setError(await detail(res))
      return
    }
    setBody(await res.json())
    setPicked(new Set())
    loadBases()
  }

  async function save() {
    if (!editing) return
    setError(null)
    const { id, used_by: _usedBy, ...fields } = editing as Base
    const res = await fetch(id ? `/api/lora-dataset/body-bases/${id}` : '/api/lora-dataset/body-bases', {
      method: id ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fields),
    })
    if (!res.ok) {
      setError(await detail(res))
      return
    }
    const saved: Base = await res.json()
    setEditing(null)
    loadBases()
    // 作った素体は、まだ素体の無いキャラにそのまま当てる
    if (!id && !body?.base && (saved.kind === 'suit' || body?.nude_allowed)) await assign(saved.id)
    else loadBody()
  }

  async function remove(base: Base) {
    const note = base.used_by ? `(${base.used_by} 人のキャラが使っています。素体なしに戻ります)` : ''
    if (!window.confirm(`素体「${base.name}」を消しますか?${note} 描いた絵は残ります。`)) return
    const res = await fetch(`/api/lora-dataset/body-bases/${base.id}`, { method: 'DELETE' })
    if (!res.ok) setError(await detail(res))
    loadBases()
    loadBody()
  }

  function toggle(key: string) {
    const next = new Set(picked)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    setPicked(next)
  }

  function pickGroup(group: string, onlyMissing: boolean) {
    const next = new Set(picked)
    for (const p of body?.parts ?? []) if (p.group === group && p.available && (!onlyMissing || !p.path)) next.add(p.key)
    setPicked(next)
  }

  async function generate() {
    if (!picked.size) return
    setRunning(true)
    setError(null)
    setStatus(`${picked.size} 枚を描いています…`)
    try {
      const res = await fetch(`/api/lora-dataset/character/${characterId}/body-base`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ keys: [...picked], root_name: rootName, use_reference: useReference }),
      })
      if (!res.ok || !res.body) {
        setStatus(`描けませんでした: ${await detail(res)}`)
        return
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let failed = 0
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const event = JSON.parse(line.slice(6)) as { current?: number; total?: number; label?: string; status?: string }
          if (event.status === 'error') failed++
          if (event.current && event.total) {
            setStatus(`${event.current}/${event.total}: ${event.label}${event.status === 'error' ? '(失敗)' : ''}`)
            if (event.status === 'ok') loadBody()
          }
        }
      }
      setStatus(failed ? `描き終わりました(失敗 ${failed} 枚)` : '描き終わりました')
      setPicked(new Set())
    } catch (e) {
      setStatus(e instanceof Error ? e.message : String(e))
    } finally {
      setRunning(false)
      loadBody()
    }
  }

  const busy = disabled || running
  const kindLabel = (kind: string) => options?.kinds.find(k => k.value === kind)?.label ?? kind
  const groups = options?.groups ?? []
  const drawn = groups.flatMap(g => (body?.parts ?? []).filter(p => p.group === g.id && p.path))
  const zoomIndex = drawn.findIndex(p => p.key === zoomKey)
  const zoom = zoomIndex >= 0 ? drawn[zoomIndex] : null

  return (
    <section className="fsh bdb">
      <h3>{characterName}の素体</h3>
      <p className="lora-hint">
        服を着せる前の体を、キャラの顔・髪・目のまま部位ごとに描きます。体型と肌の色は、キャラシートの容姿の代わりに素体の設定を使います。
        素体スーツはどのキャラにも、裸の素体(デッサン人形の程度)は成人キャラにだけ当てられます。
      </p>

      <div className="bdb-assign">
        <label className="lora-label" htmlFor="bdb-assign">このキャラに当てる素体</label>
        <select
          id="bdb-assign"
          className="lora-select"
          value={body?.base?.id ?? ''}
          disabled={busy}
          onChange={e => void assign(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">素体なし</option>
          {bases.map(b => (
            <option key={b.id} value={b.id} disabled={b.kind === 'nude' && !body?.nude_allowed}>
              {b.name}({kindLabel(b.kind)})
            </option>
          ))}
        </select>
        {body?.base && <p className="lora-hint">体のタグ: {body.body_tags || '(指定なし)'}</p>}
        {body && !body.nude_allowed && <p className="lora-hint">裸の素体は当てられません: {body.nude_reason}</p>}
        {body?.modest && (
          <p className="lora-hint">未成年の手がかりがあるキャラなので、素体スーツは胸・体の曲線の設定を使わず、控えめに描きます。</p>
        )}
      </div>

      <div className="bdb-list">
        <div className="fsh-group-head">
          <h4>素体の一覧</h4>
          <button type="button" className="lora-btn lora-btn--secondary" disabled={busy} onClick={() => setEditing({ ...EMPTY })}>
            新しい素体
          </button>
          <button type="button" className="lora-btn lora-btn--secondary" onClick={() => navigate('/body-base-settings')}
            title="性別・肌の色・体格 などのプルダウンの選択肢と、そのタグを編集します">
            選択肢を編集
          </button>
        </div>
        {bases.length === 0 && <p className="lora-hint">まだ素体がありません。</p>}
        <ul>
          {bases.map(b => (
            <li key={b.id} className={b.id === body?.base?.id ? 'bdb-item bdb-item--on' : 'bdb-item'}>
              <span className="bdb-item-name">{b.name}</span>
              <span className={b.kind === 'nude' ? 'bdb-kind bdb-kind--nude' : 'bdb-kind'}>{kindLabel(b.kind)}</span>
              <span className="lora-hint">{b.used_by ?? 0} 人</span>
              <button type="button" className="lora-btn lora-btn--secondary" disabled={busy} onClick={() => setEditing({ ...b })}>編集</button>
              <button type="button" className="lora-btn lora-btn--danger" disabled={busy} onClick={() => void remove(b)}>削除</button>
            </li>
          ))}
        </ul>
      </div>

      {editing && options && (
        <div className="bdb-editor">
          <h4>{editing.id ? `「${editing.name}」を編集` : '新しい素体'}</h4>
          <label className="lora-label">名前
            <input className="lora-input" value={editing.name} onChange={e => setEditing({ ...editing, name: e.target.value })} />
          </label>
          <div className="bdb-kinds">
            {options.kinds.map(k => (
              <label key={k.value} className="fsh-label">
                <input type="radio" name="bdb-kind" checked={editing.kind === k.value}
                  onChange={() => setEditing({ ...editing, kind: k.value })} />
                {k.label}
              </label>
            ))}
          </div>
          <div className="bdb-fields">
            {options.fields.map(f => (
              <label key={f.key} className="lora-label">{f.label}
                <select className="lora-select" value={editing[f.key]} onChange={e => setEditing({ ...editing, [f.key]: e.target.value })}>
                  {!options.options[f.key].some(c => c.value === '') && <option value="">指定しない</option>}
                  {options.options[f.key].map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
                </select>
              </label>
            ))}
          </div>
          <label className="lora-label">足すタグ(ほくろ・傷あと・日焼けあと など。性的なタグは入れられません)
            <textarea className="lora-textarea" rows={2} value={editing.extra_tags}
              onChange={e => setEditing({ ...editing, extra_tags: e.target.value })} />
          </label>
          <label className="lora-label">ネガティブ
            <textarea className="lora-textarea" rows={2} value={editing.negative_tags}
              onChange={e => setEditing({ ...editing, negative_tags: e.target.value })} />
          </label>
          <label className="lora-label">メモ
            <textarea className="lora-textarea" rows={2} value={editing.notes} onChange={e => setEditing({ ...editing, notes: e.target.value })} />
          </label>
          <div className="fsh-actions">
            <button type="button" className="lora-btn" disabled={busy || !editing.name.trim()} onClick={() => void save()}>保存</button>
            <button type="button" className="lora-btn lora-btn--secondary" onClick={() => setEditing(null)}>やめる</button>
          </div>
        </div>
      )}

      {error && <p className="lora-error" role="alert">{error}</p>}

      {body?.base && groups.map(group => (
        <div key={group.id}>
          <div className="fsh-group-head">
            <h4>{group.label}</h4>
            <button type="button" className="lora-btn lora-btn--secondary" disabled={busy} onClick={() => pickGroup(group.id, false)}>全部選ぶ</button>
            <button type="button" className="lora-btn lora-btn--secondary" disabled={busy} onClick={() => pickGroup(group.id, true)}>まだの部位を選ぶ</button>
          </div>
          <div className="fsh-grid">
            {body.parts.filter(p => p.group === group.id).map(p => (
              <div key={p.key} className={picked.has(p.key) ? 'fsh-cell fsh-cell--on' : 'fsh-cell'} title={p.check}>
                {p.path ? (
                  <button type="button" className="fsh-thumb" onClick={() => setZoomKey(p.key)} title="拡大">
                    <img src={`${fileUrl(p.path)}&v=${p.updated ?? 0}`} alt={p.label} loading="lazy" />
                  </button>
                ) : (
                  <div className="fsh-thumb fsh-thumb--none">{p.available ? '未' : '全身図で確認'}</div>
                )}
                <label className="fsh-label">
                  <input type="checkbox" checked={picked.has(p.key)} disabled={busy || !p.available} onChange={() => toggle(p.key)} />
                  {p.label}
                </label>
                <details className="bdb-check">
                  <summary>確かめる点</summary>
                  <p>{p.check}</p>
                </details>
              </div>
            ))}
          </div>
        </div>
      ))}

      {body?.base && (
        <div className="fsh-actions">
          <label className="fsh-label">
            <input type="checkbox" checked={useReference} disabled={busy} onChange={e => setUseReference(e.target.checked)} />
            参照画像も使う(V4.5・顔を寄せる。体型も寄るので、素体の体型を確かめるときは外す)
          </label>
          <button type="button" className="lora-btn" disabled={busy || picked.size === 0} onClick={() => void generate()}>
            {running ? '描いています…' : `選んだ ${picked.size} 枚を描く`}
          </button>
          {status && <span className="lora-hint">{status}</span>}
        </div>
      )}

      {zoom?.path && (
        <FullscreenViewer
          src={`${fileUrl(zoom.path)}&v=${zoom.updated ?? 0}`}
          alt={zoom.label}
          counter={`${zoomIndex + 1} / ${drawn.length} ・ ${zoom.label}`}
          onPrev={zoomIndex > 0 ? () => setZoomKey(drawn[zoomIndex - 1].key) : undefined}
          onNext={zoomIndex < drawn.length - 1 ? () => setZoomKey(drawn[zoomIndex + 1].key) : undefined}
          onClose={() => setZoomKey(null)}
        />
      )}
    </section>
  )
}
