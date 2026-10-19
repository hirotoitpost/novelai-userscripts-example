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
- **決定(2026-10-19): B. マルチテナント。** 使えるのは招待した人だけ。

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

決めたこと(2026-10-19):

- 使えるのは**招待した人だけ**。利用上限(ステップ 6)ができるまでは、運営者の GPU を使える人を絞る。
- 利用者は**ログインのメールアドレス**で見分ける(NovelAI はアカウントを一意に表す ID を返さないため)。
  メールアドレスそのものは保存せず、マスター鍵から作った鍵の HMAC だけを持つ。そのため multi では、
  トークンを貼り付けるログインはできない(永続トークンは、メールでログインした後に画面から登録する)。
- keystore の復号鍵は**セッションの間だけサーバーに持つ**。ログインのときにパスワードから作り、暗号化して
  セッションに置く。ログアウト・期限切れ・招待の取り消しで消える。

2a(バックエンド)と 2b(画面)の 2 つの PR に分ける。

#### 2a: バックエンド(実装済み)

- `src/python/sessions.py` にまとめた。テーブルは `user_invites`・`users`・`user_sessions`。
- 鍵: `.env` の `APP_MASTER_KEY`(32 バイトの base64。ホスティングでは Secret Manager)。ここから用途ごとの鍵を
  作る(メールアドレスの HMAC 用、データ鍵を包む用)。利用者ごとのデータ鍵(PyNaCl の SecretBox)をマスター鍵で
  包んで `users.wrapped_key` に置き、トークン・keystore の鍵はデータ鍵で暗号化する。multi でマスター鍵が
  なければ起動しない。マスター鍵を変えると、招待も利用者も見分けられなくなる。
- 招待: `scripts/manage_users.py add|remove|list`(サーバーの PC でだけ。画面からはできない)。招待を取り消すと
  その人のセッションも消える(データは残す)。
- ログイン(`POST /api/auth/login`): 招待されていなければ、NovelAI へログインしに行かずに 403。通れば
  セッションを作り、合言葉を `nai_session` の Cookie(HttpOnly・SameSite=Strict、https なら Secure、30 日)で
  渡す。DB には合言葉の SHA-256 だけを置く。画面にはトークンを返さない。
- `GET /api/auth/session`(運営形態とログインの状態)、`POST /api/auth/logout`、
  `PUT`/`DELETE /api/auth/api-token`(永続トークン `pst-...` の登録・削除)。
- `SessionMiddleware`: multi では `/api/` へのリクエストにセッションを求める(ログインの道を除く)。ブラウザから
  来た Authorization は捨て、その人の NovelAI トークン(永続トークンがあればそれ、なければログインのトークン)を
  差し込む。今ある各ルーター(`get_client` など)は手を入れずに本人のトークンで動く。永続トークンを登録すれば、
  ステップ 1 で戻った「期限切れの 401」も起きない。
- 書き込みのリクエストで Origin がこのサーバーと違えば 403(Vite の中継では X-Forwarded-Host と比べる)。
- CORS: Cookie を使うので、multi では `APP_ALLOWED_ORIGINS`(カンマ区切り)に書いた配信元だけにした
  (ステップ 4 の CORS の項目を前倒し)。書かなければ同じ配信元だけ。single は今までどおり。
- keystore を読む 2 つの機能(`/api/chunks/promptmacros`・`/api/story/remote`)は、multi ではセッションの
  ログインのトークンと復号鍵を使う(永続トークンでは keystore を読めない)。`encryption_key` のクエリは
  single のためだけに残す。

注意: ステップ 3 までは、招待した人どうしでデータ(物語・キャラ・画像など)が共有される。ステップ 3 が
終わるまでは、データを見られてもよい人だけを招待する。

#### 2b: 画面(実装済み)

- `AuthContext.tsx`: 開いたときに `GET /api/auth/session` で運営形態を確かめる(聞けないときは single として動く)。
  multi では NovelAI のトークンも keystore の鍵も localStorage に置かない(single のときの残りも消す)。
  ログインの状態はサーバーのセッションで判断する。
- 各画面の API の呼び出し(`Bearer ${token}` を 29 か所で組み立て、「トークンがあれば使える」という判断が 50 か所
  以上)は書き換えない。multi では `token` に秘密ではない目印(`SESSION_TOKEN`)を入れ、サーバーは来た
  Authorization を捨てて本人のトークンに差し替える(2a の `SessionMiddleware`)。
- 開発中は、画面(:5173)から :8000 へ直接送る呼び出しが 101 か所ある(Vite の中継だと SSE が詰まるため)。
  別の配信元なので、multi のときだけ `backendOrigin.ts` で `credentials: 'include'` を付けて Cookie を送る。
  バックエンドでは `.env` の `APP_ALLOWED_ORIGINS` にその配信元(`http://localhost:5173` など)を書く
  (CORS と、書き込みの Origin の確認の両方で使う)。バックエンドが画面も配る形(Docker)では同じ配信元なので要らない。
- ログインの画面: multi ではトークンだけのログインは出さない。ログアウトは `POST /api/auth/logout`。

#### 2c: reCAPTCHA を使わないログイン(実装済み)

- 実機で確かめたところ、メールアドレス+パスワードのログインは通らなかった(2026-10-19。`Login failed (400):
  Please refresh NovelAI.net`)。このアプリが借りている reCAPTCHA の鍵は novelai.net でしか使えないので、
  localhost でも LAN のアドレスでも NovelAI に断られる(single でトークンの貼り付けや `.env` の永続トークンを
  使っていたのはこのため)。
