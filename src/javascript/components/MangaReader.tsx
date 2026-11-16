import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { MangaV2Element } from './MangaV2PageEditor'
import './MangaReader.css'

/**
 * 漫画の読み上げ(VOICEVOX)。合成したページを、ナレーション → セリフの順に話し手の声で読み、
 * 読んでいる吹き出しを枠で光らせて、読み終わったら次のページへ進む。
 * - 台本は /api/manga-voice/{物語}(シーンの順。吹き出しは scene_id と要素の種類・番号で合成結果と対応させる)
 * - キャラの声はキャラシートに入れる(全部の物語で共通)。ナレーションの声は物語ごと
 * - VOICEVOX が動いていなければ、ブラウザの声で読む
 * - 使った声の「VOICEVOX:(キャラ名)」を表記する(各キャラの利用規約)
 */

interface ReadItem {
  scene_id: number
  scene_index: number
  kind: 'narration' | 'line' | 'thought' | 'sfx'
  element: string
  text: string
  read: string
  character_id: number | null
  character_name: string | null
  speaker: number
  speed: number
  pitch: number
  volume: number
}

interface CastVoice {
  id: number
  name: string
  speaker: number
  speed: number
  pitch: number
  assigned: boolean
  speaker_name: string | null
}

interface Readout {
  items: ReadItem[]
  cast: CastVoice[]
  narrator: { speaker: number; assigned: boolean; speaker_name: string | null }
  credits: string[]
  engine_ready: boolean
}

interface Speaker {
  id: number
  name: string
  style: string
}

interface Props {
  apiOrigin: string
  storyId: number
  token: string | null
  pages: string[]
  elements: MangaV2Element[][]
  pageWidths: number[]
  pageHeight: number
  fileUrl: (path: string) => string
}

const KIND_LABEL: Record<ReadItem['kind'], string> = { narration: 'ナレーション', line: '', thought: '(心の声)', sfx: '効果音' }

function headers(token: string | null): Record<string, string> {
  return { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) }
}

/** 台本の項目が、どのページのどの要素(吹き出しなど)か */
function locate(item: ReadItem, elements: MangaV2Element[][]): { page: number; element: MangaV2Element | null } {
  for (let page = 0; page < elements.length; page++) {
    const found = elements[page].find(e => e.key.startsWith(`${item.scene_id}:`) && e.key.endsWith(`:${item.element}`))
    if (found) return { page, element: found }
  }
  for (let page = 0; page < elements.length; page++) {
    if (elements[page].some(e => e.key.startsWith(`${item.scene_id}:`))) return { page, element: null }
  }
  return { page: -1, element: null }
}

function wait(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}

