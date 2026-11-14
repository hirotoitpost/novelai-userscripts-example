import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiFetch } from '../api'
import { useAuth } from '../context/AuthContext'
import { useLocalStorage } from '../hooks/useLocalStorage'
import HelpMarkdown from './HelpMarkdown'
import { MicButton, SpeakButton, useAutoRead } from './VoiceControls'
import './HelpChat.css'

/**
 * ヘルプのチャット(ヘルプのページの下。/api/help/chat)。ヘルプの記事だけを元に答える。
 * 答える文章モデルは、聞くたびに選べる(既定は管理ページの「既定値・プリセット・ガード」→「ヘルプのチャット」)。
 */

type Engine = 'default' | 'excerpt' | 'ollama' | 'novelai' | 'auto'

interface Turn {
  role: 'user' | 'assistant'
  content: string
  sources?: { slug: string; title: string }[]
  engine?: string
}

interface Engines {
  default: string
  ollama_ready: boolean
  novelai_ready: boolean
}

const ENGINE_LABEL: Record<string, string> = { excerpt: '記事の抜き出し', ollama: 'Ollama', novelai: 'NovelAI', auto: '自動' }

export default function HelpChat({ current }: { current?: string }) {
  const { token } = useAuth()
  const [engine, setEngine] = useLocalStorage<Engine>('nai_help_engine', 'default')
  const [engines, setEngines] = useState<Engines | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const end = useRef<HTMLDivElement>(null)
  const [fresh, setFresh] = useState<string | null>(null)
  useAutoRead(fresh)

  useEffect(() => {
    if (!token) return
    apiFetch<Engines>(token, '/api/help/chat/engines').then(setEngines).catch(() => setEngines(null))
  }, [token])
  useEffect(() => { end.current?.scrollIntoView({ block: 'nearest' }) }, [turns, busy])

  const ask = async (spoken?: string) => {
    const text = (spoken ?? question).trim()
    if (!text || !token || busy) return
    setBusy(true)
    setError(null)
    setQuestion('')
    const history = turns.map(({ role, content }) => ({ role, content }))
    setTurns(list => [...list, { role: 'user', content: text }])
    try {
      const res = await apiFetch<{ answer: string; engine: string; sources: Turn['sources'] }>(
        token, '/api/help/chat', { question: text, history, current, engine },
      )
      setTurns(list => [...list, { role: 'assistant', content: res.answer, sources: res.sources, engine: res.engine }])
      setFresh(res.answer)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setQuestion(text)
      setTurns(list => list.slice(0, -1))
    } finally {
      setBusy(false)
    }
  }

  const defaultLabel = engines ? ENGINE_LABEL[engines.default] ?? engines.default : ''
  return (
    <section className="help-chat" aria-label="ヘルプに質問する">
      <div className="help-chat-head">
        <h2>ヘルプに質問する</h2>
        <label>
          答え方
          <select value={engine} onChange={e => setEngine(e.target.value as Engine)} disabled={busy}>
            <option value="default">既定{defaultLabel ? `(${defaultLabel})` : ''}</option>
            <option value="excerpt">記事の抜き出し(作り話をしない・費用なし)</option>
            <option value="ollama">Ollama(この PC。費用なし){engines && !engines.ollama_ready ? ' ※未設定' : ''}</option>
            <option value="novelai">NovelAI(文章生成の残量を使う){engines && !engines.novelai_ready ? ' ※トークンなし' : ''}</option>
          </select>
        </label>
      </div>
      <p className="help-chat-hint">
        ヘルプの記事だけを元に答えます。「記事の抜き出し」は記事をそのまま出します。Ollama・NovelAI は文章で答えますが、
        記事に無いことを言うことがあります(記事に無い名前には印が付きます)。手順は記事でも確かめてください。
      </p>

      {turns.length > 0 && (
        <div className="help-chat-log">
          {turns.map((turn, index) => (
            <div key={index} className={`help-chat-turn help-chat-turn--${turn.role}`}>
              {turn.role === 'user' ? <p>{turn.content}</p> : <HelpMarkdown source={turn.content} />}
              {turn.sources && turn.sources.length > 0 && (
                <div className="help-chat-sources">
                  <SpeakButton text={turn.content} />
                  参考にした記事: {turn.sources.map((s, i) => (
                    <span key={s.slug}>{i > 0 && '、'}<Link to={`/help/${s.slug}`}>{s.title}</Link></span>
                  ))}
                  {turn.engine && <span className="help-chat-engine">({ENGINE_LABEL[turn.engine] ?? turn.engine})</span>}
                </div>
              )}
            </div>
          ))}
          {busy && <div className="help-chat-turn help-chat-turn--assistant help-chat-wait">考えています…</div>}
          <div ref={end} />
        </div>
      )}
      {error && <p className="help-chat-error" role="alert">{error}</p>}

      <form className="help-chat-form" onSubmit={e => { e.preventDefault(); void ask() }}>
        <textarea value={question} rows={2} maxLength={1000} placeholder="例: バックアップのディスクを足すには?"
          aria-label="質問" disabled={busy}
          onChange={e => setQuestion(e.target.value)}
          onKeyDown={e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); void ask() } }} />
        <div className="help-chat-actions">
          <button type="submit" className="help-chat-send" disabled={busy || !question.trim()}>{busy ? '考えています…' : '聞く'}</button>
          <MicButton disabled={busy} onInterim={setQuestion} onFinal={text => { setQuestion(text); void ask(text) }} />
          {turns.length > 0 && <button type="button" className="help-chat-clear" disabled={busy} onClick={() => { setTurns([]); setError(null) }}>最初から</button>}
        </div>
      </form>
    </section>
  )
}
