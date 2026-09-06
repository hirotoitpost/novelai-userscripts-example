import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { apiFetch, SubscriptionResponse } from '../api'
import './Home.css'

const TIER_NAMES: Record<number, string> = { 0: 'Free', 1: 'Tablet', 2: 'Scroll', 3: 'Opus' }
const TIER_CLASSES: Record<number, string> = {
  0: 'home-tier--free',
  1: 'home-tier--tablet',
  2: 'home-tier--scroll',
  3: 'home-tier--opus',
}

export default function Home() {
  const { token, logout } = useAuth()
  const navigate = useNavigate()

  const [sub, setSub] = useState<SubscriptionResponse | null>(null)

  useEffect(() => {
    if (!token) return
    apiFetch<SubscriptionResponse>(token, '/api/user/subscription')
      .then(setSub)
      .catch(() => {/* サイレント失敗 */})
  }, [token])

  return (
    <div className="home-root">
      <header className="home-header">
        <div className="home-header-left">
          <span className="home-brand">NovelAI</span>
          <span className="home-brand-sub">Image Generation</span>
        </div>
        <div className="home-header-right">
          {sub && (
            <div className="home-sub-info">
              <span className={`home-tier ${TIER_CLASSES[sub.tier] ?? ''}`}>
                {TIER_NAMES[sub.tier] ?? `Tier ${sub.tier}`}
              </span>
              {sub.perks.unlimitedImageGeneration ? (
                <span className="home-sub-badge home-sub-badge--green">画像: 無制限</span>
              ) : (
                <span className="home-sub-badge">画像: あり</span>
              )}
              <span className="home-sub-badge" title="テキスト学習ステップ残量">
                Steps: {(
                  sub.trainingStepsLeft.fixedTrainingStepsLeft +
                  sub.trainingStepsLeft.purchasedTrainingSteps
                ).toLocaleString()}
              </span>
            </div>
          )}
          <button type="button" className="home-logout-btn" onClick={logout}>
            ログアウト
          </button>
        </div>
      </header>

      <main className="home-main">
        <section className="home-welcome">
          <h1>ようこそ</h1>
          <p>NovelAI に正常にログインしました。</p>
        </section>

        <div className="home-cards">
          <div
            className="home-card home-card--available home-card--link"
            onClick={() => navigate('/generate')}
            role="button"
            tabIndex={0}
            onKeyDown={e => e.key === 'Enter' && navigate('/generate')}
          >
            <div className="home-card-icon">🎨</div>
            <h2>画像生成</h2>
            <p>テキストプロンプトから高品質な画像を生成します。</p>
            <span className="home-card-badge home-card-badge--open">開く →</span>
          </div>

          <div className="home-card home-card--available">
            <div className="home-card-icon">🔄</div>
            <h2>Image-to-Image</h2>
            <p>既存の画像を元に新しい画像へ変換します。</p>
            <span className="home-card-badge home-card-badge--soon">近日実装</span>
          </div>

          <div
            className="home-card home-card--available home-card--link"
            onClick={() => navigate('/metadata')}
            role="button"
            tabIndex={0}
            onKeyDown={e => e.key === 'Enter' && navigate('/metadata')}
          >
            <div className="home-card-icon">📊</div>
            <h2>メタデータ</h2>
            <p>NovelAI 生成画像のメタデータを抽出・消去します。</p>
            <span className="home-card-badge home-card-badge--open">開く →</span>
          </div>

          <div
            className="home-card home-card--available home-card--link"
            onClick={() => navigate('/llm')}
            role="button"
            tabIndex={0}
            onKeyDown={e => e.key === 'Enter' && navigate('/llm')}
          >
            <div className="home-card-icon">🤖</div>
            <h2>AI アシスタント</h2>
            <p>LLM を活用したプロンプト生成・キャラ設定・物語ドラフト・リバースプロンプトを提供します。</p>
            <span className="home-card-badge home-card-badge--open">開く →</span>
          </div>

          <div
            className="home-card home-card--available home-card--link"
            onClick={() => navigate('/batch')}
            role="button"
            tabIndex={0}
            onKeyDown={e => e.key === 'Enter' && navigate('/batch')}
          >
            <div className="home-card-icon">📂</div>
            <h2>バッチ整理</h2>
            <p>フォルダ内の NAI 画像をプロンプトの類似性と作成日付でグループ分けして自動整理します。</p>
            <span className="home-card-badge home-card-badge--open">開く →</span>
          </div>

          <div
            className="home-card home-card--available home-card--link"
            onClick={() => navigate('/lora-dataset')}
            role="button"
            tabIndex={0}
            onKeyDown={e => e.key === 'Enter' && navigate('/lora-dataset')}
          >
            <div className="home-card-icon">🧬</div>
            <h2>LoRA データセット生成</h2>
            <p>トリガーワードやAI設定を指定して、顔/上半身/全身の学習用画像セットを自動生成します。</p>
            <span className="home-card-badge home-card-badge--open">開く →</span>
          </div>

          <div
            className="home-card home-card--available home-card--link"
            onClick={() => navigate('/chunks')}
            role="button"
            tabIndex={0}
            onKeyDown={e => e.key === 'Enter' && navigate('/chunks')}
          >
            <div className="home-card-icon">🧩</div>
            <h2>プロンプトチャンク</h2>
            <p>NovelAI 公式のプロンプトチャンクを読み取り専用で同期します（セッショントークンが必要）。</p>
            <span className="home-card-badge home-card-badge--open">開く →</span>
          </div>

          <div
            className="home-card home-card--available home-card--link"
            onClick={() => navigate('/db-status')}
            role="button"
            tabIndex={0}
            onKeyDown={e => e.key === 'Enter' && navigate('/db-status')}
          >
            <div className="home-card-icon">📊</div>
            <h2>DB状態</h2>
            <p>インポート済みチャンクの件数・カテゴリ構成・タグ付け状況を確認します。</p>
            <span className="home-card-badge home-card-badge--open">開く →</span>
          </div>

          <div
            className="home-card home-card--available home-card--link"
            onClick={() => navigate('/select')}
            role="button"
            tabIndex={0}
            onKeyDown={e => e.key === 'Enter' && navigate('/select')}
          >
            <div className="home-card-icon">🎲</div>
            <h2>ワード選択</h2>
            <p>シナリオ・ランダム・類似・プリセットの4通りでチャンクを選び、プロンプトに結合します。</p>
            <span className="home-card-badge home-card-badge--open">開く →</span>
          </div>

          <div
            className="home-card home-card--available home-card--link"
            onClick={() => navigate('/story')}
            role="button"
            tabIndex={0}
            onKeyDown={e => e.key === 'Enter' && navigate('/story')}
          >
            <div className="home-card-icon">📖</div>
            <h2>物語 → 漫画</h2>
            <p>前提からOllamaでドラフトを作り、NovelAI公式(Kayra)で本文を執筆、V5で挿絵を生成して漫画にまとめます。</p>
            <span className="home-card-badge home-card-badge--open">開く →</span>
          </div>
        </div>

        <div className="home-sdk-info">
          <h3>novelai-sdk 連携済み機能</h3>
          <ul>
            <li><span className="dot dot--green" />テキスト → 画像生成（ストリーミング対応）</li>
            <li><span className="dot dot--green" />Anlas 消費量の事前計算</li>
            <li><span className="dot dot--green" />画像メタデータの抽出・消去</li>
            <li><span className="dot dot--green" />LLM によるプロンプト強化・キャラ設定・リバースプロンプト（vLLM）</li>
            <li><span className="dot dot--purple" />Character Reference（今後実装）</li>
            <li><span className="dot dot--purple" />ControlNet / Vibe Transfer（今後実装）</li>
          </ul>
        </div>
      </main>
    </div>
  )
}
