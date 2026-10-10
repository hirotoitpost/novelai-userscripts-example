import { Unpackr, addExtension } from 'msgpackr'

/**
 * NovelAI公式のストーリー本文(storycontent.document)をプレーンテキストへ復元する。
 *
 * 形式: base64 → msgpack(msgpackrのレコード定義拡張 0x72 を使用)。
 * 復号したJSONの document フィールドが直接この形式で、素のmsgpackデコーダでは
 * 読めない(実機データで確認済み):
 *   - 先頭3バイトが fixext1 type=20 のバージョンマーカー
 *   - NovelAI独自の拡張型 20/30/31/40/41 が含まれる(本文復元には不要)
 *   - sections は数値キーのため Map として復号される
 *
 * 本文は sections(セクションID → {text}) を order(IDの並び順)の順に連結して得る。
 */

const NOVELAI_EXT_TYPES = [20, 30, 31, 40, 41]

let extensionsRegistered = false

function registerExtensions(): void {
  if (extensionsRegistered) return
  for (const type of NOVELAI_EXT_TYPES) {
    try {
      // 本文復元には使わないので、中身は素通しして復号が止まらないようにするだけ。
      addExtension({ type, read: (data: unknown) => data })
    } catch {
      // 既に登録済み(msgpackr側の予約や二重登録)は無視する
    }
  }
  extensionsRegistered = true
}

function base64ToBytes(base64: string): Uint8Array {
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  return bytes
}

interface DecodedSection {
  text?: unknown
}

interface DecodedDocument {
  sections?: unknown
  order?: unknown
}

/**
 * document(base64のmsgpack)からプレーンな本文を取り出す。
 * 復号できない・想定外の形式だった場合は空文字を返す(呼び出し側で貼り付けへ誘導する)。
 */
export function decodeStoryDocument(document: string): string {
  registerExtensions()

  let bytes: Uint8Array
  try {
    bytes = base64ToBytes(document)
  } catch {
    return ''
  }

  // 先頭が fixext1(0xd4) type=20 のバージョンマーカーなら読み飛ばす。
  const body = bytes[0] === 0xd4 && bytes[1] === 20 ? bytes.subarray(3) : bytes

  let decoded: DecodedDocument
  try {
    decoded = new Unpackr({ structuredClone: true, bundleStrings: true }).unpack(body) as DecodedDocument
  } catch {
    return ''
  }

  const sections = decoded?.sections
  const order = decoded?.order
  if (!(sections instanceof Map) || !Array.isArray(order)) return ''

  const paragraphs: string[] = []
  for (const id of order) {
    const section = sections.get(id) as DecodedSection | undefined
    if (section && typeof section.text === 'string') paragraphs.push(section.text)
  }
  return paragraphs.join('\n')
}
