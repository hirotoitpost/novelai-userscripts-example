# うごイラ: 動画モデルで動かす(リモートの GPU)

うごイラの作り方 **F**。元の絵から、動画モデル **Wan 2.2**(14B、画像から動画)に動画を作らせ、コマにします。体・髪・表情が
いっしょに、なめらかに動きます(A〜E は、部分の描き直し・変形・部位ごとの動きでした)。代わりに、細部は元の絵から少し
変わることがあります。

この PC には Wan を動かせる GPU が無いので、**Colab か GCP の GPU で ComfyUI を動かし、アプリからその API を呼びます**。

```
アプリ(この PC)                                   GPU のマシン(Colab / GCP)
  うごイラの画面 ─ POST /api/ugoira/video            cloudflared のトンネル(https://….trycloudflare.com)
     │                                                   │
  video_remote.py ── 元の絵・ワークフロー ──────────▶ comfy_auth_proxy.py(合言葉を確かめる :8189)
     ▲                                                   │
     └──────────── コマ(PNG)────────────────────── ComfyUI(:8188、マシンの中だけ)+ Wan 2.2
  コマを保存 → 中割り(C)・書き出し・漫画への嵌め込みは今までどおり
```

- 作ったうごイラは、ほかの作り方と同じ一覧に入ります。中割り(C)・並べ替え・Pixiv 用 ZIP / GIF / アニメ WebP の書き出し・
  漫画 v2 のコマへの嵌め込みも、そのまま使えます
- 外のマシンへ絵を送る前に、ほかの作り方と同じ安全の判定(未成年キャラ)を通します
- **まだ実機の GPU では試していません**(アプリ側は、偽の ComfyUI でテストしています)。試したら、結果を `docs/trials/` に残してください

## 作り方の選び方

| 画面の選択 | ワークフロー | 向くもの |
|---|---|---|
| 繰り返す(既定) | `assets/comfyui/wan22_loop_api.json`(最初と最後を元の絵にする) | うごイラ。繰り返しても継ぎ目が出ない。最後のコマ(元の絵と同じ)は外す |
| 自由に動かす | `assets/comfyui/wan22_i2v_api.json`(元の絵から始める) | 振り向く・歩き出すなど、元に戻らない動き |

- 長さは 2・3・5 秒(16fps。33・49・81 コマ)。大きさは元の絵の縦横比のまま 832×480 の面積以内(縦長 832×1216 なら 512×752)で
  作り、受け取ってから元の絵の大きさに戻します
- 動きの説明は **英語の自然な文** が効きます(例: `she blinks slowly, her hair sways gently in the wind, subtle breathing`)。
  元の絵のプロンプト(NovelAI の重みは外す)をあとに足します
- ネガティブは、管理ページの「うごイラ(動画モデル)」で変えられます
- 中割りを足すと 32fps になります(この PC の CPU で。コマが多いので少し時間がかかります)

## アプリの設定(.env)

```env
VIDEO_GEN_BASE_URL=https://xxxx-xxxx.trycloudflare.com   # GPU のマシンの URL(Colab のノートブックの最後に出る)
VIDEO_GEN_TOKEN=…                                         # 合言葉(GPU のマシンと同じもの)
# ワークフローを差し替えるとき(任意。ComfyUI の「Export (API)」の JSON の値を {{prompt}} などに書き換えたもの)
# VIDEO_GEN_WORKFLOW_LOOP=assets/comfyui/my_loop.json
# VIDEO_GEN_WORKFLOW_I2V=assets/comfyui/my_i2v.json
```

書いたらバックエンドを起動し直します。うごイラの画面の「動画モデルで動かす」に「つながりました(GPU の名前)」と出れば使えます。

ワークフローの中で値にする文字: `{{image}}` `{{prompt}}` `{{negative}}` `{{seed}}` `{{width}}` `{{height}}` `{{length}}` `{{prefix}}`。
出力は `SaveImage`(1コマずつの PNG)のほか、アニメ WebP・GIF でも受け取れます(コマに分けます)。

