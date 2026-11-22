/**
 * 物語エディタ(漫画のページ)から、対話生成のページでコマを描き直すための受け渡し。
 * 描き方(テンプレート・カラー・キャラ参照・画像の設定)は漫画のページの state にしか無いので、
 * ボタンを押したときに sessionStorage に入れて渡す(Studio.tsx が読む)。
 */

export interface PanelDrawSettings {
  template: string
  color: boolean
  manga_style: boolean
  use_character_reference: boolean
  reference_strength: number
  reference_fidelity: number
  settings: Record<string, unknown>
}

export const PANEL_SETTINGS_KEY = 'studio.panelSettings'

export const DEFAULT_PANEL_DRAW: PanelDrawSettings = {
  template: 'grid4', color: false, manga_style: true, use_character_reference: false,
  reference_strength: 1, reference_fidelity: 1, settings: {},
}

export function savePanelSettings(settings: PanelDrawSettings): void {
  try {
    sessionStorage.setItem(PANEL_SETTINGS_KEY, JSON.stringify(settings))
  } catch {
    // 入れられなくても、既定の描き方で開く
  }
}

export function readPanelSettings(): PanelDrawSettings {
  try {
    const raw = sessionStorage.getItem(PANEL_SETTINGS_KEY)
    return raw ? { ...DEFAULT_PANEL_DRAW, ...(JSON.parse(raw) as Partial<PanelDrawSettings>) } : DEFAULT_PANEL_DRAW
  } catch {
    return DEFAULT_PANEL_DRAW
  }
}

/** 対話生成のページで、このシーンのコマを描き直す URL */
export function studioPanelUrl(sceneId: number): string {
  return `/studio?scene=${sceneId}`
}
