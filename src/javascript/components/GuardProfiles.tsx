import { useEffect, useState } from 'react'
import type { GuardCore, GuardProfile } from '../api'

interface Props {
  /** 選択中のプロファイル。null は基本のガードのみ。 */
  selectedId: number | null
  onSelect: (id: number | null) => void
  disabled?: boolean
  /** 保存・削除したときに、何を変えたかを知らせる */
  onSaved?: (what: string) => void
}

/**
 * データセット生成のガードプロファイルの選択と編集。
 * プロファイルでは、基本のガード(全年齢で止めるタグ)にタグとネガティブを上乗せできる。
 * 基本のガードそのものは、開発管理者のページ(コンテンツガード)か /api/content-guard で編集する。
 */
export default function GuardProfiles({ selectedId, onSelect, disabled, onSaved }: Props) {
  const [core, setCore] = useState<GuardCore | null>(null)
  const [profiles, setProfiles] = useState<GuardProfile[]>([])
  const [name, setName] = useState('')
  const [tags, setTags] = useState<string[]>([])
  const [negative, setNegative] = useState('')
  const [newTag, setNewTag] = useState('')
  const [showCore, setShowCore] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function load() {
    fetch('/api/lora-dataset/guards').then(r => (r.ok ? r.json() : [])).then(setProfiles).catch(() => {})
  }

  useEffect(() => {
    fetch('/api/lora-dataset/guards/core').then(r => (r.ok ? r.json() : null)).then(setCore).catch(() => {})
    load()
  }, [])

  // 選択を切り替えたら編集欄をそのプロファイルの内容にする
  useEffect(() => {
    const profile = profiles.find(p => p.id === selectedId)
    setName(profile?.name ?? '')
    setTags(profile?.blocked_tags ?? [])
    setNegative(profile?.negative_tags ?? '')
  }, [selectedId, profiles])

  const coreSet = new Set((core?.blocked_tags ?? []).map(t => t.toLowerCase()))

  function addTags() {
    const added = newTag.split(',').map(t => t.trim()).filter(Boolean)
    const next = [...tags]
    for (const tag of added) {
      if (coreSet.has(tag.toLowerCase()) || next.some(t => t.toLowerCase() === tag.toLowerCase())) continue
      next.push(tag)
    }
    setTags(next)
    setNewTag('')
  }

  async function save() {
    setError(null)
    try {
      const res = await fetch('/api/lora-dataset/guards', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim(), blocked_tags: tags, negative_tags: negative }),
      })
      if (!res.ok) throw new Error((await res.json()).detail ?? '保存に失敗しました')
      const saved: GuardProfile = await res.json()
      load()
      onSelect(saved.id)
      onSaved?.(`ガードプロファイル「${saved.name}」`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function remove() {
    if (selectedId == null) return
    const profile = profiles.find(p => p.id === selectedId)
    if (!window.confirm(`ガードプロファイル「${profile?.name ?? ''}」を削除しますか？`)) return
    await fetch(`/api/lora-dataset/guards/${selectedId}`, { method: 'DELETE' }).catch(() => {})
    onSelect(null)
    load()
    onSaved?.(`ガードプロファイル「${profile?.name ?? ''}」の削除`)
  }

  return (
    <fieldset className="lora-fieldset">
      <legend>ガード</legend>
      <label className="lora-label">使用するプロファイル
        <select
          className="lora-select"
          value={selectedId ?? ''}
          onChange={e => onSelect(e.target.value ? Number(e.target.value) : null)}
          disabled={disabled}
        >
          <option value="">基本のみ</option>
          {profiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </label>

      <button type="button" className="lora-btn lora-btn--secondary" onClick={() => setShowCore(!showCore)}>
        {showCore ? '基本のガードを隠す' : `基本のガード(${core?.blocked_tags.length ?? 0}件)を表示`}
      </button>
      {showCore && core && (
        <div className="chards-chips">
          {core.blocked_tags.map(t => <span key={t} className="chards-chip chards-chip--locked">{t}</span>)}
          <p className="lora-hint">常に付けるネガティブ: {core.negative_tags}</p>
          <p className="lora-hint">基本のガードは、開発管理者のページの「コンテンツガード」で編集できます。</p>
        </div>
      )}

      <span className="lora-label">追加でブロックするタグ</span>
      <div className="chards-chips">
        {tags.length === 0 && <span className="lora-hint">(なし)</span>}
        {tags.map(t => (
          <button
            key={t}
            type="button"
            className="chards-chip chards-chip--on"
            title="クリックで削除"
            onClick={() => setTags(tags.filter(x => x !== t))}
            disabled={disabled}
          >
            {t} ×
          </button>
        ))}
        <input
          className="chards-custom"
          placeholder="追加(カンマ区切り可)…"
          value={newTag}
          onChange={e => setNewTag(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && addTags()}
          disabled={disabled}
        />
      </div>

      <label className="lora-label">追加のネガティブ
        <textarea className="lora-textarea" rows={2} value={negative} onChange={e => setNegative(e.target.value)} disabled={disabled} />
      </label>

      <label className="lora-label">プロファイル名
        <input className="lora-input" value={name} onChange={e => setName(e.target.value)} placeholder="例: 学園もの" disabled={disabled} />
      </label>
      <div className="lora-actions">
        {selectedId != null && (
          <button type="button" className="lora-btn lora-btn--danger" onClick={() => void remove()} disabled={disabled}>削除</button>
        )}
        <button type="button" className="lora-btn lora-btn--primary" onClick={() => void save()} disabled={disabled || !name.trim()}>
          保存(同名は上書き)
        </button>
      </div>
      {error && <p className="lora-error" role="alert">{error}</p>}
    </fieldset>
  )
}
