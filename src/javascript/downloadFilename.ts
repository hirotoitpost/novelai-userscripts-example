// crypto.randomUUID() は secure context (https/localhost) 限定で、
// LAN上のスマホからは http://<IP>:5173 でアクセスするため使えない。
// 衝突しても実害が無いファイル名生成なので Math.random ベースの短縮IDで十分。
function shortId(): string {
  return Math.random().toString(16).slice(2, 10).padEnd(8, '0')
}

export function formatForFilename(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}` +
    `-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`
  )
}

/** 生成画像のダウンロード用ファイル名 (nai_YYYYMMDD-HHmmss_xxxxxxxx.png) */
export function naiImageFilename(date: Date = new Date(), ext = 'png'): string {
  return `nai_${formatForFilename(date)}_${shortId()}.${ext}`
}
