import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { API_ORIGIN, formatDate, readErrorDetail, thumbUrl } from '../library'
import { connect, deployContract, hasWallet, mintBatch, type MintItem } from '../nft'
import './Watermarks.css'

/**
 * 見えない透かし。X への投稿と出版物(ZIP)の画像には、目に見えない透かし(番号)が自動で入り、台帳に残る。
 * ここでは、ネットで見かけた画像が自分の出したものかを照合し、台帳(何をいつどこへ出したか)を見る。
 */

interface NftToken {
  contract: string
  tx_hash: string
  chain_name: string
  token_url: string | null
  tx_url: string | null
  created_at: string
}

interface NftContract {
  address: string
  owner: string
  chain_name: string
  url: string | null
}

interface NftConfig {
  chain_name: string
  max_batch: number
  contract: NftContract | null
}

interface Pending {
  total: number
  items: (MintItem & { label: string; purpose_label: string; source_path: string })[]
}

interface Mark {
  id: number
  mark_id: number
  purpose: string
  purpose_label: string
  source_path: string
  ref: string | null
  label: string
  x_post_id: number | null
  x_post_url: string | null
  publication_id: number | null
  publication_title: string | null
  width: number
  height: number
  created_at: string
  nft: NftToken | null
}

interface Verify {
  found: boolean
  method: 'exact' | 'watermark' | 'watermark_resized' | 'similar' | 'none'
  mark_id: number | null
  distance: number | null
  record: Mark | null
  foreign_mark?: number | null
}

const METHOD_TEXT: Record<Verify['method'], string> = {
  exact: '出したファイルとまったく同じです。',
  watermark: '画像の中の透かしを読めました。自分が出した画像です。',
  watermark_resized: '縮小された画像でしたが、元の大きさに戻して透かしを読めました。自分が出した画像です。',
  similar: '透かしは読めませんでしたが、見た目がほぼ同じ画像を出しています(切り抜き・加工されているかもしれません)。',
  none: '台帳に一致する画像はありません。',
}

function markText(id: number): string {
  return id.toString(16).toUpperCase().padStart(8, '0')
}

