import { useEffect, useRef, useState } from 'react'
import { apiFetch } from '../api'
import { useAuth } from '../context/AuthContext'
import { useLocalStorage } from '../hooks/useLocalStorage'
import HelpMarkdown from './HelpMarkdown'
import { MicButton, SpeakButton, useAutoRead } from './VoiceControls'
import './HelpChat.css'

/**
 * 作業アシスタントのチャット(/api/assistant/chat)。今のページの状況(「次の操作」と同じもの)とヘルプの記事を渡して、
 * 文章モデルに答えさせる。読むだけ(操作はしない)。答えの中のリンクは、押すとその欄へ移る。
 * 会話は、タブを閉じるまで残す(sessionStorage)。
 */

type Engine = 'default' | 'auto' | 'novelai' | 'ollama'

interface Turn {
  role: 'user' | 'assistant'
  content: string
  engine?: string
}

const STORE = 'nai_assistant_chat'
const ENGINE_LABEL: Record<string, string> = { novelai: 'NovelAI', ollama: 'Ollama', auto: '自動' }

function loadTurns(): Turn[] {
  try {
    return JSON.parse(sessionStorage.getItem(STORE) ?? '[]') as Turn[]
  } catch {
    return []
  }
}

export default function AssistantChat({ page, storyId, characterId, onNavigate }: {
  page: string
  storyId: number
  characterId: number
  onNavigate: (href: string) => void
}) {
  const { token } = useAuth()
  const [engine, setEngine] = useLocalStorage<Engine>('nai_assistant_engine', 'default')
  const [engines, setEngines] = useState<{ default: string; ollama_ready: boolean; novelai_ready: boolean } | null>(null)
  const [turns, setTurns] = useState<Turn[]>(loadTurns)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const end = useRef<HTMLDivElement>(null)
  // この画面で受け取ったばかりの答え(自動で読む。開き直したときの古い答えは読まない)
  const [fresh, setFresh] = useState<string | null>(null)
  useAutoRead(fresh)

  useEffect(() => {
    if (!token) return
    apiFetch<typeof engines>(token, '/api/assistant/chat/engines').then(setEngines).catch(() => setEngines(null))
  }, [token])
  useEffect(() => {
    try { sessionStorage.setItem(STORE, JSON.stringify(turns.slice(-20))) } catch { /* 残せなくても使える */ }
    end.current?.scrollIntoView({ block: 'nearest' })
  }, [turns, busy])

  const send = async (spoken?: string) => {
    const text = (spoken ?? message).trim()
    if (!text || !token || busy) return
    setBusy(true)
    setError(null)
    setMessage('')
    const history = turns.map(({ role, content }) => ({ role, content }))
    setTurns(list => [...list, { role: 'user', content: text }])
    try {
      const res = await apiFetch<{ answer: string; engine: string }>(token, '/api/assistant/chat', {
        message: text, history, page, story_id: storyId, character_id: characterId, engine,
      })
      setTurns(list => [...list, { role: 'assistant', content: res.answer, engine: res.engine }])
      setFresh(res.answer)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setMessage(text)
      setTurns(list => list.slice(0, -1))
    } finally {
      setBusy(false)
    }
  }

  const defaultLabel = engines ? ENGINE_LABEL[engines.default] ?? engines.default : ''
  return (
    <section className="help-chat" aria-label="作業アシスタントのチャット">
      <div className="help-chat-head">
        <label>
          文章モデル
          <select value={engine} onChange={e => setEngine(e.target.value as Engine)} disabled={busy}>
            <option value="default">既定{defaultLabel ? `(${defaultLabel})` : ''}</option>
            <option value="novelai">NovelAI(文章生成の残量を使う){engines && !engines.novelai_ready ? ' ※トークンなし' : ''}</option>
            <option value="ollama">Ollama(この PC){engines && !engines.ollama_ready ? ' ※未設定' : ''}</option>
          </select>
        </label>
      </div>
      <p className="help-chat-hint">
        今の画面の状況とヘルプの記事を元に答えます。操作はしません(移る先はリンクで出します)。文章モデルの答えなので、画面で確かめてください。
      </p>

      {turns.length > 0 && (
        <div className="help-chat-log">
          {turns.map((turn, index) => (
            <div key={index} className={`help-chat-turn help-chat-turn--${turn.role}`}>
              {turn.role === 'user' ? <p>{turn.content}</p> : <HelpMarkdown source={turn.content} onNavigate={onNavigate} />}
              {turn.role === 'assistant' && (
                <div className="help-chat-sources">
                  <SpeakButton text={turn.content} />
                  {turn.engine && <span>({ENGINE_LABEL[turn.engine] ?? turn.engine})</span>}
                </div>
              )}
            </div>
          ))}
          {busy && <div className="help-chat-turn help-chat-turn--assistant help-chat-wait">考えています…</div>}
          <div ref={end} />
        </div>
      )}
      {error && <p className="help-chat-error" role="alert">{error}</p>}

      <form className="help-chat-form" onSubmit={e => { e.preventDefault(); void send() }}>
        <textarea value={message} rows={2} maxLength={2000} placeholder="例: 次は何をすればいい? / この漫画を PDF にしたい"
          aria-label="アシスタントへの質問" disabled={busy}
          onChange={e => setMessage(e.target.value)}
          onKeyDown={e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); void send() } }} />
        <div className="help-chat-actions">
          <button type="submit" className="help-chat-send" disabled={busy || !message.trim()}>{busy ? '考えています…' : '送る'}</button>
          <MicButton disabled={busy} onInterim={setMessage} onFinal={text => { setMessage(text); void send(text) }} />
          {turns.length > 0 && <button type="button" className="help-chat-clear" disabled={busy} onClick={() => { setTurns([]); setError(null) }}>最初から</button>}
        </div>
      </form>
    </section>
  )
}
