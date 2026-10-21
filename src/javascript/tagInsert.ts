/**
 * カンマ区切りのタグの欄の start〜end(範囲選択なら置き換え)に text を入れる。前後のタグとはカンマで区切り、
 * 既にあるカンマ・空白は活かして、足りない分だけ補って "a, X, b" の形にそろえる。
 * 戻り値は入れたあとの値と、入れた直後のカーソル位置(続けて同じ場所へ入れられるように)。
 */
export function insertTag(value: string, start: number, end: number, text: string): { value: string; caret: number } {
  const from = Math.min(start, value.length)
  const to = Math.min(end, value.length)
  const before = value.slice(0, from)
  const after = value.slice(to)
  const lead = !before.trim() ? '' : /,\s*$/.test(before) ? (/\s$/.test(before) ? '' : ' ') : ', '
  const trail = !after.trim() ? '' : /^\s*,/.test(after) ? '' : (/^\s/.test(after) ? ',' : ', ')
  return { value: before + lead + text + trail + after, caret: (before + lead + text).length }
}
