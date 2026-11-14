import { useEffect, useSyncExternalStore } from 'react'

/**
 * 作業アシスタントに「今ページで何を開いているか」を伝える(物語の ID など)。
 * ページは useAssistantContext({ storyId }) を呼ぶだけ。ページを離れると消える。
 * 作業が進んだ(生成が終わった など)ときは refreshAssistant() で案内を読み直させる。
 */

export interface AssistantContext {
  storyId?: number | null
  characterId?: number | null
}

let current: AssistantContext = {}
let version = 0
const listeners = new Set<() => void>()

function emit() {
  version++
  listeners.forEach(fn => fn())
}

function subscribe(fn: () => void) {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

export function useAssistantContext(context: AssistantContext): void {
  const key = JSON.stringify(context)
  useEffect(() => {
    current = JSON.parse(key) as AssistantContext
    emit()
    return () => {
      current = {}
      emit()
    }
  }, [key])
}

/** 案内を読み直させる(内容は同じでも、版を上げて知らせる) */
export function refreshAssistant(): void {
  emit()
}

export function useCurrentAssistantContext(): { context: AssistantContext; version: number } {
  const v = useSyncExternalStore(subscribe, () => version)
  return { context: current, version: v }
}
