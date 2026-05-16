# NovelAI Userscripts Example

[English](../README.md) | 日本語

[novelai-sdk](https://github.com/caru-ini/novelai-sdk) を基盤とした、NovelAI 画像生成用のフルスタック UI アプリです。  
NovelAI アカウントでログインして、ブラウザから直接画像を生成できます。

> **免責事項:** 本プロジェクトは個人による独自の取り組みであり、[novelai-sdk](https://github.com/caru-ini/novelai-sdk) の開発者および NovelAI Inc. とは一切関係ありません。

## 機能

| 状態 | 機能 |
|------|------|
| ✅ | NovelAI アカウントログイン（メール + パスワード） |
| ✅ | フルパラメータ制御での画像生成 |
| ✅ | 設定の永続化（localStorage） |
| ✅ | 生成前の Anlas 消費量プレビュー |
| ✅ | ライブ中間プレビュー付きストリーミング生成（SSE） |
| ✅ | Anlas 消費量の事前計算（バックエンド） |
| ✅ | 画像メタデータの抽出・消去 |
| ✅ | メタデータ管理 UI |
| ✅ | Image-to-Image モード |
| ✅ | AI アシスタント（vLLM を活用したプロンプト支援ツール） |
| 🔄 | インペインティング（今後実装） |
| 🔄 | Character Reference UI（今後実装） |
| 🔄 | Vibe Transfer / ControlNet UI（今後実装） |

## 要件

- **Python**: 3.10 以上
- **Node.js**: 20 以上
- **uv**: 最新版（Python パッケージマネージャー）
- **Docker + NVIDIA GPU**（任意）: AI アシスタント機能（vLLM）に必要

## クイックスタート

### 1. クローン & 依存関係のインストール

```bash
git clone https://github.com/hirotoitpost/novelai-userscripts-example.git
cd novelai-userscripts-example

# Python 依存関係（.venv を自動作成）
uv sync

# Node.js 依存関係
npm install
```

### 2. 環境設定

```bash
cp .env.example .env
# メール+パスワードでログインする場合、.env の編集は不要
```

AI アシスタントを使用する場合は `.env` に以下を追加してください:

```
VLLM_BASE_URL=http://localhost:8001/v1
VLLM_MODEL=Qwen/Qwen2-VL-7B-Instruct
VLLM_VISION_BASE_URL=http://localhost:8001/v1
VLLM_VISION_MODEL=Qwen/Qwen2-VL-7B-Instruct
HF_TOKEN=hf_...   # モデルダウンロード用 Hugging Face トークン
```

### 3. サービスの起動

**2つのターミナル**を開いてそれぞれ実行します。

```bash
# ターミナル 1 — FastAPI バックエンド（ポート 8000）
uv run python -m uvicorn python.server:app --app-dir src --host 127.0.0.1 --port 8000 --reload

# ターミナル 2 — Vite フロントエンド（ポート 5173）
npm run dev

# ターミナル 3（任意）— AI アシスタント用 vLLM サーバー（NVIDIA GPU + Docker が必要）
docker compose up -d
```

ブラウザで **http://localhost:5173** を開きます。

### 4. ログイン

NovelAI アカウントの**メールアドレス**と**パスワード**を入力してください。  
バックエンドが argon2id でアクセスキーを導出し（公式クライアントと同じアルゴリズム）、NovelAI に認証します。生のパスワードはどこにも保存されません。

> **代替手段:** `.env` に `NOVELAI_API_TOKEN=pst-...` を設定すると、[永続 API トークン](https://docs.novelai.net)によってログイン画面をスキップできます。

## 使い方

### 画像生成

1. ホーム画面の **「🎨 画像生成」** カードをクリック
2. 左サイドバーにプロンプトを入力
3. パラメータ（モデル・サイズ・Steps・Scale・Seed など）を調整
4. 生成ボタン下の **推定 Anlas 消費量** を確認
5. **「生成する」** ボタンをクリックまたは **Ctrl+Enter** を押す
6. 生成ステップごとに中間プレビュー画像がリアルタイムで更新される
7. 生成完了後に最終画像が表示される。**ダウンロード** ボタンで保存可能

> プロンプトやパラメータはすべて `localStorage` に自動保存され、次回アクセス時に復元されます。

### プロンプト・画像のプライバシー

| データ | 保存場所 | 永続化 |
|--------|---------|--------|
| プロンプトテキスト | React state（ブラウザメモリ） | なし |
| 生成画像 | ブラウザメモリ（base64） | なし（ダウンロードしない限り消える） |
| アクセストークン | `localStorage`（ブラウザのみ） | ログアウトまで |
| メール / パスワード | キー導出に一度だけ使用後、即破棄 | 一切保存しない |

## 開発

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

### コミット規約

[Conventional Commits](https://www.conventionalcommits.org/ja/v1.0.0/) 形式に従ってください。

```
{feat|fix|docs|style|refactor|test|chore}: 簡潔な説明
```

## プロジェクト構造

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
│   ├── README_jp.md             # 日本語 README（このファイル）
│   └── architecture.md          # システムアーキテクチャ & API リファレンス
├── index.html                   # Vite エントリーポイント
├── vite.config.ts               # Vite 設定（/api/ を :8000 へプロキシ）
├── pyproject.toml               # Python 依存関係 & ツール設定
├── package.json                 # Node.js 依存関係
└── .env.example                 # 環境変数テンプレート
```

## API エンドポイント

| メソッド | パス | 説明 |
|---------|------|------|
| `POST` | `/api/auth/login` | メール + パスワード → アクセストークン |
| `POST` | `/api/image/generate` | 画像生成 |
| `POST` | `/api/image/generate/stream` | ストリーミング生成（SSE） |
| `POST` | `/api/image/anlas` | Anlas 消費量の見積もり |
| `POST` | `/api/metadata/extract` | 画像メタデータの抽出 |
| `POST` | `/api/metadata/erase` | 画像メタデータの消去 |
| `POST` | `/api/llm/prompt-format` | プロンプト整形（SSE） |
| `POST` | `/api/llm/char-gen` | キャラ設定生成（SSE） |
| `POST` | `/api/llm/story-draft` | 物語ドラフト生成（SSE） |
| `POST` | `/api/llm/aux-text` | 補助テキスト生成（SSE） |
| `POST` | `/api/llm/metadata-gen` | 生成パラメータ一括提案（SSE） |
| `POST` | `/api/llm/reverse-prompt` | リバースプロンプト（SSE） |

バックエンド起動中は **http://localhost:8000/docs** でインタラクティブな API ドキュメントを確認できます。

## ライセンス

MIT — 詳細は [LICENSE](../LICENSE) を参照してください。

## 関連プロジェクト

- [novelai-sdk](https://github.com/caru-ini/novelai-sdk) – コミュニティ製 Python SDK（サードパーティ）
- [novelai-image-metadata](https://github.com/NovelAI/novelai-image-metadata) – メタデータ抽出ツール
- [novelai-script-examples](https://github.com/NovelAI/novelai-script-examples) – スクリプト例
