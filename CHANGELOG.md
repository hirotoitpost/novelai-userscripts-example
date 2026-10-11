# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **入力値をキャラシートに反映** — 画像生成ページの入力(プロンプト・キャラごとのプロンプト・ネガティブ・シード値)や、
  メタデータ管理ページで読んだ画像の設定を、キャラシートに反映できる。プロンプトを容姿・服装・画風に分けた下書きを
  確かめて、新しいキャラとして追加するか、既存のキャラに上書きする(反映するのはチェックした項目だけ)

### Planned

- Inpainting UI
- Character Reference UI
- Vibe Transfer / ControlNet UI
- Batch generation UI

---

## [0.8.0] - 2026-10-11

### Added

- **物語と漫画(漫画v2)** — 物語エディタから漫画まで。コマ単位で絵を生成し、コマ割り・吹き出し・ナレーション・描き文字を
  合成してページにする。漫画ドラフト(大枠シナリオ → 台本 → 漫画)、斜めのコマ・枠なし・裁ち切り・見開きにも対応
- **ギャラリーと本棚** — 生成した画像と、物語・漫画を一覧して読む。全画面表示(ズーム・スワイプ・慣性つき)、
  ブックマーク、成人向けの区分とぼかし、シリーズ・作者・似ている作品
- **作品の取り込み** (`/manga-import`) — PDF・画像・zip からコマ割りと構成を読み取る。取り込むときに使い方を選ぶ:
  - 似た漫画を作る — コマ運び・場面・所作を再現し、話とセリフは新しく作る(セリフは型だけを読み、文面は保存しない)
  - 自分の作品を作り直す — セリフの文面も読み、セリフごと台本に起こして描き直す(読み取ったセリフは画面で直せる)
  - 仕様は docs/manga_import_jp.md
- **登場人物をそろえる** — 物語の内容から既存のキャラを割り当て、いなければ作る。参照画像も候補から自動で1枚選ぶ。
  物語をそのまま漫画にする(自動の漫画化)
- **出版(パブリッシュ)** (`/publications`) — ギャラリー・本棚で選んだ画像や本から NovelAI の生成情報(メタデータ)を
  取り除き、出版物として別に保存して ZIP にまとめる。アイテムの表示画面からも出版でき、作成後にダウンロードする
  オプションつき。仕様は docs/publish_jp.md
- **画像から設定を読み込んで生成** — NovelAI の画像を渡すと、プロンプト・キャラごとのプロンプト・モデル・サイズ・
  サンプラー・シード値などを画像生成ページに入れる(メタデータ管理ページからも渡せる)
- **タグ補完** — 画像生成ページのプロンプト欄で、打っている途中のタグから候補を出す。日本語から英語のタグも引ける
- **表情の変更と2倍の拡大** — できた画像の仕上げ(NovelAI のディレクターツール)
- **V5 モデルとキャラの位置の指定** — 画像生成ページで V5 Full / Curated を選べる。キャラごとのプロンプトの位置を
  指定するか、AI にまかせるかを選べる
- **リバースプロンプトの作り直し** — 画像に埋め込まれた設定の読み取りと、WD Tagger による推定。タグごとの確率、
  人物ごとのキャラプロンプト、構図・白黒の判定。検証は docs/trials/2026-10-10_reverse-prompt.md
- **処理状況のトレイ** — 時間のかかる処理を最小化しても、どのページからでも戻れる。再読み込みしても引き継ぐ
- **スマホにインストール(PWA)** — LAN 用の証明書(`scripts/make_lan_cert.py`)で https にして、ホーム画面に追加できる
- **開発管理者のページ** (`/admin`) — サーバーの状態とログ、動いている処理、設定(.env)、再起動、保存領域の掃除、
  DB のバックアップ、既定値・プリセット。LAN の中から使える。設定を変えると、反映に何が要るか
  (PC の再起動 / サーバーの再起動 / すぐ反映)をダイアログで知らせる
- **コンテンツガードの編集** — 止めるタグ・取り除くタグ・足すネガティブ・LLM への指示・成人向けの判定の語を、
  API (`/api/content-guard`) と開発管理者のページで編集できる。仕様は docs/content_guard_jp.md
