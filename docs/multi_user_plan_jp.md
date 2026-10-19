# 複数ユーザー対応(.env の利用者情報の安全化)の計画

今は 1 人の利用者を前提に、NovelAI のトークンなどを `.env` に置いている。これを、利用者ごと・安全な形式で扱える
一般的なサービスの形にするための計画。実装はこの計画に沿って、ステップごとに PR を分けて進める。

## 方針

`.env` の項目を「誰の情報か」で仕分ける。

- **サーバー全体の設定**は、運営者が用意するものとして `.env`(ホスティングするなら GCP Secret Manager など)に残す。
- **利用者ごとの情報**は `.env` から外し、DB に暗号化して保存するか、ログインのセッションで扱う。

そのうえで、DB とファイルを利用者ごとに分ける。

## 現状: 1 人用になっている箇所

| 箇所 | 今の動き | 複数人で使うと起きる問題 |
|---|---|---|
| `src/python/client.py` の `api_keys_with_fallback` | 画面のトークンが 401 になると、`.env` の `NOVELAI_API_TOKEN` で再試行する | **他の人のリクエストが、運営者の NovelAI アカウントと Anlas で処理される**(最も危険) |
| `src/python/client.py` の `get_client` | Authorization ヘッダーがないと、`.env` のトークンで作った共通のクライアントを使う | ログインしていない人でも生成できてしまう |
| `src/python/db.py` | `data/app.db` という 1 つの SQLite に約 45 個のテーブル。どれも `user_id` を持たない | 全員のチャンク・物語・キャラ・履歴が混ざる |
| `outputs/`、`data/imports/` など | 画像の保存先が全員共通 | 他の人の画像が見える、上書きされる |
| `src/python/guard_password.py`、`src/python/app_settings.py` | ガードのパスワードとアプリ設定がそれぞれ 1 つ | 1 人が変えると全員に効く |
| `src/python/routes/admin.py` の `/api/admin/env` | LAN からなら `.env` を読み書きできる | ホスティングすると、秘密の書き換え口になる |
| `src/python/mcp_server.py` | `.env` のトークンで NovelAI に接続する | 利用者を区別できない |
| `src/javascript/context/AuthContext.tsx` | NovelAI のアクセストークンをそのまま localStorage に保存する | XSS があるとトークンが盗まれる |
| `src/javascript/hooks/useChunkSync.ts`、`src/javascript/pages/Story.tsx` | keystore を復号する鍵(メール+パスワードから作る)を localStorage の `nai_encryption_key` に保存し、`/api/chunks/promptmacros?encryption_key=...` のように URL のクエリで送る | XSS で盗まれる。URL はアクセスログや中継に残りやすい。この鍵は永続トークンでは代わりにならない |
| `src/python/server.py` | CORS が `allow_origins=["*"]` | 公開すると危ない |

## `.env` の項目の仕分け

| 区分 | 項目 | 置き場所 |
|---|---|---|
| サーバー全体の設定 | `VLLM_*`、`VIDEO_GEN_*`、`HF_TOKEN`、`PUSH_CONTACT`、VAPID 鍵(`data/vapid_private.pem`) | `.env` / Secret Manager(運営者が用意) |
| サーバー全体の設定(セルフホストのみ) | `PATH_ADD_UV`(開発用 PC の PATH の補い) | `.env` |
| サーバー全体の設定(新規) | セッション署名鍵、トークン暗号化用のマスター鍵 | Secret Manager(`.env` はセルフホストのみ) |
| 利用者ごとの情報 | `NOVELAI_API_TOKEN`(永続トークン `pst-...`)、ログインで得るアクセストークン | DB に暗号化して保存 |
| テスト専用 | `NOVELAI_TEST_EMAIL`、`NOVELAI_TEST_PASSWORD`、`NOVELAI_SESSION_TOKEN`(使っているのは `scripts/inspect_promptmacros.py` などの調査用スクリプトだけ) | GitHub Actions の Secrets |

## ステップ(1 ステップ = 1 PR を想定)

### ステップ 0: 運営形態を決める

- 選択肢は 2 つ。
  - **A. セルフホスト**: 各自が自分の PC やサーバーで動かす。
  - **B. マルチテナント**: 1 つのサーバーを複数人で共有する。
- A なら、ステップ 1 と 4 だけで「安全な形式」はほぼ満たせる。
- B の場合、他人の NovelAI の認証情報をサーバーで預かることになる。NovelAI の利用規約と、成人向けコンテンツの
  扱いの責任も確認が必要。

