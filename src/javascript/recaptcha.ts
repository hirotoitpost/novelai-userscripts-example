// NovelAI の /user/login が要求する reCAPTCHA Enterprise トークンを取得する。
// site key は novelai.net 自身の HTML に埋め込まれている公開情報。
const SITE_KEY = '6Lfk9nYqAAAAAP6cKauxBjMa7Z3bbiN2mvG4x59O'

interface GrecaptchaEnterprise {
  ready: (callback: () => void) => void
  execute: (siteKey: string, options: { action: string }) => Promise<string>
}

declare global {
  interface Window {
    grecaptcha?: { enterprise: GrecaptchaEnterprise }
  }
}

export function getRecaptchaToken(action: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const grecaptcha = window.grecaptcha?.enterprise
    if (!grecaptcha) {
      reject(new Error('reCAPTCHA が読み込まれていません'))
      return
    }
    grecaptcha.ready(() => {
      grecaptcha.execute(SITE_KEY, { action }).then(resolve, reject)
    })
  })
}
