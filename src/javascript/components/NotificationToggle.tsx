import { useEffect, useState } from 'react'
import {
  disablePush,
  enableNotifications,
  isPushSubscribed,
  notifySupport,
  NotifySupport,
  sendTestPush,
  subscribePush,
} from '../notify'

/** 処理終了のブラウザ通知の状態表示と、オン/オフ・テスト送信。 */
export default function NotificationToggle() {
  const [support, setSupport] = useState<NotifySupport>(() => notifySupport())
  const [pushOn, setPushOn] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [working, setWorking] = useState(false)

  useEffect(() => {
    if (support === 'granted') void isPushSubscribed().then(setPushOn)
  }, [support])

  // サイト設定で許可を変えて戻ってきたら、表示を合わせる
  useEffect(() => {
    const recheck = () => { if (!document.hidden) setSupport(notifySupport()) }
    document.addEventListener('visibilitychange', recheck)
    return () => document.removeEventListener('visibilitychange', recheck)
  }, [])

  async function enable() {
    setWorking(true)
    setNote(null)
    const result = await enableNotifications()
    setSupport(result)
    if (result === 'granted') {
      try {
        await subscribePush()
        setPushOn(true)
      } catch (e) {
        setPushOn(false)
        setNote(`プッシュ通知を登録できませんでした(ページを開いている間だけ通知します): ${e instanceof Error ? e.message : String(e)}`)
      }
    }
    setWorking(false)
  }

  async function disable() {
    setWorking(true)
    await disablePush()
    setPushOn(false)
    setNote('プッシュ通知をやめました。ページを開いている間の通知は、ブラウザの設定で許可を外すまで出ます。')
    setWorking(false)
  }

  async function resubscribe() {
    setWorking(true)
    setNote(null)
    try {
      await subscribePush()
      setPushOn(true)
      setNote('登録しました。「テスト通知」で届くか確認できます。')
    } catch (e) {
      setPushOn(false)
      setNote(`プッシュ通知を登録できませんでした: ${e instanceof Error ? e.message : String(e)}`)
    }
    setWorking(false)
  }

  async function test() {
    setWorking(true)
    try {
      const sent = await sendTestPush()
      setNote(sent > 0 ? `テスト通知を${sent}台に送りました。` : '送り先がありません。もう一度オンにしてください。')
    } catch (e) {
      setNote(`テスト通知を送れませんでした: ${e instanceof Error ? e.message : String(e)}`)
    }
    setWorking(false)
  }

  const page = window.location.origin
  const api = `${window.location.protocol}//${window.location.hostname}:8000`

  return (
    <div className="notify-toggle">
      {support === 'unsupported' && (
        <>
          <span>🔕 このURLではブラウザ通知を使えません(HTTPS か localhost でのみ使えます)。</span>
          <details>
            <summary>スマホ(Android の Chrome)で使う設定</summary>
            <ol>
              <li>Chrome のアドレス欄に <code>chrome://flags/#unsafely-treat-insecure-origin-as-secure</code> を入力して開く</li>
              <li>欄に <code>{page},{api}</code> を入力し、Enabled にする</li>
              <li>「Relaunch」で Chrome を再起動し、このページを開き直す</li>
            </ol>
            <p>iPhone は HTTPS で開き、ホーム画面に追加したときだけ使えます(詳しくは docs/notifications_jp.md)。</p>
          </details>
        </>
      )}
      {support === 'default' && (
        <button type="button" disabled={working} onClick={enable}>
          {working ? '設定中...' : '🔔 処理が終わったら通知する'}
        </button>
      )}
      {support === 'denied' && (
        <>
          <span>🔕 通知がブロックされています(Chrome が自動でブロックした場合もこの状態です)。</span>
          <details open>
            <summary>許可し直す方法</summary>
            <ol>
              <li>
                Android の Chrome: 右上の ⋮ →「設定」→「サイトの設定」→「通知」を開き、
                「許可しないサイト」の <code>{page}</code> の ⋮ →「許可」
              </li>
              <li>PC の Chrome: アドレス欄左のアイコン →「通知」を「許可」</li>
              <li>このページに戻り、「画面を消していても届くようにする」→「テスト通知」</li>
            </ol>
          </details>
        </>
      )}
      {support === 'granted' && (
        <>
          <span>
            {pushOn ? '🔔 通知オン(画面を消していても届きます)' : '🔔 通知オン(ページを開いている間だけ)'}
          </span>
          {pushOn ? (
            <>
              <button type="button" className="story-secondary" disabled={working} onClick={test}>
                テスト通知
              </button>
              <button type="button" className="story-secondary" disabled={working} onClick={disable}>
                プッシュ通知をやめる
              </button>
            </>
          ) : (
            <button type="button" className="story-secondary" disabled={working} onClick={resubscribe}>
              {working ? '登録中...' : '画面を消していても届くようにする'}
            </button>
          )}
        </>
      )}
      {note && <p className="notify-toggle-note">{note}</p>}
    </div>
  )
}
