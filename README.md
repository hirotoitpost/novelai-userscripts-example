# NovelAI Userscripts Example

[English](#english) | [日本語](#日本語)

---

## English

A comprehensive UI framework for NovelAI integration, built on top of the [novelai-sdk](https://github.com/caru-ini/novelai-sdk) to simplify image generation workflows with advanced features like character management, batch processing, and metadata handling.

> **Disclaimer:** This project is an independent, personal work and is in no way affiliated with, endorsed by, or officially connected to the developers of [novelai-sdk](https://github.com/caru-ini/novelai-sdk) or NovelAI Inc. The use of their libraries is solely at the author's own discretion.

### Features

- **Python + JavaScript/React Integration** – Full-stack UI development framework
- **Type-Safe Development** – Built with Python 3.10+, TypeScript, and comprehensive type hints
- **Image Generation** – Streamlined NovelAI API interaction
- **Metadata Management** – Extract and manage image generation parameters
- **Batch Operations** – Handle multiple image generations efficiently
- **Character Reference System** – Maintain consistent character appearances

### Requirements

- **Python**: 3.10 or higher
- **Node.js**: 16 or higher
- **npm**: 8 or higher (or alternative package manager)
- **uv**: Latest version (Python package manager)
- **Docker**: Optional, for containerized deployment

### Quick Start

#### Python Setup

```bash
# Clone the repository
git clone https://github.com/hirotoitpost/novelai-userscripts-example.git
cd novelai-userscripts-example

# Initialize Python environment
uv sync

# Install pre-commit hooks
uv run poe pre-commit
```

#### JavaScript/React Setup

```bash
# Install dependencies
npm install

# Build
npm run build

# Development
npm run dev
```

### Development

#### Code Quality

This project uses **Ruff** for linting and formatting, **Pyright** for type checking, and **pytest** for testing.

```bash
# Format code
uv run poe fmt

# Lint
uv run poe lint

# Type check
uv run poe check

# Run tests
pytest
```

#### Commit Convention

Follow the [Conventional Commits](https://www.conventionalcommits.org/) format:

```
{feat|fix|docs|style|refactor|test|chore}: Short description
```

### Project Structure

```
.
├── src/                    # Source code
│   ├── python/            # Python backend
│   └── javascript/        # JavaScript/React frontend
├── tests/                 # Test files
├── docs/                  # Documentation
├── pyproject.toml         # Python project config
├── package.json           # Node.js project config
├── CONTRIBUTING.md        # Contribution guidelines
└── README.md             # This file
```

### Contributing

Please read [CONTRIBUTING.md](CONTRIBUTING.md) for details on our code of conduct and the process for submitting pull requests.

### License

This project is licensed under the MIT License - see [LICENSE](LICENSE) file for details.

### Related Projects

- [novelai-sdk](https://github.com/caru-ini/novelai-sdk) – Community Python SDK (third-party, not affiliated with this project)
- [novelai-image-metadata](https://github.com/NovelAI/novelai-image-metadata) – Metadata extraction tools
- [novelai-script-examples](https://github.com/NovelAI/novelai-script-examples) – Script examples

---

## 日本語

[novelai-sdk](https://github.com/caru-ini/novelai-sdk)を基盤とした、NovelAI統合用の包括的なUIフレームワークです。キャラクター管理、バッチ処理、メタデータ処理などの高度な機能で画像生成ワークフローを簡素化します。

> **免責事項:** 本プロジェクトは個人による独自の取り組みであり、[novelai-sdk](https://github.com/caru-ini/novelai-sdk) の開発者および NovelAI Inc. とは一切関係ありません。同ライブラリの利用は、作者が独自の判断で行っているものです。

### 機能

- **Python + JavaScript/React統合** – フルスタックUI開発フレームワーク
- **型安全な開発** – Python 3.10+、TypeScript、および包括的な型ヒント
- **画像生成** – NovelAI APIとの連携を簡素化
- **メタデータ管理** – 画像生成パラメータの抽出・管理
- **バッチ操作** – 複数の画像生成を効率的に処理
- **キャラクター参照システム** – キャラクターの一貫した外観を保持

### 要件

- **Python**: 3.10以上
- **Node.js**: 16以上
- **npm**: 8以上（または別のパッケージマネージャー）
- **uv**: 最新版（Pythonパッケージマネージャー）
- **Docker**: オプション（コンテナ化されたデプロイメント用）

### クイックスタート

#### Pythonセットアップ

```bash
# リポジトリをクローン
git clone https://github.com/hirotoitpost/novelai-userscripts-example.git
cd novelai-userscripts-example

# Python環境を初期化
uv sync

# プリコミットフックをインストール
uv run poe pre-commit
```

#### JavaScript/Reactセットアップ

```bash
# 依存関係をインストール
npm install

# ビルド
npm run build

# 開発
npm run dev
```

### 開発

#### コード品質

このプロジェクトは**Ruff**によるリント・フォーマット、**Pyright**による型チェック、**pytest**によるテストを使用しています。

```bash
# コードをフォーマット
uv run poe fmt

# リント
uv run poe lint

# 型チェック
uv run poe check

# テストを実行
pytest
```

#### コミット規約

[Conventional Commits](https://www.conventionalcommits.org/ja/v1.0.0/) 形式に従ってください：

```
{feat|fix|docs|style|refactor|test|chore}: 簡潔な説明
```

### プロジェクト構造

```
.
├── src/                    # ソースコード
│   ├── python/            # Pythonバックエンド
│   └── javascript/        # JavaScript/Reactフロントエンド
├── tests/                 # テストファイル
├── docs/                  # ドキュメント
├── pyproject.toml         # Pythonプロジェクト設定
├── package.json           # Node.jsプロジェクト設定
├── CONTRIBUTING.md        # コントリビューションガイドライン
└── README.md             # このファイル
```

### コントリビューション

詳細は[CONTRIBUTING.md](CONTRIBUTING.md)をご覧ください。

### ライセンス

このプロジェクトはMITライセンスの下でライセンスされています。詳細は[LICENSE](LICENSE)ファイルを参照してください。

### 関連プロジェクト

- [novelai-sdk](https://github.com/caru-ini/novelai-sdk) – コミュニティ製Python SDK（サードパーティ製・本プロジェクトとは無関係）
- [novelai-image-metadata](https://github.com/NovelAI/novelai-image-metadata) – メタデータ抽出ツール
- [novelai-script-examples](https://github.com/NovelAI/novelai-script-examples) – スクリプト例
