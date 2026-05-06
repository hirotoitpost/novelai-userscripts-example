# Contributing to novelai-userscripts-example

[English](#english) | [日本語](#日本語)

---

## English

Thank you for your interest in contributing! This document provides guidelines for contributing to the novelai-userscripts-example project.

### 🚀 Quick Start Workflow

1. **Fork and Clone**: Fork the repository and clone to your local machine
   ```bash
   git clone https://github.com/YOUR_USERNAME/novelai-userscripts-example.git
   cd novelai-userscripts-example
   ```

2. **Create Branch**: Use the naming convention below
   ```bash
   git checkout -b feature/YOUR_DESCRIPTION
   ```

3. **Implement Changes**: Edit code, add tests, and update documentation

4. **Verify Quality**: Run all checks locally
   ```bash
   uv run poe fmt
   uv run poe lint
   uv run poe check
   pytest
   npm run lint  # JavaScript/React
   ```

5. **Commit**: Follow [Conventional Commits](https://www.conventionalcommits.org/) format
   ```bash
   git commit -m "feat: add new feature description"
   ```

6. **Push & Create PR**: Push your branch and create a pull request via GitHub Web UI

### 📋 Contribution Types

- **Bug Fixes**: Reference Issues, include test cases, detail the fix in PR description
- **New Features**: Propose via Issues/Discussions first, include tests, documentation, and examples
- **Documentation**: Improve clarity, add examples, fix typos, contribute translations
- **Code Quality**: Refactor, optimize, improve test coverage

### 🛠️ Development Environment

#### Requirements

- Windows, macOS, or Linux
- Python 3.10 or higher
- Node.js 16 or higher
- uv (Python package manager)
- Git

#### Setup

```bash
# Python environment
uv sync

# JavaScript dependencies
npm install

# Pre-commit hooks
uv run poe pre-commit

# Run all checks
uv run poe fmt && uv run poe lint && uv run poe check && pytest
```

### 📖 Code Standards

#### Python

- **Formatter**: Ruff (`uv run poe fmt`)
- **Linter**: Ruff (`uv run poe lint`)
- **Type Checker**: Pyright (`uv run poe check`)
- **Style**: Follow PEP 8 conventions enforced by Ruff
- **Type Hints**: Full type annotations required (Python 3.10+)
- **Validation**: Use Pydantic v2 for data validation

#### JavaScript/React

- **Formatter**: Prettier
- **Linter**: ESLint
- **Type System**: TypeScript
- **Module Format**: ES modules

#### Markdown

- Maximum 100 characters per line
- Split files exceeding 500 lines into logical sections
- Use clear headings and code block syntax highlighting

#### Language

- **Primary**: English for code, comments, and documentation
- **Secondary**: Japanese translations provided for key documents

### 🔄 Git Workflow & Branch Strategy

#### Branch Naming Convention

```
feature/[DESCRIPTION]    # New features
fix/[DESCRIPTION]        # Bug fixes
docs/[DESCRIPTION]       # Documentation updates
refactor/[DESCRIPTION]   # Code refactoring
test/[DESCRIPTION]       # Test additions/improvements
chore/[DESCRIPTION]      # Build, dependencies, etc.
```

Example: `feature/add-character-reference-system`, `fix/metadata-extraction-bug`

#### Commit Message Format

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
{feat|fix|docs|style|refactor|test|chore}: Brief description

Optional body explaining:
- Why the change is necessary
- What the change does
- How to verify it works
```

Example:
```
feat: implement character reference system

Add support for maintaining consistent character appearances
across multiple generated images using ControlNet integration.

Fixes #42
```

### ✅ PR Checklist Requirements

Before submitting a pull request, verify:

- [ ] Branch is up to date with `main`
- [ ] No merge conflicts
- [ ] All tests passing: `pytest`
- [ ] Code formatted: `uv run poe fmt`
- [ ] Linting passed: `uv run poe lint`
- [ ] Type checking passed: `uv run poe check`
- [ ] JavaScript linting passed: `npm run lint`
- [ ] Documentation updated (README, docstrings, etc.)
- [ ] Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/) format
- [ ] PR description references related Issues

### 📝 Documentation

- Update docstrings for all functions and classes
- Include usage examples in docstrings
- Update README.md if adding new features
- Update CHANGELOG.md in the Unreleased section

### ✨ Testing

- Write tests for all new functionality
- Maintain or improve code coverage
- Test file location: `tests/` directory
- Run tests: `pytest`
- Test naming convention: `test_[feature_name].py`

### 🔍 Code Review Process

1. Request reviewers when submitting PR
2. Address review comments promptly
3. Re-request review after making changes
4. Ensure all conversations are resolved before merging
5. Squash commits if requested during review

### 🚫 Before You Start

- Check existing Issues/PRs to avoid duplicates
- Discuss major changes in Issues or Discussions first
- Check the `docs/` directory for troubleshooting guides

### 📞 Support & Questions

- **Issues**: Bug reports and feature requests
- **Discussions**: Questions, ideas, and general discussion
- **Documentation**: Check docs/ directory for guides

### License

By contributing to this project, you agree that your contributions will be licensed under the MIT License.

---

## 日本語

このプロジェクトへのコントリビューションに関心をお持ちいただきありがとうございます。本ドキュメントはコントリビューションのガイドラインを提供しています。

### 🚀 クイックスタートワークフロー

1. **フォークとクローン**: リポジトリをフォークしてクローンします
   ```bash
   git clone https://github.com/YOUR_USERNAME/novelai-userscripts-example.git
   cd novelai-userscripts-example
   ```

2. **ブランチを作成**: 以下の命名規約を使用します
   ```bash
   git checkout -b feature/YOUR_DESCRIPTION
   ```

3. **変更を実装**: コードを編集し、テストとドキュメントを追加します

4. **品質確認**: すべてのチェックをローカルで実行します
   ```bash
   uv run poe fmt
   uv run poe lint
   uv run poe check
   pytest
   npm run lint  # JavaScript/React
   ```

5. **コミット**: [Conventional Commits](https://www.conventionalcommits.org/ja/v1.0.0/) 形式に従います
   ```bash
   git commit -m "feat: 新機能の説明"
   ```

6. **プッシュとPR作成**: ブランチをプッシュしてGitHub Web UIでプルリクエストを作成します

### 📋 コントリビューションタイプ

- **バグ修正**: Issueを参照し、テストケースを含め、PR説明で修正内容を詳述
- **新機能**: Issues/Discussionsで事前提案、テスト・ドキュメント・例を含める
- **ドキュメント**: 明確性の向上、例の追加、タイプミス修正、翻訳への貢献
- **コード品質**: リファクタリング、最適化、テストカバレッジの向上

### 🛠️ 開発環境

#### 要件

- Windows、macOS、またはLinux
- Python 3.10以上
- Node.js 16以上
- uv（Pythonパッケージマネージャー）
- Git

#### セットアップ

```bash
# Python環境
uv sync

# JavaScript依存関係
npm install

# プリコミットフック
uv run poe pre-commit

# すべてのチェックを実行
uv run poe fmt && uv run poe lint && uv run poe check && pytest
```

### 📖 コード規約

#### Python

- **フォーマッタ**: Ruff（`uv run poe fmt`）
- **リンター**: Ruff（`uv run poe lint`）
- **型チェッカー**: Pyright（`uv run poe check`）
- **スタイル**: Ruffで強制されるPEP 8規約に従う
- **型ヒント**: 完全な型注釈が必須（Python 3.10+）
- **検証**: データ検証にはPydantic v2を使用

#### JavaScript/React

- **フォーマッタ**: Prettier
- **リンター**: ESLint
- **型システム**: TypeScript
- **モジュール形式**: ESモジュール

#### Markdown

- 1行の最大文字数: 100文字
- 500行を超えるファイルは論理的なセクションに分割
- 明確な見出しとコードブロック構文を使用

#### 言語

- **主言語**: コード、コメント、ドキュメントは英語
- **副言語**: 重要なドキュメントは日本語翻訳を提供

### 🔄 Gitワークフローとブランチ戦略

#### ブランチ命名規約

```
feature/[説明]        # 新機能
fix/[説明]            # バグ修正
docs/[説明]           # ドキュメント更新
refactor/[説明]       # コードリファクタリング
test/[説明]           # テスト追加/改善
chore/[説明]          # ビルド、依存関係など
```

例: `feature/add-character-reference-system`、`fix/metadata-extraction-bug`

#### コミットメッセージ形式

[Conventional Commits](https://www.conventionalcommits.org/ja/v1.0.0/) 形式に従う：

```
{feat|fix|docs|style|refactor|test|chore}: 簡潔な説明

オプションの本文：
- 変更が必要な理由
- 変更の内容
- 動作確認方法
```

例:
```
feat: キャラクター参照システムの実装

ControlNet統合を使用した複数生成画像間での
一貫したキャラクター外観の維持に対応。

Fixes #42
```

### ✅ PR チェックリスト要件

プルリクエストを提出する前に以下を確認してください：

- [ ] ブランチが`main`に対して最新
- [ ] マージコンフリクトなし
- [ ] すべてのテスト合格: `pytest`
- [ ] コードフォーマット: `uv run poe fmt`
- [ ] リント合格: `uv run poe lint`
- [ ] 型チェック合格: `uv run poe check`
- [ ] JavaScriptリント合格: `npm run lint`
- [ ] ドキュメント更新（README、docstringsなど）
- [ ] コミットメッセージが [Conventional Commits](https://www.conventionalcommits.org/ja/v1.0.0/) 形式に従う
- [ ] PR説明が関連Issueを参照

### 📝 ドキュメント

- すべての関数とクラスのドックストリングを更新
- ドックストリングに使用例を含める
- 新機能追加時はREADME.mdを更新
- CHANGELOG.mdの「Unreleased」セクションを更新

### ✨ テスト

- すべての新機能についてテストを作成
- コードカバレッジを維持または改善
- テストファイルの場所: `tests/`ディレクトリ
- テスト実行: `pytest`
- テストファイル命名規約: `test_[feature_name].py`

### 🔍 コードレビュープロセス

1. PR提出時にレビュアーをリクエスト
2. レビューコメントに速やかに対応
3. 変更後にレビューを再度リクエスト
4. すべての会話が解決されてからマージ
5. レビュー中にリクエストされた場合はコミットをスカッシュ

### 🚫 開始前に

- 既存のIssues/PRを確認して重複を避ける
- 大きな変更はIssuesまたはDiscussionsで事前に議論
- トラブルシューティングは`docs/`ディレクトリを参照

### 📞 サポートと質問

- **Issues**: バグ報告と機能要望
- **Discussions**: 質問、提案、一般的な相談
- **ドキュメント**: ガイドは`docs/`ディレクトリを参照

### ライセンス

このプロジェクトへのコントリビューションにより、あなたのコントリビューションがMITライセンスの下でライセンスされることに同意するものとします。