- **CI** — GitHub Actions で ruff・pytest・フロントのビルドを実行
- **キャラシート** — 登場人物に容姿(固定)・普段の服装・画風・ネガティブ・基準シード・トリガーワードを持たせ、
  物語の挿絵/漫画v2のコマ生成に自動で反映(シード未指定時はキャラの基準シードを使用)
- **キャラ別データセット** (`/character-dataset`) — キャラシートを元にポーズ/服装/表情/場所を差し替えて1枚ずつ生成(全年齢向けのみ)
  - 参照画像との類似度(色の近さ / 画像AIによる採点)が基準未満なら、シードをずらして引き直し、最も似た1枚を採用。外れは `rejected/` に保存
  - 手持ち画像の取り込み(`manual/` にPNG+キャプションで保存)
  - 構図の軸(full body / cowboy shot / from side など)を追加し、組み合わせは各軸の値が均等に出るように選ぶ
  - 参照する範囲(キャラのみ / キャラ＋画風)を選択可能に
  - 既定値を検証結果に合わせて変更: 参照画像オフ(使うときはキャラのみ・強さ0.7)、引き直しなし、横顔は `from side, profile`。
    検証は docs/trials/2026-10-06_character-dataset-stability.md
  - 画像AIの採点は空応答・タイムアウト時に最大3回まで採点し直す
  - キャラ別データセットの画面でキャラの追加とキャラシートの編集(参照画像の登録・差し替えを含む)ができるように。
    編集部分は物語の「登場人物」と共通の部品(`CharacterSheetEditor`)
  - キャラシートの別名保存(参照画像も複製、トリガーワードの重複は拒否)と、使われている物語を示してからの削除
  - 生成枚数を組み合わせの数より多く指定できるように(一巡ごとにシードをずらして繰り返す)。生成ボタンの横でも枚数を指定可能
- **ギャラリーにデータセットを表示** — キャラ別データセットの画像(generated / manual、R18 は成人向け扱い)を「データセット」として一覧。
  削除すると学習用フォルダからも画像とキャプションを消す(確認ダイアログで明示)。rejected/ は出さない
- **キャラの付け替え** — 物語の「登場人物」で、その物語のシーンに割り当てたキャラを別のキャラに一括で付け替え
  - ガードプロファイル — ブロックするタグ/ネガティブを追加・削除して名前を付けて保存し、選んで使用。
    基本のブロックリストは固定で、プロファイルは上乗せのみ
  - R18 — キャラシートで成人フラグを付けたキャラのみ。未成年を示すタグ(school uniform, student 等)は
    キャラシート・バリエーション・キャプションのどこにあっても拒否し、成人フラグも付けられない。
    保存先は `generated_r18/`・`manual_r18/` に分離

### Changed

- **novelai-sdk を 0.8.1 から 0.14.1 に更新**
- **コンテンツガードの定義を DB に移動** — これまで Python の定数だった値を、テーブル `content_guard_rules` で持つ
  (はじめの値は `src/python/content_guard_defaults.json`)。はじめの値のままなら判定は変わらない
- **開発サーバーの既定を http に** — `scripts/dev-ctl.ps1` は何も付けなければ http で起動する。https は `-Https`
  (または環境変数 `NAI_HTTPS=1`)
- **漫画の取り込みで読むものが増えた** — 使い方を選んで取り込むと、場面・所作・セリフも読む。前からある取り込みは
  「構成のみ」として残り、読み取り直すと切り替えられる

### Fixed

- **キャラごとのプロンプトの位置が効いていなかった** — 古い SDK は位置を送っても NovelAI に無視される形だった
  (SDK の更新で解消)
- **UC プリセット「なし」が使えなかった** — SDK の検証で弾かれていたのを、アプリ側で回避 (`src/python/sdk_compat.py`)
- **キャラごとのプロンプトの変更が、生成に反映されないことがあった**

---

## [0.7.1] - 2026-05-11

### Fixed

- **Ollama 対応** — vLLM の代わりに Ollama (llama.cpp ベース) を LLM バックエンドとして使用
  - vLLM 0.20.x は CUDA compute capability 7.5+ が必要 (GTX 10xx / Pascal 世代は対象外)
  - Ollama は Pascal GPU (sm_61) を含む旧世代 GPU で動作
  - `docker-compose.yml` を `vllm/vllm-openai` から `ollama/ollama` へ変更