- multi のログインは、メールアドレス・パスワードに、novelai.net で取ったアクセストークンを添える。サーバーは
  そのトークンで keystore を読み、メールアドレス+パスワードから作った鍵で開けるかを見る(`auth_utils.py` の
  `verify_token_owner`)。keystore はその鍵でしか開けないので、開ければトークン・メールアドレス・パスワードが
  そろって本人のもの。開けた鍵はそのままセッションに持つ。
- 確かめられないもの: 永続トークン(pst-。keystore を読めない)、keystore が空のアカウント(novelai.net で物語を
  1 つ保存すればできる)。
- トークンを添えないときは、今までどおり reCAPTCHA でログインを試す。
- ホームに永続トークンの登録・置き換え・削除(`components/ApiTokenSettings.tsx`。multi のときだけ)。
- keystore の 2 つの機能: multi では鍵の入力欄を出さず、セッションの鍵を使う。single でも、鍵は URL のクエリ
  ではなく `X-NAI-Encryption-Key` ヘッダーで送る(サーバーは前の画面のためにクエリも受け付ける)。

### ステップ 3: データを利用者ごとに分ける

決めたこと(2026-10-19):

- `user_id` の列を足すのではなく、**利用者ごとの DB とフォルダ**にする。DB を開くときに今の利用者の DB を選ぶだけ
  なので、382 か所のクエリはそのまま使え、条件の付け忘れでほかの人のデータが見えることもない。PostgreSQL に移る
  ときは作り直しになる。
- 今のデータ(single の `data/app.db`・`outputs/` など)は、**運営者の利用者のもの**にする(コピー。元は消さない)。

3a(DB)と 3b(ファイル)の 2 つの PR に分ける。

#### 3a: 利用者ごとの DB(実装済み)

- `db.get_connection()` は、今の利用者の DB(`data/users/{id}/app.db`)を開く。利用者は `SessionMiddleware` が
  `ContextVar` に入れる(リクエストから切り離したタスク・スレッドにも引き継がれる)。multi で利用者が決まって
  いなければ `NoUserError`(運営者の前のデータを誤って開かない)。single は今までどおり `data/app.db`。
- 招待・利用者・セッション(`sessions.py`)と、バックアップの設定(`backup.py`)は全体の DB
  (`get_system_connection()`。multi でも `data/app.db`)。
- アプリの設定・コンテンツガードの読み込みのキャッシュは、今の利用者の DB で読み直す。ごみ箱は DB のとなり
  (`data/users/{id}/trash`)。
- メモリの中のジョブの一覧で DB の ID をキーにしているもの(物語・漫画の取り込み)は、`db.PerUser` で
  (利用者, ID) をキーにする(利用者ごとに ID が 1 から振られてぶつかるため)。
- バックアップは、利用者ごとの DB も壊れない写しで入れ、戻すときも入れ替える。
- 運営者の前のデータを渡す: 一度ログインしてから `scripts/manage_users.py adopt <メールアドレス>`
  (`data/app.db` と `data/trash` をその人の DB・ごみ箱にコピー。ログインの情報は写さない。すでにその人の DB が
  あれば `--force` がないと断る)。
- 3b までの注意: 画像などのファイルはまだ全員で共有している。管理ページの「使っていない画像の片付け」は、
  ほかの人の画像を消してしまうので multi では止めた(409)。
- 残り: 管理ページの DB の写し・大きさの表示は全体の DB のまま(ステップ 4)。MCP サーバーは multi では
  利用者が決まらないので DB を開けない(ステップ 5)。

#### 3b: 利用者ごとのフォルダ(実装済み)

- `db.user_root()`: 今の利用者のファイルを置くフォルダ。single はプロジェクトのフォルダ、multi はその人の DB の
  となり(`data/users/{id}/`)。multi で利用者が決まっていなければ `NoUserError`(共有の場所へ書かない)。
- 利用者のデータを扱うモジュールの `_PROJECT_ROOT` を `db.user_files()`(`UserPath`)にした。使うたびに今の
  利用者のフォルダを指すので、そこからつないだフォルダの定数(`_MANGA_DIR`・`_HISTORY_DIR`・`STAMP_DIR`・
  `PUBLICATIONS_DIR`・`_IMPORT_DIR` など)も、DB に入っている相対パス(`outputs/manga/...`)も、使う側を書き
  換えずにその人のフォルダで読み書きする(テストで本物の Path に差し替えるのもそのまま)。
  - 対象: 画像の生成の履歴、漫画、ギャラリー・本棚、キャラ別データセット、作品の取り込み、出版、うごイラ、
    ごみ箱、作品・キャラシートの書き出し、描き文字のスタンプ(利用者が素材集から取り込むもの)。
  - サーバー全体のもの(今の場所のまま): モデル、フォント(配布元から落とす書体)、証明書、ログ、DB の
    バックアップ、動画のワークフロー。
- 管理ページの片付けと容量の表示は、その人のフォルダを見る(片付けを multi でも使えるように戻した)。
- 運営者の前のファイルを渡す: `scripts/manage_users.py adopt <メールアドレス>` で DB と一緒に `outputs/`・
  `data/imports/`・`data/stamps/` もコピーする。DB を先に写してあるときは `--files-only`。

### ステップ 4: 管理者機能と設定の扱い

- `/api/admin` の許可の基準を「LAN からのアクセスか」から「管理者ロールを持つか」に変える。
- `multi` では `.env` の編集 API(`PUT /api/admin/env`)を無効にする。
- 設定は pydantic-settings(新しく依存に加える)で読み、シークレットは Secret Manager から入れる。
- CORS は、実際の配信元だけに絞る(multi はステップ 2a で済み)。

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
