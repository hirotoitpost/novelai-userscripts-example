# Docker で動かす

アプリ本体(バックエンドとビルドした画面)を Docker のコンテナで動かす方法です。まずは開発している Windows PC の
Docker Desktop で動かす前提です。ほかのマシンへ段階的に移す予定です。

開発はこれまでどおり `scripts/dev-ctl.ps1`(Vite :5173 + バックエンド :8000)で行います。Docker は「配る・動かす」用です。

## 構成

```
docker compose
├─ app     … バックエンド + ビルドした画面(:8000。画面と API を同じ場所から配る)
└─ ollama  … LLM(GPU。前からあるもの)
```

| コンテナの中 | ホスト | 中身 |
|---|---|---|
| `/app/data` | `./data` | DB・モデル・取り込んだ作品・素材・ごみ箱 |
| `/app/outputs` | `./outputs` | 生成画像・漫画のコマとページ・データセット |
| `/app/.env` | `./.env` | 設定(管理ページから書き換えられる) |
| `/backup` | `./backups`(`BACKUP_DIR` で変えられる) | 画像・作品データのバックアップの写し先 |
| `/windows-fonts` | `C:/Windows/Fonts`(読み取り専用) | 吹き出し・描き文字の日本語フォント |

`data/` と `outputs/` は、開発中のサーバーと同じフォルダを使います。

## 起動・停止

開発中のサーバーを止めてから起動します。同じ DB を使い、ポート 8000 も同じなので、同時には動かせません。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/dev-ctl.ps1 stop -Target all
docker compose --profile app up -d --build      # app と ollama を起動(初回はイメージを作る。1〜2分)
```

開くのは `http://localhost:8000`(LAN の端末からは `http://192.168.0.9:8000` か `http://novelai.lan:8000`)です。

```powershell
docker compose --profile app logs -f app        # ログ(管理ページの「ログ」でも見られる)
docker compose --profile app restart app        # 起動し直す(管理ページの再起動ボタンは使えない)
docker compose --profile app stop app           # 止める(開発に戻るときは、このあと dev-ctl.ps1 start)
```

コードを変えたら `--build` を付けて起動し直すと、イメージを作り直します。

`app` は profile `app` に入れてあります。今までどおり `docker compose up -d` だけなら、ollama だけが起動します。

起動のたびに `The "env" variable is not set` という警告が出ますが、問題ありません。docker compose が `.env` の
`$env:…`(PowerShell の書き方)を自分の変数として読もうとして出るもので、アプリは `.env` を自分で読みます。

## 開発中との違い

| 項目 | 開発中(dev-ctl.ps1) | Docker |
|---|---|---|
| 画面 | Vite :5173(API は :8000) | バックエンドが :8000 で配る(`SERVE_FRONTEND=1`) |
| LLM の場所 | `.env` の `VLLM_BASE_URL`(localhost:11434) | `http://ollama:11434/v1`(compose の `environment` が `.env` より優先) |
| フォント | `C:/Windows/Fonts` | 読み取り専用で渡した Windows のフォント + Noto CJK + 取ってきたフォント |
| 起動し直し | 管理ページのボタン | `docker compose --profile app restart app` |
| 起動の記録・ログ | `data/run/` | `data/run/container/` |
| https(スマホのインストール・通知) | `dev-ctl.ps1 -Https` | まだ無い(http だけ) |

Windows のフォントを渡すのは、同じ PC の中で使うときだけにしてください(フォントのライセンスのため)。ほかのマシンへ
移すときは、`WINDOWS_FONTS` を空のフォルダにします。その場合は Noto CJK と、アプリのフォント一覧から取ってきた
フォントになります。

## 移すとき(これから)

- `data/` と `outputs/` を移し先へ写す(または、画像・作品データのバックアップから戻す)
- https は、コンテナの前にリバースプロキシ(Caddy など)を置いて付ける予定
- LAN の外から使えるようにする場合は、ログインと https を作り直してから
