import { useEffect, useState } from 'react'
import { apiFetch } from '../api'
import { useAuth } from '../context/AuthContext'
import { speak, speechInputUnavailable, stopSpeaking, useSpeaking, useSpeechInput, useVoiceSettings } from '../voice'

/** 音声の設定(作業アシスタントの見出しの 🔊 から開く): 自動で読む・声・速さ。VOICEVOX のキャラ名を表記する。 */
export function VoiceSettingsBox() {
  const { token } = useAuth()
  const [settings, update] = useVoiceSettings()
  const [speakers, setSpeakers] = useState<{ id: number; name: string; style: string }[] | null>(null)
  const [unavailable, setUnavailable] = useState<string | null>(null)
  const speaking = useSpeaking()

  useEffect(() => {
    if (!token) return
    apiFetch<{ speakers: { id: number; name: string; style: string }[] }>(token, '/api/voice/speakers')
      .then(res => { setSpeakers(res.speakers); setUnavailable(null) })
      .catch(e => { setSpeakers(null); setUnavailable(e instanceof Error ? e.message : String(e)) })
  }, [token])

  const chosen = speakers?.find(s => s.id === settings.speaker)
  return (
    <div className="as-voice">
      <label className="as-voice-row">
        <input type="checkbox" checked={settings.autoRead} onChange={e => update({ autoRead: e.target.checked })} />
        答えと「次の操作」を自動で読み上げる
      </label>
      {speakers && (
        <label className="as-voice-row">
          声
          <select value={settings.speaker} onChange={e => update({ speaker: Number(e.target.value) })}>
            {speakers.map(s => <option key={s.id} value={s.id}>{s.name}({s.style})</option>)}
          </select>
        </label>
      )}
      <label className="as-voice-row">
        速さ {settings.speed.toFixed(1)}
        <input type="range" min={0.7} max={1.8} step={0.1} value={settings.speed} onChange={e => update({ speed: Number(e.target.value) })} />
      </label>
      <div className="as-voice-row">
        <button type="button" className="as-go" onClick={() => void speak(token, 'こんにちは。作業アシスタントです。この声で読み上げます。')}>試しに聞く</button>
        {speaking && <button type="button" className="as-go" onClick={stopSpeaking}>止める</button>}
      </div>
      {unavailable
        ? <p className="as-muted">VOICEVOX が動いていないので、ブラウザの声で読みます。PC で VOICEVOX を起動すると、声を選べます。</p>
        : chosen && <p className="as-muted">VOICEVOX:{chosen.name}</p>}
    </div>
  )
}

/** 1つの文を読む・止めるボタン */
export function SpeakButton({ text, label = '読む' }: { text: string; label?: string }) {
  const { token } = useAuth()
  const speaking = useSpeaking()
  return (
    <button type="button" className="as-speak" title={speaking ? '読み上げを止める' : '読み上げる'}
      onClick={() => (speaking ? stopSpeaking() : void speak(token, text))}>
      {speaking ? '■ 止める' : `🔊 ${label}`}
    </button>
  )
}

/** マイクで話して入力する(話し終わったら onFinal)。使えない環境では理由を出す */
export function MicButton({ onInterim, onFinal, disabled }: {
  onInterim: (text: string) => void
  onFinal: (text: string) => void
  disabled?: boolean
}) {
  const reason = speechInputUnavailable()
  const { listening, error, start, stop } = useSpeechInput((text, final) => (final ? onFinal(text) : onInterim(text)))
  return (
    <>
      <button type="button" className={listening ? 'as-mic as-mic--on' : 'as-mic'} disabled={disabled || !!reason}
        title={reason ?? (listening ? '聞き取りを止める' : '話して入力する(Chrome は声を Google の音声認識へ送ります)')}
        onClick={() => (listening ? stop() : start())}>
        {listening ? '● 聞いています' : '🎤 話す'}
      </button>
      {error && <span className="help-chat-error">{error}</span>}
    </>
  )
}

/** 新しい答えが来たら、自動で読む(設定が「自動で読み上げる」のとき) */
export function useAutoRead(text: string | null): void {
  const { token } = useAuth()
  const [settings] = useVoiceSettings()
  useEffect(() => {
    if (text && settings.autoRead) void speak(token, text)
    // 新しい文が来たときだけ読む
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text])
}
