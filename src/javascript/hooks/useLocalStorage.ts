import { useState, useCallback, useRef, useEffect } from 'react'

/**
 * localStorage と同期する state フック。
 * debounceMs > 0 を指定すると書き込みをまとめてパフォーマンスを改善する（テキスト入力向け）。
 */
export function useLocalStorage<T>(
  key: string,
  defaultValue: T,
  debounceMs = 0,
): [T, (value: T) => void] {
  const [state, setState] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key)
      return raw !== null ? (JSON.parse(raw) as T) : defaultValue
    } catch {
      return defaultValue
    }
  })

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const setAndStore = useCallback(
    (value: T) => {
      setState(value)

      if (debounceMs > 0) {
        if (timerRef.current !== null) clearTimeout(timerRef.current)
        timerRef.current = setTimeout(() => {
          localStorage.setItem(key, JSON.stringify(value))
        }, debounceMs)
      } else {
        localStorage.setItem(key, JSON.stringify(value))
      }
    },
    [key, debounceMs],
  )

  // アンマウント時に残ったタイマーをフラッシュ
  useEffect(() => {
    return () => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current)
        localStorage.setItem(key, JSON.stringify(state))
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  return [state, setAndStore]
}
