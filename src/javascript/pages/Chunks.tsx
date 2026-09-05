import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import './Chunks.css'

interface Situation {
  id: number
  name: string
}

interface ExclusiveGroup {
  id: number
  name: string
}

interface DbChunk {
  id: string
  container_id: string | null
  label: string
  expansion: string
  color: string | null
  is_category: boolean
  child_order: string[] | null
  situations: Situation[]
  exclusive_groups: ExclusiveGroup[]
}

const KEY_STORAGE = 'nai_encryption_key'

export default function Chunks() {
  const { token } = useAuth()
  const navigate = useNavigate()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [encryptionKey, setEncryptionKey] = useState<string | null>(() =>
    localStorage.getItem(KEY_STORAGE)
  )

  const [chunks, setChunks] = useState<DbChunk[]>([])
  const [situations, setSituations] = useState<Situation[]>([])
  const [newSituationName, setNewSituationName] = useState('')
  const [exclusiveGroups, setExclusiveGroups] = useState<ExclusiveGroup[]>([])
  const [newGroupName, setNewGroupName] = useState('')

  const [searchQuery, setSearchQuery] = useState('')
  const [situationFilter, setSituationFilter] = useState('all') // 'all' | 'untagged' | situationId
  const [categoryFilter, setCategoryFilter] = useState('all') // 'all' | containerId
  const [groupFilter, setGroupFilter] = useState('all') // 'all' | groupId

  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const loadChunks = async () => {
    const res = await fetch('/api/chunks/imported')
    const data = await res.json()
    if (res.ok) setChunks(data)
  }

  const loadSituations = async () => {
    const res = await fetch('/api/chunks/situations')
    const data = await res.json()
    if (res.ok) setSituations(data)
  }

  const loadExclusiveGroups = async () => {
    const res = await fetch('/api/chunks/exclusive-groups')
    const data = await res.json()
    if (res.ok) setExclusiveGroups(data)
  }

  useEffect(() => {
    loadChunks()
    loadSituations()
    loadExclusiveGroups()
  }, [])

  const computeKey = async () => {
    setError(null)
    setLoading(true)
    try {
      const res = await fetch('/api/chunks/encryption-key', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail ?? '鍵の計算に失敗しました')
      localStorage.setItem(KEY_STORAGE, data.encryption_key)
      setEncryptionKey(data.encryption_key)
      setPassword('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  const forgetKey = () => {
    localStorage.removeItem(KEY_STORAGE)
    setEncryptionKey(null)
  }

  const syncFromNovelAI = async () => {
    if (!token || !encryptionKey) return
    setError(null)
    setLoading(true)
    try {
      const res = await fetch(
        `/api/chunks/promptmacros?encryption_key=${encodeURIComponent(encryptionKey)}`,
        { headers: { Authorization: `Bearer ${token}` } }
      )
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail ?? 'チャンクの取得に失敗しました')
      await loadChunks()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  const addSituation = async () => {
    const name = newSituationName.trim()
    if (!name) return
    await fetch('/api/chunks/situations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    })
    setNewSituationName('')
    await loadSituations()
  }

  const deleteSituation = async (situationId: number) => {
    await fetch(`/api/chunks/situations/${situationId}`, { method: 'DELETE' })
    await Promise.all([loadSituations(), loadChunks()])
  }

  const setChunkSituationIds = async (chunkId: string, situationIds: number[]) => {
    await fetch(`/api/chunks/${chunkId}/situations`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ situation_ids: situationIds }),
    })
    setChunks(prev =>
      prev.map(c =>
        c.id === chunkId
          ? { ...c, situations: situations.filter(s => situationIds.includes(s.id)) }
          : c
      )
    )
  }

  const addSituationToChunk = (chunk: DbChunk, situationId: number) => {
    const ids = [...chunk.situations.map(s => s.id), situationId]
    void setChunkSituationIds(chunk.id, ids)
  }

  const removeSituationFromChunk = (chunk: DbChunk, situationId: number) => {
    const ids = chunk.situations.map(s => s.id).filter(id => id !== situationId)
    void setChunkSituationIds(chunk.id, ids)
  }

  const addExclusiveGroup = async () => {
    const name = newGroupName.trim()
    if (!name) return
    await fetch('/api/chunks/exclusive-groups', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    })
    setNewGroupName('')
    await loadExclusiveGroups()
  }

  const deleteExclusiveGroup = async (groupId: number) => {
    await fetch(`/api/chunks/exclusive-groups/${groupId}`, { method: 'DELETE' })
    await Promise.all([loadExclusiveGroups(), loadChunks()])
  }

  const setChunkGroupIds = async (chunkId: string, groupIds: number[]) => {
    await fetch(`/api/chunks/${chunkId}/exclusive-groups`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ group_ids: groupIds }),
    })
    setChunks(prev =>
      prev.map(c =>
        c.id === chunkId
          ? { ...c, exclusive_groups: exclusiveGroups.filter(g => groupIds.includes(g.id)) }
          : c
      )
    )
  }

  const addGroupToChunk = (chunk: DbChunk, groupId: number) => {
    const ids = [...chunk.exclusive_groups.map(g => g.id), groupId]
    void setChunkGroupIds(chunk.id, ids)
  }

  const removeGroupFromChunk = (chunk: DbChunk, groupId: number) => {
    const ids = chunk.exclusive_groups.map(g => g.id).filter(id => id !== groupId)
    void setChunkGroupIds(chunk.id, ids)
  }

  const leafChunks = chunks.filter(c => !c.is_category)
  const categories = chunks.filter(c => c.is_category)
  const categoryLabelById = new Map(categories.map(c => [c.id, c.label]))

  // NovelAI 側のデータは container_id が実際の所属フォルダと一致しないことがあるため、
  // カテゴリの child_order (親→子の一覧) を逆引きして本当の親を求める。
  const parentIdByChunkId = new Map<string, string>()
  for (const cat of categories) {
    for (const childId of cat.child_order ?? []) {
      parentIdByChunkId.set(childId, cat.id)
    }
  }

  const filteredChunks = leafChunks.filter(chunk => {
    if (searchQuery.trim()) {
      const q = searchQuery.trim().toLowerCase()
      if (!chunk.label.toLowerCase().includes(q) && !chunk.expansion.toLowerCase().includes(q)) {
        return false
      }
    }
    if (situationFilter === 'untagged') {
      if (chunk.situations.length > 0) return false
    } else if (situationFilter !== 'all') {
      if (!chunk.situations.some(s => s.id === Number(situationFilter))) return false
    }
    if (categoryFilter !== 'all' && parentIdByChunkId.get(chunk.id) !== categoryFilter) return false
    if (groupFilter !== 'all' && !chunk.exclusive_groups.some(g => g.id === Number(groupFilter))) {
      return false
    }
    return true
  })

  return (
    <div className="chunks-root">
      <div className="chunks-inner">
        <button type="button" className="chunks-back" onClick={() => navigate('/')}>
          ← ホーム
        </button>
        <h1>プロンプトチャンク</h1>
        <p className="chunks-intro">
          NovelAI 公式の「プロンプトチャンク」を読み取り専用で同期し、シチュエーションタグを付けて管理します。
          同期(取得)は任意操作で、通常はローカルDBの内容だけを表示します。
        </p>

        <section className="chunks-section">
          <h2>公式からの同期(任意)</h2>
          {encryptionKey ? (
            <div className="chunks-key-set">
              <span>✓ 復号鍵 設定済み</span>
              <button type="button" onClick={forgetKey}>削除</button>
              <button type="button" onClick={syncFromNovelAI} disabled={loading}>
                {loading ? '同期中...' : 'チャンクを同期'}
              </button>
            </div>
          ) : (
            <div className="chunks-key-form">
              <p>
                同期にはメール・パスワードから計算する復号鍵と、novelai.net から取得したセッショントークンでのログインが必要です。
                パスワード自体は保存されません。
              </p>
              <input
                type="email"
                placeholder="メールアドレス"
                value={email}
                onChange={e => setEmail(e.target.value)}
              />
              <input
                type="password"
                placeholder="パスワード"
                value={password}
                onChange={e => setPassword(e.target.value)}
              />
              <button type="button" onClick={computeKey} disabled={loading || !email || !password}>
                鍵を計算して保存
              </button>
            </div>
          )}
        </section>

        <section className="chunks-section">
          <h2>シチュエーションタグ</h2>
          <div className="chunks-situation-tags">
            {situations.map(s => (
              <span key={s.id} className="chunks-situation-chip">
                {s.name}
                <button type="button" onClick={() => deleteSituation(s.id)}>×</button>
              </span>
            ))}
          </div>
          <div className="chunks-situation-form">
            <input
              placeholder="新しいシチュエーション名"
              value={newSituationName}
              onChange={e => setNewSituationName(e.target.value)}
            />
            <button type="button" onClick={addSituation} disabled={!newSituationName.trim()}>
              追加
            </button>
          </div>
        </section>

        <section className="chunks-section">
          <h2>排他グループ（同時に使えない組み合わせ）</h2>
          <p className="chunks-section-note">
            同じグループに属するチャンクは、同時に選ぶべきではない組み合わせとして扱います（例:「髪の長さ」グループに長い髪/短い髪/坊主）。
          </p>
          <div className="chunks-situation-tags">
            {exclusiveGroups.map(g => (
              <span key={g.id} className="chunks-situation-chip chunks-group-chip">
                {g.name}
                <button type="button" onClick={() => deleteExclusiveGroup(g.id)}>×</button>
              </span>
            ))}
          </div>
          <div className="chunks-situation-form">
            <input
              placeholder="新しい排他グループ名"
              value={newGroupName}
              onChange={e => setNewGroupName(e.target.value)}
            />
            <button type="button" onClick={addExclusiveGroup} disabled={!newGroupName.trim()}>
              追加
            </button>
          </div>
        </section>

        {error && <p className="chunks-error">{error}</p>}

        <section className="chunks-section chunks-filter-bar">
          <input
            type="search"
            placeholder="ラベル・内容で検索..."
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
          />
          <select value={situationFilter} onChange={e => setSituationFilter(e.target.value)}>
            <option value="all">すべてのタグ</option>
            <option value="untagged">未タグのみ</option>
            {situations.map(s => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
          <select value={categoryFilter} onChange={e => setCategoryFilter(e.target.value)}>
            <option value="all">すべてのカテゴリ</option>
            {categories.map(c => (
              <option key={c.id} value={c.id}>{c.label}</option>
            ))}
          </select>
          <select value={groupFilter} onChange={e => setGroupFilter(e.target.value)}>
            <option value="all">すべての排他グループ</option>
            {exclusiveGroups.map(g => (
              <option key={g.id} value={g.id}>{g.name}</option>
            ))}
          </select>
          <span className="chunks-filter-count">
            {filteredChunks.length} / {leafChunks.length} 件
          </span>
        </section>

        <ul className="chunks-list">
          {filteredChunks.map(chunk => {
            const assignedIds = new Set(chunk.situations.map(s => s.id))
            const available = situations.filter(s => !assignedIds.has(s.id))
            const assignedGroupIds = new Set(chunk.exclusive_groups.map(g => g.id))
            const availableGroups = exclusiveGroups.filter(g => !assignedGroupIds.has(g.id))
            return (
              <li key={chunk.id} className="chunks-item">
                <span className="chunks-item-dot" style={{ background: chunk.color ?? '#888' }} />
                <span className="chunks-item-label">🏷️ {chunk.label}</span>
                {categoryLabelById.get(parentIdByChunkId.get(chunk.id) ?? '') && (
                  <span className="chunks-item-category">
                    📁 {categoryLabelById.get(parentIdByChunkId.get(chunk.id) ?? '')}
                  </span>
                )}
                <div className="chunks-item-expansion">{chunk.expansion}</div>

                <div className="chunks-item-situations">
                  {chunk.situations.map(s => (
                    <span key={s.id} className="chunks-situation-chip">
                      {s.name}
                      <button type="button" onClick={() => removeSituationFromChunk(chunk, s.id)}>×</button>
                    </span>
                  ))}
                  {available.length > 0 && (
                    <select
                      value=""
                      onChange={e => {
                        if (e.target.value) addSituationToChunk(chunk, Number(e.target.value))
                      }}
                    >
                      <option value="">+ タグを追加</option>
                      {available.map(s => (
                        <option key={s.id} value={s.id}>{s.name}</option>
                      ))}
                    </select>
                  )}
                </div>

                <div className="chunks-item-situations">
                  {chunk.exclusive_groups.map(g => (
                    <span key={g.id} className="chunks-situation-chip chunks-group-chip">
                      {g.name}
                      <button type="button" onClick={() => removeGroupFromChunk(chunk, g.id)}>×</button>
                    </span>
                  ))}
                  {availableGroups.length > 0 && (
                    <select
                      value=""
                      onChange={e => {
                        if (e.target.value) addGroupToChunk(chunk, Number(e.target.value))
                      }}
                    >
                      <option value="">+ 排他グループを追加</option>
                      {availableGroups.map(g => (
                        <option key={g.id} value={g.id}>{g.name}</option>
                      ))}
                    </select>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      </div>
    </div>
  )
}