- **Qwen3-VL 思考モード対応** — `routes/llm.py` を Ollama ネイティブ API に切り替え
  - OpenAI 互換 API では Qwen3 の思考トークン (reasoning) が `content` を空にする問題を回避
  - Ollama ネイティブ `/api/chat` を `think: false` で呼び出し、content フィールドを正常に取得
  - ストリーム完了後に `<think>...</think>` ブロックをレスポンスから除去
  - vLLM バックエンドでは従来通り OpenAI SDK ストリーミングを使用 (後方互換)
- **デフォルトモデル更新** — `Qwen/Qwen2-VL-7B-Instruct` → `qwen3-vl:2b` (Ollama 用)

### Configuration

モデル起動手順（Ollama）:

```bash
docker compose up -d
docker compose exec ollama ollama pull qwen3-vl:2b
```

`.env`:
```
VLLM_BASE_URL=http://localhost:11434/v1
VLLM_MODEL=qwen3-vl:2b
VLLM_VISION_BASE_URL=http://localhost:11434/v1
VLLM_VISION_MODEL=qwen3-vl:2b
```

---

## [0.7.0] - 2026-05-11

### Added

- **AI アシスタント** (`/llm`) — vLLM 経由のローカル LLM を活用した 6 機能のタブ UI
  - **プロンプト整形** — 曖昧な日本語・英語の説明を NovelAI コンマ区切りタグに変換
  - **キャラ設定生成** — キャラクター概念 → ビジュアルタグ JSON（name / positive_tags / negative_tags / notes）
  - **物語ドラフト** — 前提設定 → シーン付き物語文 + 各シーンの生成プロンプト候補
  - **補助テキスト生成** — コンセプト → positive / negative プロンプトの最適ペア
  - **メタデータ生成** — コンセプト → 全生成パラメータ JSON（steps / scale / sampler 等を一括提案）
  - **リバースプロンプト** — アニメ・イラスト画像をアップロード → 再現用プロンプトを逆算（ビジョン LLM）
- **「画像生成に使用」ボタン** — 各パネルの出力を `localStorage` 経由で `/generate` に一括転送
- **「🤖 強化」ボタン** — `/generate` のプロンプト入力欄から直接 AI アシスタントへ往復
- **`docker-compose.yml`** — GPU 1 枚構成（Qwen2-VL-7B-Instruct 単一インスタンスでテキスト+ビジョン両対応）
  - コメントアウトで GPU 2 枚の text/vision 分離構成に切り替え可能
- **`src/python/llm_client.py`** — `openai.AsyncOpenAI` シングルトン（text / vision）、起動時に自動初期化
- **`src/python/llm_models.py`** — 6 機能分の Pydantic リクエストモデル
- **`POST /api/llm/prompt-format`** — プロンプト整形（SSE ストリーミング）
- **`POST /api/llm/char-gen`** — キャラ設定生成（SSE）
- **`POST /api/llm/story-draft`** — 物語ドラフト生成（SSE）
- **`POST /api/llm/aux-text`** — 補助テキスト生成（SSE）
- **`POST /api/llm/metadata-gen`** — メタデータ生成（SSE）
- **`POST /api/llm/reverse-prompt`** — リバースプロンプト（SSE、ビジョンモデル使用）
- `openai>=1.0.0` を Python 依存関係に追加

### Configuration

vLLM を使用するには `.env` に以下を設定してください:

```
VLLM_BASE_URL=http://localhost:8001/v1
VLLM_MODEL=Qwen/Qwen2-VL-7B-Instruct
VLLM_VISION_BASE_URL=http://localhost:8001/v1
VLLM_VISION_MODEL=Qwen/Qwen2-VL-7B-Instruct
HF_TOKEN=hf_...
```

設定なしでもサーバーは起動し、LLM エンドポイントのみ 503 を返します。

---

## [0.6.0] - 2026-05-10

### Added

- **Metadata management UI** (`/metadata`)
  - Drag & drop or click-to-browse file upload (PNG / WebP)
  - Auto-extracts metadata on upload via `POST /api/metadata/extract`; nested JSON
    strings (e.g. NovelAI `Comment` field) are expanded inline for readability
  - Erase controls: target selector (`png_info` / `alpha` / `both`) + erase button
    via `POST /api/metadata/erase`; download cleaned image as `<name>_clean.png`
  - Wired up `/metadata` route and Home dashboard card