export default function MangaReader({ apiOrigin, storyId, token, pages, elements, pageWidths, pageHeight, fileUrl }: Props) {
  const [readout, setReadout] = useState<Readout | null>(null)
  const [speakers, setSpeakers] = useState<Speaker[]>([])
  const [readSfx, setReadSfx] = useState(false)
  const [rate, setRate] = useState(1.0)
  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [fullscreen, setFullscreen] = useState(false)
  const [showVoices, setShowVoices] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const run = useRef(0)
  const audio = useRef<HTMLAudioElement | null>(null)

  const load = useCallback(async () => {
    const res = await fetch(`${apiOrigin}/api/manga-voice/${storyId}?sfx=${readSfx}`, { headers: headers(token) })
    if (!res.ok) {
      setError(`読み上げの台本を読めませんでした(${res.status})`)
      return
    }
    setReadout(await res.json())
    setError(null)
  }, [apiOrigin, storyId, token, readSfx])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    fetch(`${apiOrigin}/api/voice/speakers`, { headers: headers(token) })
      .then(r => (r.ok ? r.json() : { speakers: [] }))
      .then(d => setSpeakers(d.speakers ?? []))
      .catch(() => setSpeakers([]))
  }, [apiOrigin, token])

  // 台本の項目ごとのページと要素(合成し直すと変わる)
  const placed = useMemo(() => {
    const items = readout?.items ?? []
    let last = 0
    return items.map(item => {
      const where = locate(item, elements)
      if (where.page < 0) return { page: last, element: null }
      last = where.page
      return where
    })
  }, [readout, elements])

  const items = readout?.items ?? []
  const current = items[index] ?? null
  const currentPage = placed[index]?.page ?? 0
  const [viewPage, setViewPage] = useState(0)
  useEffect(() => { if (playing || current) setViewPage(currentPage) }, [currentPage, playing, current])

  const stop = useCallback(() => {
    run.current++
    audio.current?.pause()
    audio.current = null
    if ('speechSynthesis' in window) window.speechSynthesis.cancel()
    setPlaying(false)
  }, [])

  useEffect(() => () => stop(), [stop])
  useEffect(() => { stop() }, [readout, stop])

  async function synth(item: ReadItem): Promise<Blob | null> {
    try {
      const res = await fetch(`${apiOrigin}/api/voice/tts`, {
        method: 'POST',
        headers: headers(token),
        body: JSON.stringify({
          text: item.read,
          speaker: item.speaker,
          speed: Math.max(0.5, Math.min(2.0, item.speed * rate)),
          pitch: item.pitch,
          volume: item.volume,
        }),
      })
      return res.ok ? await res.blob() : null
    } catch {
      return null
    }
  }

  function playBlob(blob: Blob, mine: number): Promise<void> {
    return new Promise(resolve => {
      if (mine !== run.current) return resolve()
      const url = URL.createObjectURL(blob)
      const a = new Audio(url)
      audio.current = a
      const done = () => { URL.revokeObjectURL(url); resolve() }
      a.onended = done
      a.onerror = done
      a.play().catch(done)
    })
  }

  function browserRead(item: ReadItem, mine: number): Promise<void> {
    return new Promise(resolve => {
      if (!('speechSynthesis' in window) || mine !== run.current) return resolve()
      const u = new SpeechSynthesisUtterance(item.read)
      u.lang = 'ja-JP'
      u.rate = item.speed * rate
      u.onend = () => resolve()
      u.onerror = () => resolve()
      window.speechSynthesis.speak(u)
    })
  }

  async function play(from: number) {
    stop()
    const mine = ++run.current
    setPlaying(true)
    let next: Promise<Blob | null> | null = from < items.length ? synth(items[from]) : null
    for (let i = from; i < items.length && next; i++) {
      setIndex(i)
      const blob = await next
      if (mine !== run.current) return
      // 次の文は、いまの文を流しているあいだに作る
      next = i + 1 < items.length ? synth(items[i + 1]) : null
      if (blob) await playBlob(blob, mine)
      else await browserRead(items[i], mine)
      if (mine !== run.current) return
      const pageChange = i + 1 < items.length && placed[i + 1]?.page !== placed[i]?.page
      await wait(pageChange ? 700 : 250)
    }
    if (mine === run.current) setPlaying(false)
  }

  function jump(to: number) {
    const target = Math.max(0, Math.min(items.length - 1, to))
    setIndex(target)
    if (playing) void play(target)
  }

  function startPage(page: number) {
    const first = placed.findIndex(p => p.page === page)
    setViewPage(page)
    if (first >= 0) setIndex(first)
  }

  /** キャラの声を保存する。speaker が null なら既定の声に戻す(patch に無ければ今の割り当てのまま) */
  async function saveVoice(characterId: number, patch: { speaker?: number | null; speed?: number; pitch?: number }, base: CastVoice) {
    const body = {
      speaker: patch.speaker !== undefined ? patch.speaker : base.assigned ? base.speaker : null,
      speed: patch.speed ?? base.speed,
      pitch: patch.pitch ?? base.pitch,
    }
    const res = await fetch(`${apiOrigin}/api/manga-voice/characters/${characterId}`, {
      method: 'PUT', headers: headers(token), body: JSON.stringify(body),
    })
    if (!res.ok) setError(`声を保存できませんでした(${res.status})`)
    await load()
  }

  async function saveNarrator(speaker: number | null) {
    await fetch(`${apiOrigin}/api/manga-voice/${storyId}/narrator`, {
      method: 'PUT', headers: headers(token), body: JSON.stringify({ speaker }),
    })
    await load()
  }

  async function sample(speaker: number, speed: number, pitch: number, text: string) {
    stop()
    const mine = ++run.current
    const blob = await synth({ ...(current ?? items[0]), read: text, speaker, speed, pitch, volume: 1 } as ReadItem)
    if (blob) await playBlob(blob, mine)
  }

  useEffect(() => {
    if (!fullscreen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setFullscreen(false)
      else if (e.key === ' ') { e.preventDefault(); if (playing) stop(); else void play(index) }
      else if (e.key === 'ArrowRight') jump(index + 1)
      else if (e.key === 'ArrowLeft') jump(index - 1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  if (!readout) return error ? <p className="story-error">{error}</p> : <p className="story-muted">読み上げの台本を読み込んでいます…</p>
  const width = pageWidths[viewPage] ?? pageWidths[0] ?? 1
  const highlight = placed[index]?.page === viewPage ? placed[index]?.element : null
  const voiceLabel = (id: number) => {
    const s = speakers.find(v => v.id === id)
    return s ? `${s.name}(${s.style})` : `声 ${id}`
  }

  return (
    <div className={`mr-reader${fullscreen ? ' mr-reader--full' : ''}`}>
      <div className="mr-controls">
        {playing
          ? <button type="button" onClick={stop}>⏸ 止める</button>
          : <button type="button" disabled={!items.length} onClick={() => void play(index)}>▶ 読み上げ</button>}
        <button type="button" className="story-secondary" disabled={index <= 0} onClick={() => jump(index - 1)}>⏮ 前</button>
        <button type="button" className="story-secondary" disabled={index >= items.length - 1} onClick={() => jump(index + 1)}>次 ⏭</button>
        <label className="mr-inline">
          速さ {rate.toFixed(1)}
          <input type="range" min={0.7} max={1.6} step={0.1} value={rate} onChange={e => setRate(Number(e.target.value))} />
        </label>
        <label className="mr-inline">
          <input type="checkbox" checked={readSfx} onChange={e => setReadSfx(e.target.checked)} />
          効果音も読む
        </label>
        <button type="button" className="story-secondary" onClick={() => setShowVoices(v => !v)}>声の割り当て</button>
        <button type="button" className="story-secondary" onClick={() => setFullscreen(f => !f)}>
          {fullscreen ? '全画面を閉じる' : '全画面で読む'}
        </button>
      </div>
      {!readout.engine_ready && (
        <p className="story-muted">VOICEVOX が動いていないので、ブラウザの声で読みます(PC で VOICEVOX を起動すると、キャラの声で読みます)。</p>
      )}
      {error && <p className="story-error">{error}</p>}

      {showVoices && (
        <div className="mr-voices">
          <div className="mr-voice-row">
            <span className="mr-voice-name">ナレーション</span>
            <select value={readout.narrator.assigned ? readout.narrator.speaker : ''}
              onChange={e => void saveNarrator(e.target.value === '' ? null : Number(e.target.value))}>
              <option value="">既定({readout.narrator.speaker_name ?? voiceLabel(readout.narrator.speaker)})</option>
              {speakers.map(s => <option key={s.id} value={s.id}>{s.name}({s.style})</option>)}
            </select>
            <button type="button" className="story-secondary"
              onClick={() => void sample(readout.narrator.speaker, 1, 0, 'ナレーションは、この声で読みます。')}>試聴</button>
          </div>
          {readout.cast.map(c => (
            <div key={c.id} className="mr-voice-row">
              <span className="mr-voice-name">{c.name}</span>
              <select value={c.assigned ? c.speaker : ''}
                onChange={e => void saveVoice(c.id, { speaker: e.target.value === '' ? null : Number(e.target.value) }, c)}>
                <option value="">既定({c.speaker_name ?? voiceLabel(c.speaker)})</option>
                {speakers.map(s => <option key={s.id} value={s.id}>{s.name}({s.style})</option>)}
              </select>
              <label className="mr-inline">
                速さ {c.speed.toFixed(2)}
                <input type="range" min={0.7} max={1.5} step={0.05} defaultValue={c.speed}
                  onMouseUp={e => void saveVoice(c.id, { speed: Number((e.target as HTMLInputElement).value) }, c)}
                  onTouchEnd={e => void saveVoice(c.id, { speed: Number((e.target as HTMLInputElement).value) }, c)} />
              </label>
              <label className="mr-inline">
                高さ {c.pitch.toFixed(2)}
                <input type="range" min={-0.15} max={0.15} step={0.01} defaultValue={c.pitch}
                  onMouseUp={e => void saveVoice(c.id, { pitch: Number((e.target as HTMLInputElement).value) }, c)}
                  onTouchEnd={e => void saveVoice(c.id, { pitch: Number((e.target as HTMLInputElement).value) }, c)} />
              </label>
              <button type="button" className="story-secondary"
                onClick={() => void sample(c.speaker, c.speed, c.pitch, `${c.name}です。よろしくお願いします。`)}>試聴</button>
            </div>
          ))}
          <p className="story-muted">キャラの声はキャラシートに保存され、ほかの物語でも同じ声で読みます。</p>
        </div>
      )}

      <div className="mr-page-tabs">
        {pages.map((path, page) => (
          <button key={path} type="button" className={page === viewPage ? 'mv2-mode-active' : 'story-secondary'}
            onClick={() => startPage(page)}>P{page + 1}</button>
        ))}
      </div>
      <div className="mr-stage">
        <div className="mr-page" style={{ aspectRatio: `${width} / ${pageHeight}` }}>
          <img src={fileUrl(pages[viewPage])} alt={`${viewPage + 1}ページ`} />
          {highlight && (
            <div className="mr-highlight" style={{
              left: `${(highlight.box[0] / width) * 100}%`,
              top: `${(highlight.box[1] / pageHeight) * 100}%`,
              width: `${((highlight.box[2] - highlight.box[0]) / width) * 100}%`,
              height: `${((highlight.box[3] - highlight.box[1]) / pageHeight) * 100}%`,
            }} />
          )}
        </div>
      </div>
      {current && (
        <p className="mr-caption">
          <span className="mr-who">{current.character_name ?? KIND_LABEL[current.kind] ?? ''}{current.kind === 'thought' ? KIND_LABEL.thought : ''}</span>
          {current.text}
          <span className="mr-count">{index + 1} / {items.length}</span>
        </p>
      )}
      {readout.credits.length > 0 && <p className="mr-credits">{readout.credits.join('、')}</p>}
    </div>
  )
}