## A. Colab で動かす(試すとき・ときどき使うとき)

1. `notebooks/comfyui_wan_colab.ipynb` を Colab で開く(GitHub から開くか、アップロード)
2. ランタイムのタイプを **L4**(か A100)にする。L4 は 24GB で、14B の fp8 を 480p で動かせる下限くらいです
3. 上から順に実行する(はじめはモデル約 35GB の取得で 5〜10 分)
4. 最後のセルに出た 2 行を `.env` に書く
5. 終わったら「ランタイムを接続解除して削除」。**開いたままだと、使っていなくてもユニットが減ります**

Colab の URL は起動のたびに変わるので、そのたびに `.env` を書き換えます。

## B. GCP の Spot VM で動かす(たくさん作るとき)

使うときだけ VM を起動し、`idle-minutes` の分数(既定 30)頼まれなかったら VM が自分で止まります。

```bash
TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
echo "$TOKEN"   # .env の VIDEO_GEN_TOKEN に書く

# Deep Learning VM のイメージ(NVIDIA のドライバ入り)の名前を探す
gcloud compute images list --project deeplearning-platform-release --filter="family~pytorch" --format="value(family)" | sort -u

gcloud compute instances create comfy-wan \
  --zone=asia-northeast1-a \
  --machine-type=g2-standard-8 \
  --provisioning-model=SPOT --instance-termination-action=STOP --maintenance-policy=TERMINATE \
  --image-project=deeplearning-platform-release --image-family=<上で見つけた family> \
  --boot-disk-size=100GB --boot-disk-type=pd-balanced \
  --metadata=install-nvidia-driver=True,comfy-token=$TOKEN,idle-minutes=30 \
  --metadata-from-file=startup-script=scripts/gcp/comfyui_setup.sh
```

- `g2-standard-8` は L4 が1枚付いた型です。A100 なら `a2-highgpu-1g`、H100 なら `a3-highgpu-1g`(Spot か Flex-start)
- 初回の起動では、ComfyUI とモデルを入れます(15〜30 分)。2回目からは起動だけです
- URL は起動のたびに変わります。シリアルポートの出力で見ます:
  `gcloud compute instances get-serial-port-output comfy-wan --zone=asia-northeast1-a | grep trycloudflare.com`
- 次に使うときは `gcloud compute instances start comfy-wan --zone=asia-northeast1-a`
- GPU のクォータは初めは 0 です。「IAM と管理 → 割り当て」で、使うリージョンの GPU を申請します
- URL を固定したいときは、メタデータに `tunnel=none` を入れ、Tailscale などで VM の 8189 番につなぎます(合言葉は要ります)

## 費用の目安

| かかるもの | 目安 |
|---|---|
| Colab Pro | 月 $9.99 で 100 ユニット。L4 は 1時間 約 1.7 ユニット(実測の一例。時期・割り当てで変わる) |
| GCP の L4(g2) | オンデマンド 1時間 約 $0.7。Spot はその半分以下(いつ止められてもよい作業向け) |
| GCP のディスク | 100GB の pd-balanced で月 $10 前後(VM を止めていてもかかる。使わなくなったら VM ごと消す) |
| 通信 | 送る絵1枚は受信なので無料。受け取るコマは 832×480 の PNG 49 枚で約 30MB(インターネットへの送信は 1GB あたり約 $0.12) → 1本 1円もしない |

料金は変わるので、使う前に Google の料金ページで確かめてください。いちばん大きいのは **GPU の消し忘れ** です
(Colab はランタイムの削除、GCP は idle-minutes での自動停止)。

## 安全

- ComfyUI には認証が無いので、マシンの中(127.0.0.1)だけで待たせ、外へは `scripts/comfy_auth_proxy.py`(Bearer トークンを
  確かめる中継)だけを出します。トンネルの URL を知られても、合言葉が無ければ使えません
- 合言葉は `.env`(git に入らない)にだけ書きます。漏れたら、GPU のマシンを作り直して合言葉を替えます