export default function Watermarks() {
  const navigate = useNavigate()
  const [marks, setMarks] = useState<Mark[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<string | null>(null)
  const [result, setResult] = useState<Verify | null>(null)
  const [dragging, setDragging] = useState(false)
  const [previewIsVideo, setPreviewIsVideo] = useState(false)
  const [nft, setNft] = useState<NftConfig | null>(null)
  const [pending, setPending] = useState<Pending | null>(null)
  const [account, setAccount] = useState<string | null>(null)
  const [nftBusy, setNftBusy] = useState<string | null>(null)
  const [nftNotice, setNftNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API_ORIGIN}/api/watermarks`)
      if (!res.ok) throw new Error(await readErrorDetail(res))
      setMarks(await res.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  const loadNft = useCallback(async () => {
    try {
      const get = async (path: string) => {
        const res = await fetch(`${API_ORIGIN}${path}`)
        if (!res.ok) throw new Error(await readErrorDetail(res))
        return res.json()
      }
      const [config, todo] = await Promise.all([get('/api/nft/config'), get('/api/nft/pending')])
      setNft(config)
      setPending(todo)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    void load()
    void loadNft()
  }, [load, loadNft])

  async function nftStep(label: string, work: () => Promise<void>) {
    setError(null)
    setNftNotice(null)
    setNftBusy(label)
    try {
      await work()
    } catch (e) {
      const err = e as { shortMessage?: string; message?: string; code?: string }
      setError(err.code === 'ACTION_REJECTED' ? 'MetaMask で取り消されました。' : (err.shortMessage ?? err.message ?? String(e)))
    } finally {
      setNftBusy(null)
    }
  }

  async function sendJson(path: string, method: string, body: unknown) {
    const res = await fetch(`${API_ORIGIN}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) throw new Error(await readErrorDetail(res))
    return res.json()
  }

  const onConnect = () => nftStep('connect', async () => setAccount(await connect()))

  const onDeploy = () => nftStep('deploy', async () => {
    if (!window.confirm('Sepolia(テストネット)に記録の契約を作ります。MetaMask でテスト用の ETH を少し使います。よろしいですか?')) return
    setAccount(await connect())
    const made = await deployContract()
    await sendJson('/api/nft/contract', 'PUT', { address: made.address, tx_hash: made.txHash, owner: made.owner })
    setNftNotice('記録の契約を作りました。')
    await loadNft()
  })

  const onMint = () => nftStep('mint', async () => {
    const contract = nft?.contract
    const items = pending?.items ?? []
    if (!contract || items.length === 0) return
    if (!window.confirm(`${items.length} 枚の記録を Sepolia に発行します(1回の取引)。よろしいですか?`)) return
    setAccount(await connect())
    const txHash = await mintBatch(contract.address, items)
    await sendJson('/api/nft/minted', 'POST', { contract: contract.address, tx_hash: txHash, mark_ids: items.map(i => i.mark_id) })
    setNftNotice(`${items.length} 枚を発行しました。`)
    await Promise.all([load(), loadNft()])
  })

  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview)
  }, [preview])

  async function verify(file: File) {
    setError(null)
    setResult(null)
    setPreview(URL.createObjectURL(file))
    setPreviewIsVideo(file.type.startsWith('video/'))
    setBusy(true)
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await fetch(`${API_ORIGIN}/api/watermarks/verify`, { method: 'POST', body: form })
      if (!res.ok) throw new Error(await readErrorDetail(res))
      setResult(await res.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function onFiles(files: FileList | null) {
    const file = files?.[0]
    if (file) void verify(file)
  }

  const record = result?.record ?? null

  return (
    <div className="wm-root">
      <header className="wm-header">
        <button type="button" className="wm-link" onClick={() => navigate('/')}>← ホーム</button>
        <h1>見えない透かし</h1>
      </header>

      <main className="wm-main">
        <p className="wm-lead">
          X への投稿と出版物(ZIP)の画像には、目に見えない透かし(番号)が自動で入り、下の台帳に残ります。
          ネットで見かけた画像を入れると、自分の出したものかを確かめられます。
          JPEG への再圧縮や軽い縮小のあとでも読めますが、切り抜き・回転・大きな縮小では読めないことがあります
          (そのときは見た目の指紋で照合します)。
        </p>
        {error && <p className="wm-error">{error}</p>}

        <section className="wm-section">
          <h2>照合する</h2>
          <label
            className={`wm-drop${dragging ? ' is-dragging' : ''}`}
            onDragOver={e => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={e => { e.preventDefault(); setDragging(false); onFiles(e.dataTransfer.files) }}
          >
            <input type="file" accept="image/*,video/mp4,video/quicktime,video/webm" onChange={e => { onFiles(e.target.files); e.target.value = '' }} />
            {busy ? '照合しています…' : '画像・動画をここに置くか、押して選んでください'}
          </label>
          {(preview || result) && (
            <div className="wm-result">
              {preview && (previewIsVideo
                ? <video src={preview} controls muted playsInline />
                : <img src={preview} alt="照合した画像" />)}
              {result && (
                <div className={`wm-verdict${result.found ? ' is-found' : ''}`}>
                  <strong>{result.found ? (result.method === 'similar' ? 'よく似た画像を出しています' : '自分の画像です') : '見つかりません'}</strong>
                  <p>{METHOD_TEXT[result.method]}</p>
                  {result.mark_id !== null && <p>透かしの番号: <code>{markText(result.mark_id)}</code></p>}
                  {result.distance !== null && result.method !== 'exact' && (
                    <p className="wm-meta">見た目の指紋の差: {result.distance} / 64(6 以下ならほぼ同じ絵)</p>
                  )}
                  {result.foreign_mark != null && (
                    <p className="wm-meta">台帳に無い番号の透かし({markText(result.foreign_mark)})が読めました。別の鍵や別の環境で入れたものかもしれません。</p>
                  )}
                  {record && (
                    <div className="wm-record">
                      <img src={thumbUrl(record.source_path, 240)} alt={record.label} loading="lazy" />
                      <div>
                        <p>{result.found ? '出した記録' : 'いちばん近い記録'}: {record.purpose_label} ・ {formatDate(record.created_at)}</p>
                        {record.label && <p>{record.label}</p>}
                        {record.x_post_url && <a href={record.x_post_url} target="_blank" rel="noreferrer">X の投稿を開く</a>}
                        {record.publication_title && <p>出版物: {record.publication_title}</p>}
                        {record.nft?.token_url && (
                          <a href={record.nft.token_url} target="_blank" rel="noreferrer">NFT の記録を見る({record.nft.chain_name})</a>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </section>

        <section className="wm-section">
          <h2>NFT として記録する <span className="wm-meta">{nft?.chain_name ?? 'Sepolia'} テストネット</span></h2>
          <p className="wm-lead">
            台帳の画像を1枚ずつ、テストネットの NFT として記録します。チェーンに載るのは、ファイルの指紋(SHA-256)・見た目の指紋・
            出した日時・透かしの番号だけで、画像や題名は載りません。記録にお金としての価値は無く、譲渡もできません。
            署名は MetaMask で行い、テスト用の ETH(フォーセットで無料でもらえるもの)を使います。
          </p>
          <p className="wm-warn">
            チェーンの記録は誰でも見られます。アドレスを X などで公開すると、そのアカウントとウォレットが結び付きます。
            本物の資産のあるアカウントではなく、テスト用に作った別のアカウントを使ってください。
          </p>
          {nftNotice && <p className="wm-ok">{nftNotice}</p>}
          {!hasWallet() ? (
            <p className="wm-meta">MetaMask が見つかりません。MetaMask を入れたブラウザで開いてください。</p>
          ) : (
            <div className="wm-nft">
              <p>
                MetaMask: {account ? <code>{account}</code> : 'つないでいません'}{' '}
                <button type="button" className="wm-link" onClick={() => void onConnect()} disabled={nftBusy !== null}>
                  {account ? 'つなぎ直す' : 'MetaMask につなぐ'}
                </button>
              </p>
              {nft?.contract ? (
                <p>
                  記録の契約:{' '}
                  {nft.contract.url
                    ? <a href={nft.contract.url} target="_blank" rel="noreferrer"><code>{nft.contract.address}</code></a>
                    : <code>{nft.contract.address}</code>}
                  <span className="wm-meta">(持ち主 <code>{nft.contract.owner}</code>)</span>
                </p>
              ) : (
                <p>
                  まだ記録の契約がありません(初回だけ作ります)。{' '}
                  <button type="button" className="wm-primary" onClick={() => void onDeploy()} disabled={nftBusy !== null}>
                    {nftBusy === 'deploy' ? '作っています…' : '記録の契約を作る'}
                  </button>
                </p>
              )}
              {nft?.contract && (
                <p>
                  まだ発行していない画像: {pending?.total ?? 0} 枚{' '}
                  <button type="button" className="wm-primary" onClick={() => void onMint()}
                    disabled={nftBusy !== null || !pending?.items.length}>
                    {nftBusy === 'mint' ? '発行しています…' : `${Math.min(pending?.total ?? 0, nft.max_batch)} 枚を発行する`}
                  </button>
                  {(pending?.total ?? 0) > nft.max_batch && <span className="wm-meta">(1回に {nft.max_batch} 枚ずつ)</span>}
                </p>
              )}
            </div>
          )}
        </section>

        <section className="wm-section">
          <h2>台帳 <span className="wm-meta">{marks.length} 件</span></h2>
          {marks.length === 0 ? (
            <p className="wm-meta">まだありません。X に投稿するか、出版物を作ると記録されます。</p>
          ) : (
            <ul className="wm-list">
              {marks.map(mark => (
                <li key={mark.id}>
                  <img src={thumbUrl(mark.source_path, 240)} alt={mark.label} loading="lazy" />
                  <div>
                    <p><code>{markText(mark.mark_id)}</code> ・ {mark.purpose_label}</p>
                    <p className="wm-meta">{formatDate(mark.created_at)} ・ {mark.width}×{mark.height}</p>
                    {mark.label && <p className="wm-label">{mark.label}</p>}
                    {mark.x_post_url && <a href={mark.x_post_url} target="_blank" rel="noreferrer">X で見る</a>}
                    {mark.publication_title && <p className="wm-meta">出版物: {mark.publication_title}</p>}
                    {mark.nft?.token_url && <a href={mark.nft.token_url} target="_blank" rel="noreferrer">NFT ✓</a>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </main>
    </div>
  )
}