### ステップ 1: 運営者のトークンへの切り替えを止める(最優先・小さい変更)

- `APP_MODE=single|multi` を加える(既定は `single`)。
- `multi` のときは、`api_keys_with_fallback` が `.env` のトークンを足さないようにする。
- `get_client` の共通クライアントも使わない(Authorization がなければ 401)。
- 今の使い方は `single` のまま変わらない。
- 注意: この切り替えは、画面のログインのトークンが期限切れで 401 になったとき(2026-10 実機: スマホから文章・画像の
  生成がどちらも 401)に続けられるよう入れたもの。`multi` で止めると、ステップ 2 で利用者ごとの永続トークンを
  登録できるようになるまでは、この 401 がまた起きる。`api_keys_with_fallback` の呼び出し元は 11 か所あるので、
  切り替えの判断は関数の中の 1 か所で行う。
- 実装済み: `client.py` の `app_mode()` / `operator_token()` で判断する。`.env` のトークンを読んでいた
  `routes/manga_import.py` の `_optional_api_key` もこれを使う。`single`・`multi` 以外の値では起動しない
  (書き間違いを `single` として通さないため)。管理ページのトークン状態の表示(`routes/admin.py`)と MCP は
  ステップ 4・5 で扱う。

### ステップ 2: アプリ独自のログインとセッション

- `users` テーブルを作る。
- 今の NovelAI ログイン(`auth_utils.py`)の後に、NovelAI のアカウント情報から利用者を特定し、アプリ用の
  セッションを発行する。セッションは HttpOnly・Secure・SameSite 付きの Cookie で渡す。
- NovelAI のトークンはブラウザに置かず、サーバーに暗号化して保存する。暗号化には既に依存に入っている PyNaCl の
  SecretBox を使い、マスター鍵は Secret Manager に置く(利用者ごとのデータ鍵をマスター鍵で包む形)。
- 利用者は画面から永続トークンを登録できるようにする。これが `.env` の `NOVELAI_API_TOKEN` の代わりになる。
- `get_client` を「セッションの利用者 → 復号したトークン → クライアント」の流れに変える。
- keystore の復号鍵は別に扱う。この鍵はメール+パスワードから作るもので、永続トークンでは代わりにならない。
  使うのは公式のプロンプトのチャンクの取り込み(`routes/chunks.py`)と公式サイトの物語の一覧(`routes/story.py`)の
  2 つだけ。
  - URL のクエリで送るのをやめ、リクエストの本文で送る。
  - localStorage に置くのをやめる。サーバーのセッションに結びつけて持つ(期限付き)か、その 2 つの機能を
    使うたびにパスワードを入れてもらう。

### ステップ 3: データを利用者ごとに分ける

- 利用者が持ち主になるテーブルに `user_id` を加える。既存のデータは最初の利用者のものとして移行する。
- すべてのクエリに `user_id` の条件を加える。漏れを防ぐため、`db.py` に利用者を必ず受け取る関数を挟む。
- 画像の保存先は `outputs/{user_id}/` に分ける(`data/imports/` なども同様)。
- `app_settings`、`guard_secret`、`push_subscriptions`、`backup_config` は利用者ごとにする。
- 本格的に公開するなら、PostgreSQL の行レベルセキュリティへの移行も検討する。

### ステップ 4: 管理者機能と設定の扱い

- `/api/admin` の許可の基準を「LAN からのアクセスか」から「管理者ロールを持つか」に変える。
- `multi` では `.env` の編集 API(`PUT /api/admin/env`)を無効にする。
- 設定は pydantic-settings(新しく依存に加える)で読み、シークレットは Secret Manager から入れる。
- CORS は、実際の配信元だけに絞る。

### ステップ 5: MCP サーバーとジョブ

- MCP は `.env` を使うのをやめ、アプリが発行する利用者ごとのアクセストークンで接続する。
- バッチや動画生成のジョブに `user_id` を持たせ、他の人のジョブを見たり止めたりできないようにする。

### ステップ 6: 運用

- 運営者の負担になる GPU(vLLM や ComfyUI)には、利用者ごとの上限と回数制限を設ける。
- 監査ログを残す。
- 利用者ごとのエクスポートと削除を用意する。
- `docs/architecture.md` と `SECURITY.md` を更新する。

## 進め方

- まずステップ 1 から着手する。差分が小さく、今の挙動は変わらず、一番大きな問題(運営者のトークンへの切り替え)を
  先に閉じられる。
- ステップ 2 以降は、ステップ 0 の決定(セルフホストかマルチテナントか)を受けて範囲を確定する。