- **Image-to-Image mode** on the image generation page
  - Toggle in the sidebar enables img2img controls: reference image upload (drag &
    drop or click), Strength slider (0.01–0.99), Noise slider (0–0.99)
  - Generate button disabled until a reference image is provided while in img2img
    mode
  - Reference image passed to `POST /api/image/generate/stream` via the existing
    `i2i` field; streaming preview works as in text-to-image mode

---

## [0.5.0] - 2026-05-10

### Added

- **Anlas cost preview** – estimated Anlas consumption shown below the generate button
  - Calls `POST /api/image/anlas` with 500 ms debounce whenever parameters change
  - Displays "推定消費: N Anlas"; silently suppressed on network error
- **SSE streaming generation** – live intermediate preview during image generation
  - Switched from `POST /api/image/generate` to `POST /api/image/generate/stream`
  - Uses `fetch` + `ReadableStream` to consume SSE (supports `Authorization` header,
    unlike `EventSource`)
  - Intermediate frames render progressively in the canvas with a spinning overlay badge
  - Final frame replaces the preview automatically when generation completes

---

## [0.4.0] - 2026-05-10

### Added

- **Settings persistence** – all generation parameters saved to `localStorage`
  - Prompt, negative prompt, model, size, steps, scale, UC preset, and quality toggle
    persist across page visits and browser restarts
  - Text inputs debounced at 300 ms to avoid excessive writes; discrete controls write
    immediately
  - Seed intentionally excluded (random per generation is the expected UX)
- **`src/javascript/hooks/useLocalStorage.ts`** – generic debounced `localStorage`
  sync hook; flushes pending timer on component unmount

---

## [0.3.0] - 2026-05-10

### Added

- **Image generation page** (`/generate`) with full parameter control
  - Prompt / negative prompt textareas
  - Model selector (V4.5 Full/Curated, V4 Full/Curated, V3)
  - Size selector (Portrait, Landscape, Square, Large variants)
  - Steps (1–50) and Scale (0–10) sliders
  - Seed input, UC preset selector, quality tag toggle
  - Ctrl+Enter keyboard shortcut to trigger generation
  - Download and re-generate buttons on result
- **`src/javascript/api.ts`** – authenticated `fetch` utility (Bearer token injection)
- **Home dashboard** now links to the image generation page

### Changed

- Home page: 画像生成 card is now clickable and navigates to `/generate`

---

## [0.2.0] - 2026-05-10

### Added

- **NovelAI account login** (email + password)
  - `src/python/auth_utils.py`: argon2id key derivation matching the official client
  - `POST /api/auth/login` endpoint (`src/python/routes/auth.py`)
- **React frontend** with routing (`react-router-dom`)
  - Login page with NovelAI dark theme
  - Home dashboard with feature overview
  - Protected routes (redirect to `/login` when unauthenticated)
- **Dynamic Bearer token support** – every API request uses the token from the
  `Authorization` header; falls back to `NOVELAI_API_TOKEN` env var
- **Vite proxy** – `/api/*` forwarded to `http://127.0.0.1:8000`
- `argon2-cffi` and `httpx` added to Python dependencies

### Security

- Prompts and generated images live in browser memory only; never persisted
- Raw password discarded immediately after key derivation
- Access token stored in `localStorage` (browser-local, cleared on logout)
- `outputs/` and `samples/` excluded from git (binary files / third-party HTML)

---

## [0.1.0] - 2026-05-06

### Added

- **FastAPI relay server** (`src/python/`) wrapping novelai-sdk
  - `POST /api/image/generate` – image generation
  - `POST /api/image/generate/stream` – SSE streaming generation
  - `POST /api/image/anlas` – Anlas consumption estimate
  - `POST /api/metadata/extract` – PNG metadata extraction
  - `POST /api/metadata/erase` – metadata erasure
- Pydantic v2 request/response models (`src/python/models.py`)
- React + TypeScript scaffold with Vite
- `scripts/generate_cat_garden.py` – standalone SDK usage example
- Project initialization: LICENSE, README, CONTRIBUTING, SECURITY
