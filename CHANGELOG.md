# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned

- Inpainting UI
- Character Reference UI
- Vibe Transfer / ControlNet UI
- Batch generation UI

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
