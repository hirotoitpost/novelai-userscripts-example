import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import './DbStatus.css'

interface Situation {
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
  synced_at: string
  situations: Situation[]
}

function countLeaves(node: DbChunk, byId: Map<string, DbChunk>): number {
  if (!node.is_category) return 1
  return (node.child_order ?? []).reduce((sum, childId) => {
    const child = byId.get(childId)
    return child ? sum + countLeaves(child, byId) : sum
  }, 0)
}

function countTagged(node: DbChunk, byId: Map<string, DbChunk>): number {
  if (!node.is_category) return node.situations.length > 0 ? 1 : 0
  return (node.child_order ?? []).reduce((sum, childId) => {
    const child = byId.get(childId)
    return child ? sum + countTagged(child, byId) : sum
  }, 0)
}

function CategoryNode({
  node,
  byId,
  depth,
}: {
  node: DbChunk
  byId: Map<string, DbChunk>
  depth: number
}) {
  const total = countLeaves(node, byId)
  const tagged = countTagged(node, byId)
  return (
    <details className="dbstatus-category" open={depth === 0}>
      <summary>
        📁 {node.label}
        <span className="dbstatus-count">
          {total}件（タグ付け {tagged}/{total}）
        </span>
      </summary>
      <div className="dbstatus-children">
        {(node.child_order ?? []).map(childId => {
          const child = byId.get(childId)
          if (!child) return null
          return child.is_category ? (
            <CategoryNode key={childId} node={child} byId={byId} depth={depth + 1} />
          ) : (
            <LeafNode key={childId} node={child} />
          )
        })}
      </div>
    </details>
  )
}

function LeafNode({ node }: { node: DbChunk }) {
  return (
    <div className="dbstatus-leaf">
      <span className="dbstatus-leaf-dot" style={{ background: node.color ?? '#888' }} />
      <span className="dbstatus-leaf-label">{node.label}</span>
      {node.situations.length > 0 ? (
        <span className="dbstatus-leaf-tags">
          {node.situations.map(s => s.name).join(' / ')}
        </span>
      ) : (
        <span className="dbstatus-leaf-untagged">未タグ</span>
      )}
    </div>
  )
}

export default function DbStatus() {
  const navigate = useNavigate()
  const [chunks, setChunks] = useState<DbChunk[]>([])
  const [situations, setSituations] = useState<Situation[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      fetch('/api/chunks/imported').then(r => r.json()),
      fetch('/api/chunks/situations').then(r => r.json()),
    ])
      .then(([c, s]) => {
        setChunks(c)
        setSituations(s)
      })
      .finally(() => setLoading(false))
  }, [])

  const byId = useMemo(() => new Map(chunks.map(c => [c.id, c])), [chunks])

  const { roots, orphanLeaves } = useMemo(() => {
    const referencedAsChild = new Set<string>()
    for (const c of chunks) {
      if (c.child_order) for (const childId of c.child_order) referencedAsChild.add(childId)
    }
    return {
      roots: chunks.filter(c => c.is_category && !referencedAsChild.has(c.id)),
      orphanLeaves: chunks.filter(c => !c.is_category && !referencedAsChild.has(c.id)),
    }
  }, [chunks])

  const leaves = useMemo(() => chunks.filter(c => !c.is_category), [chunks])
  const categoryCount = chunks.length - leaves.length
  const taggedCount = leaves.filter(c => c.situations.length > 0).length
  const untaggedCount = leaves.length - taggedCount

  const lastSyncedAt = useMemo(() => {
    if (chunks.length === 0) return null
    return chunks.reduce((latest, c) => (c.synced_at > latest ? c.synced_at : latest), chunks[0].synced_at)
  }, [chunks])

  const situationCounts = useMemo(
    () =>
      situations
        .map(s => ({
          ...s,
          count: leaves.filter(c => c.situations.some(cs => cs.id === s.id)).length,
        }))
        .sort((a, b) => b.count - a.count),
    [situations, leaves]
  )

  if (loading) {
    return (
      <div className="dbstatus-root">
        <div className="dbstatus-inner">読み込み中...</div>
      </div>
    )
  }

  return (
    <div className="dbstatus-root">
      <div className="dbstatus-inner">
        <button type="button" className="dbstatus-back" onClick={() => navigate('/')}>
          ← ホーム
        </button>
        <h1>DB状態</h1>
        <p className="dbstatus-intro">
          ローカルDB(<code>data/app.db</code>)に保存されているプロンプトチャンクの状態です。
          NovelAIへは問い合わせません。
        </p>

        <div className="dbstatus-stats">
          <div className="dbstatus-stat">
            <span className="dbstatus-stat-value">{leaves.length}</span>
            <span className="dbstatus-stat-label">チャンク</span>
          </div>
          <div className="dbstatus-stat">
            <span className="dbstatus-stat-value">{categoryCount}</span>
            <span className="dbstatus-stat-label">カテゴリ</span>
          </div>
          <div className="dbstatus-stat">
            <span className="dbstatus-stat-value">{taggedCount}</span>
            <span className="dbstatus-stat-label">タグ付け済み</span>
          </div>
          <div className="dbstatus-stat dbstatus-stat--warn">
            <span className="dbstatus-stat-value">{untaggedCount}</span>
            <span className="dbstatus-stat-label">未タグ</span>
          </div>
          <div className="dbstatus-stat">
            <span className="dbstatus-stat-value">{situations.length}</span>
            <span className="dbstatus-stat-label">シチュエーション種別</span>
          </div>
        </div>

        {lastSyncedAt && (
          <p className="dbstatus-synced">
            最終同期: {new Date(lastSyncedAt).toLocaleString('ja-JP')}
          </p>
        )}

        {situationCounts.length > 0 && (
          <section className="dbstatus-section">
            <h2>シチュエーション別件数</h2>
            <ul className="dbstatus-situation-list">
              {situationCounts.map(s => (
                <li key={s.id}>
                  <span className="dbstatus-situation-name">{s.name}</span>
                  <div className="dbstatus-situation-bar-track">
                    <div
                      className="dbstatus-situation-bar-fill"
                      style={{ width: leaves.length ? `${(s.count / leaves.length) * 100}%` : '0%' }}
                    />
                  </div>
                  <span className="dbstatus-situation-count">{s.count}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="dbstatus-section">
          <h2>カテゴリ構成</h2>
          {chunks.length === 0 ? (
            <p className="dbstatus-empty">
              まだ何もインポートされていません。「プロンプトチャンク」ページから同期してください。
            </p>
          ) : (
            <div className="dbstatus-tree">
              {roots.map(root => (
                <CategoryNode key={root.id} node={root} byId={byId} depth={0} />
              ))}
              {orphanLeaves.length > 0 && (
                <details className="dbstatus-category" open={false}>
                  <summary>
                    ⚠️ 未分類
                    <span className="dbstatus-count">{orphanLeaves.length}件</span>
                  </summary>
                  <div className="dbstatus-children">
                    {orphanLeaves.map(c => (
                      <LeafNode key={c.id} node={c} />
                    ))}
                  </div>
                </details>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
