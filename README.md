# NovelAI Userscripts Example

[English](#english) | [日本語](#日本語)

---

## English

A full-stack UI application for NovelAI image generation, built on top of the [novelai-sdk](https://github.com/caru-ini/novelai-sdk).  
Login with your NovelAI account and generate images directly from your browser.

> **Disclaimer:** This project is an independent, personal work and is in no way affiliated with, endorsed by, or officially connected to the developers of [novelai-sdk](https://github.com/caru-ini/novelai-sdk) or NovelAI Inc.

### Features

| Status | Feature |
|--------|---------|
| ✅ | NovelAI account login (email + password) |
| ✅ | Image generation with full parameter control |
| ✅ | Streaming image generation (SSE, backend) |
| ✅ | Anlas consumption estimation |
| ✅ | Image metadata extraction / erasure |
| 🔄 | Image-to-Image (planned) |
| 🔄 | Inpainting (planned) |
| 🔄 | Character Reference UI (planned) |
| 🔄 | Vibe Transfer / ControlNet UI (planned) |

### Requirements

- **Python**: 3.10 or higher
- **Node.js**: 20 or higher
- **uv**: Latest (Python package manager)

### Quick Start

#### 1. Clone & install dependencies

```bash
git clone https://github.com/hirotoitpost/novelai-userscripts-example.git
cd novelai-userscripts-example

# Python dependencies (creates .venv automatically)
uv sync

# Node.js dependencies
npm install
```

#### 2. Configure environment

```bash
cp .env.example .env
# Edit .env if needed — no API token required for email/password login
```

#### 3. Start services

Open **two terminals**:

```bash
# Terminal 1 — FastAPI backend (port 8000)
uv run python -m uvicorn python.server:app --app-dir src --host 127.0.0.1 --port 8000 --reload

# Terminal 2 — Vite frontend (port 5173)
npm run dev
```

Open **http://localhost:5173** in your browser.

#### 4. Log in

Enter your NovelAI account **email** and **password**.  
The backend derives the access key using argon2id (same algorithm as the official client) and authenticates with NovelAI — your raw password is never stored.

> **Alternative:** Set `NOVELAI_API_TOKEN=pst-...` in `.env` to skip the login screen and use a [persistent API token](https://docs.novelai.net) instead.

### Usage

#### Image Generation

1. Click **「画像生成」** on the home screen
2. Enter a prompt in the left sidebar
3. Adjust parameters (model, size, steps, scale, seed…)
4. Click **「生成する」** or press **Ctrl+Enter**
5. The generated image appears on the right — use the **Download** button to save it

#### Prompt & Image Privacy

| Data | Where it lives | Persisted? |
|------|---------------|------------|
| Prompt text | React state (browser memory) | No |
| Generated image | Browser memory (base64) | No — download to save |
| Access token | `localStorage` (browser only) | Until logout |
| Email / Password | Used once for key derivation, then discarded | Never |

### Development

```bash
# Format code
uv run poe fmt

# Lint
uv run poe lint

# Type check
uv run poe check

# Run tests
uv run pytest
```

#### Commit Convention

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
{feat|fix|docs|style|refactor|test|chore}: Short description
```

### Project Structure

```
.
├── src/
│   ├── python/                  # FastAPI backend
│   │   ├── server.py            # App entry point
│   │   ├── client.py            # NovelAI client (Bearer token per-request)
│   │   ├── auth_utils.py        # argon2id key derivation + login
│   │   ├── models.py            # Pydantic request/response models
│   │   └── routes/
│   │       ├── auth.py          # POST /api/auth/login
│   │       ├── image.py         # POST /api/image/generate (+ stream, anlas)
│   │       └── metadata.py      # POST /api/metadata/extract|erase
│   └── javascript/              # React + TypeScript frontend
│       ├── api.ts               # Authenticated fetch utility
│       ├── App.tsx              # Router setup
│       ├── context/
│       │   └── AuthContext.tsx  # Login state (localStorage)
│       └── pages/
│           ├── Login.tsx/css    # Email + password login screen
│           ├── Home.tsx/css     # Dashboard
│           └── ImageGenerate.tsx/css  # Image generation UI
├── scripts/
│   └── generate_cat_garden.py  # Standalone SDK usage example
├── tests/                       # pytest test files
├── docs/                        # Additional documentation
│   └── architecture.md          # System architecture & API reference
├── index.html                   # Vite entry point
├── vite.config.ts               # Vite config (API proxy to :8000)
├── pyproject.toml               # Python dependencies & tools
├── package.json                 # Node.js dependencies
└── .env.example                 # Environment variable template
```

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/auth/login` | Email + password → access token |
| `POST` | `/api/image/generate` | Generate image(s) |
| `POST` | `/api/image/generate/stream` | Streaming generation (SSE) |
| `POST` | `/api/image/anlas` | Estimate Anlas cost |
| `POST` | `/api/metadata/extract` | Extract image metadata |
| `POST` | `/api/metadata/erase` | Erase image metadata |

Full interactive docs available at **http://localhost:8000/docs** while the backend is running.

### License

MIT — see [LICENSE](LICENSE) for details.

### Related Projects

- [novelai-sdk](https://github.com/caru-ini/novelai-sdk) – Community Python SDK (third-party)
- [novelai-image-metadata](https://github.com/NovelAI/novelai-image-metadata) – Metadata tools
- [novelai-script-examples](https://github.com/NovelAI/novelai-script-examples) – Script examples

---

## 日本語

[novelai-sdk](https://github.com/caru-ini/novelai-sdk) を基盤とした、NovelAI 画像生成用のフルスタック UI アプリです。  
NovelAI アカウントでログインして、ブラウザから直接画像を生成できます。

> **免責事項:** 本プロジェクトは個人による独自の取り組みであり、[novelai-sdk](https://github.com/caru-ini/novelai-sdk) の開発者および NovelAI Inc. とは一切関係ありません。

### 機能

| 状態 | 機能 |
|------|------|
| ✅ | NovelAI アカウントログイン（メール + パスワード） |
| ✅ | フルパラメータ制御での画像生成 |
| ✅ | ストリーミング画像生成（SSE、バックエンド実装済み） |
| ✅ | Anlas 消費量の事前計算 |
| ✅ | 画像メタデータの抽出・消去 |
| 🔄 | Image-to-Image（今後実装） |
| 🔄 | インペインティング（今後実装） |
| 🔄 | Character Reference UI（今後実装） |
| 🔄 | Vibe Transfer / ControlNet UI（今後実装） |

### 要件

- **Python**: 3.10 以上
- **Node.js**: 20 以上
- **uv**: 最新版（Python パッケージマネージャー）

### クイックスタート

#### 1. クローン & 依存関係のインストール

```bash
git clone https://github.com/hirotoitpost/novelai-userscripts-example.git
cd novelai-userscripts-example

# Python 依存関係（.venv を自動作成）
uv sync

# Node.js 依存関係
npm install
```

#### 2. 環境設定

```bash
cp .env.example .env
# メール+パスワードでログインする場合、.env の編集は不要
```

#### 3. サービスの起動

**2つのターミナル**を開いてそれぞれ実行します。

```bash
# ターミナル 1 — FastAPI バックエンド（ポート 8000）
uv run python -m uvicorn python.server:app --app-dir src --host 127.0.0.1 --port 8000 --reload

# ターミナル 2 — Vite フロントエンド（ポート 5173）
npm run dev
```

ブラウザで **http://localhost:5173** を開きます。

#### 4. ログイン

NovelAI アカウントの**メールアドレス**と**パスワード**を入力してください。  
バックエンドが argon2id でアクセスキーを導出し（公式クライアントと同じアルゴリズム）、NovelAI に認証します。生のパスワードはどこにも保存されません。

> **代替手段:** `.env` に `NOVELAI_API_TOKEN=pst-...` を設定すると、[永続 API トークン](https://docs.novelai.net)によってログイン画面をスキップできます。

### 使い方

#### 画像生成

1. ホーム画面の **「🎨 画像生成」** カードをクリック
2. 左サイドバーにプロンプトを入力
3. パラメータ（モデル・サイズ・Steps・Scale・Seed など）を調整
4. **「生成する」** ボタンをクリックまたは **Ctrl+Enter** を押す
5. 右エリアに生成画像が表示される。**ダウンロード** ボタンで保存可能

#### プロンプト・画像のプライバシー

| データ | 保存場所 | 永続化 |
|--------|---------|--------|
| プロンプトテキスト | React state（ブラウザメモリ） | なし |
| 生成画像 | ブラウザメモリ（base64） | なし（ダウンロードしない限り消える） |
| アクセストークン | `localStorage`（ブラウザのみ） | ログアウトまで |
| メール / パスワード | キー導出に一度だけ使用後、即破棄 | 一切保存しない |

### 開発

```bash
# コードフォーマット
uv run poe fmt

# リント
uv run poe lint

# 型チェック
uv run poe check

# テスト実行
uv run pytest
```

#### コミット規約

[Conventional Commits](https://www.conventionalcommits.org/ja/v1.0.0/) 形式に従ってください。

```
{feat|fix|docs|style|refactor|test|chore}: 簡潔な説明
```

### プロジェクト構造

```
.
├── src/
│   ├── python/                  # FastAPI バックエンド
│   │   ├── server.py            # アプリエントリーポイント
│   │   ├── client.py            # NovelAI クライアント（リクエストごとに Bearer トークン）
│   │   ├── auth_utils.py        # argon2id キー導出 + ログイン処理
│   │   ├── models.py            # Pydantic リクエスト/レスポンスモデル
│   │   └── routes/
│   │       ├── auth.py          # POST /api/auth/login
│   │       ├── image.py         # POST /api/image/generate（+ stream, anlas）
│   │       └── metadata.py      # POST /api/metadata/extract|erase
│   └── javascript/              # React + TypeScript フロントエンド
│       ├── api.ts               # 認証付き fetch ユーティリティ
│       ├── App.tsx              # ルーター設定
│       ├── context/
│       │   └── AuthContext.tsx  # ログイン状態管理（localStorage）
│       └── pages/
│           ├── Login.tsx/css    # メール + パスワードログイン画面
│           ├── Home.tsx/css     # ダッシュボード
│           └── ImageGenerate.tsx/css  # 画像生成 UI
├── scripts/
│   └── generate_cat_garden.py  # SDK 単体利用サンプル
├── tests/                       # pytest テストファイル
├── docs/
│   └── architecture.md          # システムアーキテクチャ & API リファレンス
├── index.html                   # Vite エントリーポイント
├── vite.config.ts               # Vite 設定（/api/ を :8000 へプロキシ）
├── pyproject.toml               # Python 依存関係 & ツール設定
├── package.json                 # Node.js 依存関係
└── .env.example                 # 環境変数テンプレート
```

### API エンドポイント

| メソッド | パス | 説明 |
|---------|------|------|
| `POST` | `/api/auth/login` | メール + パスワード → アクセストークン |
| `POST` | `/api/image/generate` | 画像生成 |
| `POST` | `/api/image/generate/stream` | ストリーミング生成（SSE） |
| `POST` | `/api/image/anlas` | Anlas 消費量の見積もり |
| `POST` | `/api/metadata/extract` | 画像メタデータの抽出 |
| `POST` | `/api/metadata/erase` | 画像メタデータの消去 |

バックエンド起動中は **http://localhost:8000/docs** でインタラクティブな API ドキュメントを確認できます。

### ライセンス

MIT — 詳細は [LICENSE](LICENSE) を参照してください。

### 関連プロジェクト

- [novelai-sdk](https://github.com/caru-ini/novelai-sdk) – コミュニティ製 Python SDK（サードパーティ）
- [novelai-image-metadata](https://github.com/NovelAI/novelai-image-metadata) – メタデータ抽出ツール
- [novelai-script-examples](https://github.com/NovelAI/novelai-script-examples) – スクリプト例
