import { useEffect, useState, useSyncExternalStore } from 'react'

/**
 * 音声(作業アシスタント)。
 * - 読み上げ: VOICEVOX(/api/voice。PC で動かしたエンジンへの中継)。文ごとに頼み、最初の文から流す(次の文は流しているあいだに作る)。
 *   VOICEVOX が動いていなければ、ブラウザの読み上げ(speechSynthesis)で読む
 * - 聞き取り: ブラウザの音声認識(Chrome などの SpeechRecognition)。マイクは https か localhost でしか使えない
 *   (スマホから http で開いているときは使えない)。Chrome の音声認識は、声を Google のサーバーへ送る
 */

export interface VoiceSettings {
  autoRead: boolean
  speaker: number
  speed: number
}

const KEY = 'nai_voice_settings'
const DEFAULTS: VoiceSettings = { autoRead: false, speaker: 8, speed: 1.1 }
let settings: VoiceSettings = (() => {
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) ?? '{}') as Partial<VoiceSettings> }
  } catch {
    return DEFAULTS
  }
})()
let speaking = false
const listeners = new Set<() => void>()
const emit = () => listeners.forEach(fn => fn())
const subscribe = (fn: () => void) => { listeners.add(fn); return () => { listeners.delete(fn) } }

export function useVoiceSettings(): [VoiceSettings, (patch: Partial<VoiceSettings>) => void] {
  const value = useSyncExternalStore(subscribe, () => settings)
  const update = (patch: Partial<VoiceSettings>) => {
    settings = { ...settings, ...patch }
    try { localStorage.setItem(KEY, JSON.stringify(settings)) } catch { /* 残せなくても使える */ }
    emit()
  }
  return [value, update]
}

export function useSpeaking(): boolean {
  return useSyncExternalStore(subscribe, () => speaking)
}

// ---- 読み上げ ----

let generation = 0
let current: HTMLAudioElement | null = null

function setSpeaking(on: boolean) {
  speaking = on
  emit()
}

async function post(token: string, path: string, body: unknown): Promise<Response> {
  return fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  })
}

function playBlob(blob: Blob, run: number): Promise<void> {
  return new Promise(resolve => {
    if (run !== generation) return resolve()
    const url = URL.createObjectURL(blob)
    const audio = new Audio(url)
    current = audio
    const done = () => { URL.revokeObjectURL(url); resolve() }
    audio.onended = done
    audio.onerror = done
    audio.play().catch(done)
  })
}

function browserSpeak(sentences: string[], run: number): Promise<void> {
  return new Promise(resolve => {
    if (!('speechSynthesis' in window) || run !== generation) return resolve()
    window.speechSynthesis.cancel()
    const utterances = sentences.map(text => {
      const u = new SpeechSynthesisUtterance(text)
      u.lang = 'ja-JP'
      u.rate = settings.speed
      return u
    })
    if (!utterances.length) return resolve()
    utterances[utterances.length - 1].onend = () => resolve()
    utterances[utterances.length - 1].onerror = () => resolve()
    utterances.forEach(u => window.speechSynthesis.speak(u))
  })
}

/** 文を読む(前に読んでいたものは止める)。VOICEVOX が使えなければブラウザの声で読む。 */
export async function speak(token: string | null, text: string): Promise<void> {
  stopSpeaking()
  const run = ++generation
  if (!token || !text.trim()) return
  setSpeaking(true)
  try {
    const split = await post(token, '/api/voice/split', { text })
    const sentences: string[] = split.ok ? (await split.json()).sentences : [text]
    const request = (sentence: string) => post(token, '/api/voice/tts', { text: sentence, speaker: settings.speaker, speed: settings.speed })
    let next = sentences.length ? request(sentences[0]) : null
    for (let i = 0; i < sentences.length && next; i++) {
      const res = await next
      if (run !== generation) return
      if (!res.ok) {
        // VOICEVOX が動いていない(503)など: 残りをブラウザの声で読む
        await browserSpeak(sentences.slice(i), run)
        return
      }
      next = i + 1 < sentences.length ? request(sentences[i + 1]) : null
      await playBlob(await res.blob(), run)
    }
  } catch {
    // 読めなくても、ほかの操作は続けられる
  } finally {
    if (run === generation) setSpeaking(false)
  }
}

export function stopSpeaking(): void {
  generation++
  current?.pause()
  current = null
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
  if (speaking) setSpeaking(false)
}

// ---- 聞き取り ----

interface Recognition {
  lang: string
  interimResults: boolean
  continuous: boolean
  start(): void
  stop(): void
  onresult: ((e: { resultIndex: number; results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null
  onend: (() => void) | null
  onerror: ((e: { error: string }) => void) | null
}

function recognitionClass(): (new () => Recognition) | null {
  const w = window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

/** 使えない理由(使えるなら null) */
export function speechInputUnavailable(): string | null {
  if (!window.isSecureContext) return 'マイクは https か、この PC(localhost)で開いたときだけ使えます'
  if (!recognitionClass()) return 'このブラウザは音声入力に対応していません(Chrome・Edge なら使えます)'
  return null
}

/** 話した言葉を入れる。onText は話しているあいだの途中経過も含めて呼ぶ(final で確定)。 */
export function useSpeechInput(onText: (text: string, final: boolean) => void) {
  const [listening, setListening] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [recognition, setRecognition] = useState<Recognition | null>(null)

  useEffect(() => () => recognition?.stop(), [recognition])

  const start = () => {
    const Klass = recognitionClass()
    const reason = speechInputUnavailable()
    if (!Klass || reason) {
      setError(reason)
      return
    }
    stopSpeaking()
    const r = new Klass()
    r.lang = 'ja-JP'
    r.interimResults = true
    r.continuous = false
    r.onresult = e => {
      let text = ''
      let final = false
      for (let i = 0; i < e.results.length; i++) {
        text += e.results[i][0].transcript
        final = e.results[i].isFinal
      }
      onText(text, final)
    }
    r.onerror = e => setError(e.error === 'not-allowed' ? 'マイクの使用が許可されていません' : `音声入力のエラー: ${e.error}`)
    r.onend = () => setListening(false)
    setError(null)
    setRecognition(r)
    setListening(true)
    r.start()
  }
  const stop = () => recognition?.stop()
  return { listening, error, start, stop }
}
