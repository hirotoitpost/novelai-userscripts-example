/**
 * キーボードショートカット。割り当ては YouTube に合わせる(F: 全画面、J / L: 前後、/: 検索、?: 一覧)。
 * 文字の入力中(入力欄・テキストエリア・選択肢)と、Ctrl / Alt / ⌘ と一緒に押したときは反応しない
 * (ブラウザのショートカットや、文字の入力を邪魔しないため)。
 */

export interface Shortcut {
  keys: string[]
  label: string
}

export interface ShortcutGroup {
  title: string
  items: Shortcut[]
}

/** 一覧(? で出す画面と、ヘルプの記事に同じものを載せる) */
export const SHORTCUT_GROUPS: ShortcutGroup[] = [
  {
    title: 'どのページでも',
    items: [
      { keys: ['?'], label: 'キーボードショートカットの一覧' },
      { keys: ['/'], label: 'ページの検索欄へ(ギャラリー・キャラ一覧・本棚・ヘルプ など)' },
      { keys: ['F'], label: '見ている画像・漫画を全画面にする' },
    ],
  },
  {
    title: '画像の拡大表示・本棚で読んでいるとき',
    items: [
      { keys: ['J', '←'], label: '前の画像(本棚の漫画は ← が次のページ)' },
      { keys: ['L', '→'], label: '次の画像(本棚の漫画は → が前のページ)' },
      { keys: ['Home', 'End'], label: '本棚: 最初のページ / 最後のページ' },
      { keys: ['Esc'], label: '閉じる' },
    ],
  },
  {
    title: '全画面のとき',
    items: [
      { keys: ['F', 'Esc'], label: '全画面を終える(ズーム中の Esc は、ズームを戻す)' },
      { keys: ['J', 'L'], label: '前 / 次(本棚の漫画は J が前のページ、L が次のページ)' },
      { keys: ['←', '→'], label: '左 / 右の画像(漫画は ← が次のページ)' },
      { keys: ['+', '-'], label: 'ズームする / 戻す' },
      { keys: ['0'], label: 'ズームを戻す' },
    ],
  },
]

/** 文字を入力しているところでキーを押したか */
export function isTyping(e: KeyboardEvent): boolean {
  const target = e.target as HTMLElement | null
  if (!target) return false
  if (target.isContentEditable) return true
  const tag = target.tagName
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (tag !== 'INPUT') return false
  // チェックボックス・ボタン・スライダーなどは、文字の入力ではない
  const type = (target as HTMLInputElement).type
  return !['checkbox', 'radio', 'button', 'submit', 'reset', 'range', 'color', 'file', 'image'].includes(type)
}

/** ショートカットとして扱ってよいキー入力か(入力中・Ctrl / Alt / ⌘ 付き・IME の変換中は扱わない) */
export function isShortcutKey(e: KeyboardEvent): boolean {
  return !e.isComposing && !e.ctrlKey && !e.metaKey && !e.altKey && !isTyping(e)
}

/** 押したキーが、この文字か(大文字・小文字を区別しない) */
export function keyIs(e: KeyboardEvent, letter: string): boolean {
  return e.key.toLowerCase() === letter
}

/** 全画面の表示が開いているか(開いているときは、全画面の表示がキーを受ける) */
export function fullscreenOpen(): boolean {
  return document.querySelector('.fsv') !== null
}

function visible(element: HTMLElement): boolean {
  return element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden'
}

/**
 * data-shortcut="name" の付いた、見えていて押せるボタンを押す。いくつもあるときは、後から出たもの
 * (拡大表示など、上に重なっているもの)を押す。押せたら true。
 */
export function clickShortcut(name: string): boolean {
  const buttons = [...document.querySelectorAll<HTMLButtonElement>(`[data-shortcut="${name}"]`)]
    .filter(b => !b.disabled && visible(b))
  const button = buttons[buttons.length - 1]
  if (!button) return false
  button.click()
  return true
}

/** ページの検索欄にカーソルを移す。移せたら true */
export function focusSearch(): boolean {
  const input = [...document.querySelectorAll<HTMLInputElement>('input[type="search"]')]
    .find(i => !i.disabled && visible(i))
  if (!input) return false
  input.focus()
  input.select()
  return true
}
