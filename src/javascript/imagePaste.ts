import { useEffect, useRef } from 'react'

/**
 * クリップボードの画像を受け取る。Ctrl+V(貼り付けの操作)はどこでも使え、ボタンからの読み取り(navigator.clipboard.read)は
 * ブラウザが許す所(localhost や https)だけで使える。
 */

/** 貼り付けの操作に画像が入っていれば、その画像のファイル */
export function imageFromPaste(e: ClipboardEvent): File | null {
  for (const item of Array.from(e.clipboardData?.items ?? [])) {
    if (item.kind === 'file' && item.type.startsWith('image/')) {
      const file = item.getAsFile()
      if (file) return file
    }
  }
  return null
}

/** ボタンからクリップボードを読めるか(読めない所では Ctrl+V を案内する) */
export function canReadClipboard(): boolean {
  return typeof navigator !== 'undefined' && !!navigator.clipboard && 'read' in navigator.clipboard && window.isSecureContext
}

/** クリップボードの画像を読む(無ければ null。読めない・断られたら例外) */
export async function readClipboardImage(): Promise<File | null> {
  const items = await navigator.clipboard.read()
  for (const item of items) {
    const type = item.types.find(t => t.startsWith('image/'))
    if (type) {
      const blob = await item.getType(type)
      return new File([blob], `clipboard.${type.split('/')[1] || 'png'}`, { type })
    }
  }
  return null
}

/**
 * 有効なあいだ、ページのどこで Ctrl+V しても、クリップボードの画像を onImage に渡す。
 * 文字を貼り付けたとき(画像が入っていないとき)は、ふつうの貼り付けのまま。
 */
export function usePasteImage(enabled: boolean, onImage: (file: File) => void): void {
  const handler = useRef(onImage)
  handler.current = onImage
  useEffect(() => {
    if (!enabled) return
    const listener = (e: ClipboardEvent) => {
      const file = imageFromPaste(e)
      if (!file) return
      e.preventDefault()
      handler.current(file)
    }
    window.addEventListener('paste', listener)
    return () => window.removeEventListener('paste', listener)
  }, [enabled])
}

/** ファイルを data URL にする */
export function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}
