import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import './Home.css'

export default function Home() {
  const { logout } = useAuth()
  const navigate = useNavigate()

  return (
    <div className="home-root">
      <header className="home-header">
        <div className="home-header-left">
          <span className="home-brand">NovelAI</span>
          <span className="home-brand-sub">Image Generation</span>
        </div>
        <button type="button" className="home-logout-btn" onClick={logout}>
          ログアウト
        </button>
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
        </div>

        <div className="home-sdk-info">
          <h3>novelai-sdk 連携済み機能</h3>
          <ul>
            <li><span className="dot dot--green" />テキスト → 画像生成（ストリーミング対応）</li>
            <li><span className="dot dot--green" />Anlas 消費量の事前計算</li>
            <li><span className="dot dot--green" />画像メタデータの抽出・消去</li>
            <li><span className="dot dot--purple" />Character Reference（今後実装）</li>
            <li><span className="dot dot--purple" />ControlNet / Vibe Transfer（今後実装）</li>
          </ul>
        </div>
      </main>
    </div>
  )
}
