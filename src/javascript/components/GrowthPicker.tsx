import { useEffect, useState } from 'react'

/**
 * この巻で使う成長段階(同じ人物の年齢ごとのキャラシート)。人物ごとに段階を選ぶか、巻の時期(年齢)から自動で選ぶ。
 * シーンに入れたキャラがその人物なら、選んだ段階の見た目で描く(名前・話し手はそのまま)。
 */

interface Stage {
  id: number
  name: string
  growth_label: string | null
  age: number | null
}

interface Person {
  person_id: number
  name: string
  stages: Stage[]
  chosen_id: number | null
}

interface Growth {
  age: number | null
  stages: Record<string, number>
  people: Person[]
}

interface Props {
  apiOrigin: string
  storyId: number
  busy?: boolean
}

export default function GrowthPicker({ apiOrigin, storyId, busy }: Props) {
  const [growth, setGrowth] = useState<Growth | null>(null)
  const [age, setAge] = useState('')
  const [stages, setStages] = useState<Record<string, number>>({})
  const [message, setMessage] = useState<string | null>(null)

  function apply(data: Growth) {
    setGrowth(data)
    setAge(data.age === null ? '' : String(data.age))
    setStages(data.stages)
  }

  useEffect(() => {
    setMessage(null)
    fetch(`${apiOrigin}/api/manga-v2/${storyId}/growth`)
      .then(r => (r.ok ? r.json() : null))
      .then((data: Growth | null) => data && apply(data))
      .catch(() => {})
  }, [apiOrigin, storyId])

  async function save() {
    const trimmed = age.trim()
    if (trimmed && Number.isNaN(Number(trimmed))) {
      setMessage('年齢は数字で入力してください')
      return
    }
    const res = await fetch(`${apiOrigin}/api/manga-v2/${storyId}/growth`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ age: trimmed ? Number(trimmed) : null, stages }),
    })
    if (!res.ok) {
      const data = await res.json().catch(() => null)
      setMessage(typeof data?.detail === 'string' ? data.detail : `保存できませんでした (HTTP ${res.status})`)
      return
    }
    apply(await res.json())
    setMessage('保存しました。これから描くコマから、選んだ段階の見た目で描きます')
  }

  if (growth && growth.people.length === 0) {
    return <p className="story-muted">この物語には、成長段階のある人物が出ていません(キャラシートの「成長段階」で作れます)。</p>
  }

  return (
    <div>
      <p className="story-muted">
        人物ごとに段階を選ぶと、シーンに入れたのが元のキャラでもほかの段階でも、選んだ段階の見た目で描きます。
        「自動」の人物は、巻の時期(年齢)以下で一番大きい段階を使います。
      </p>
      <label className="story-sheet-field">
        <span className="story-muted">この巻の時期(歳。空なら自動で選ばない)</span>
        <input type="text" inputMode="decimal" value={age} disabled={busy} onChange={e => setAge(e.target.value)} />
      </label>
      {growth?.people.map(person => (
        <label key={person.person_id} className="story-sheet-field">
          <span className="story-muted">{person.name}(いま使う段階: {person.stages.find(s => s.id === person.chosen_id)?.growth_label ?? 'シーンのキャラのまま'})</span>
          <select value={stages[String(person.person_id)] ?? ''} disabled={busy}
            onChange={e => {
              const next = { ...stages }
              if (e.target.value) next[String(person.person_id)] = Number(e.target.value)
              else delete next[String(person.person_id)]
              setStages(next)
            }}>
            <option value="">自動(巻の時期から)</option>
            {person.stages.map(s => (
              <option key={s.id} value={s.id}>{s.growth_label ?? s.name}{s.age !== null ? `(${s.age}歳)` : ''}</option>
            ))}
          </select>
        </label>
      ))}
      <div className="story-actions">
        <button type="button" disabled={busy} onClick={() => void save()}>成長段階を保存</button>
        {message && <span className="story-muted">{message}</span>}
      </div>
    </div>
  )
}
