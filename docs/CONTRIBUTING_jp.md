# novelai-userscripts-example への貢献

[English](../CONTRIBUTING.md) | 日本語

このプロジェクトへのコントリビューションに関心をお持ちいただきありがとうございます。本ドキュメントはコントリビューションのガイドラインを提供しています。

## クイックスタートワークフロー

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

## コントリビューションタイプ

- **バグ修正**: Issueを参照し、テストケースを含め、PR説明で修正内容を詳述
- **新機能**: Issues/Discussionsで事前提案、テスト・ドキュメント・例を含める
- **ドキュメント**: 明確性の向上、例の追加、タイプミス修正、翻訳への貢献
- **コード品質**: リファクタリング、最適化、テストカバレッジの向上

## 開発環境

### 要件

- Windows、macOS、またはLinux
- Python 3.10以上
- Node.js 16以上
- uv（Pythonパッケージマネージャー）
- Git

### セットアップ

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

## コード規約

### Python

- **フォーマッタ**: Ruff（`uv run poe fmt`）
- **リンター**: Ruff（`uv run poe lint`）
- **型チェッカー**: Pyright（`uv run poe check`）
- **スタイル**: Ruffで強制されるPEP 8規約に従う
- **型ヒント**: 完全な型注釈が必須（Python 3.10+）
- **検証**: データ検証にはPydantic v2を使用

### JavaScript/React

- **フォーマッタ**: Prettier
- **リンター**: ESLint
- **型システム**: TypeScript
- **モジュール形式**: ESモジュール

### Markdown

- 1行の最大文字数: 100文字
- 500行を超えるファイルは論理的なセクションに分割
- 明確な見出しとコードブロック構文を使用

### 言語

- **主言語**: コード、コメント、ドキュメントは英語
- **副言語**: 重要なドキュメントは日本語翻訳を提供

## Gitワークフローとブランチ戦略

### ブランチ命名規約

```
feature/[説明]        # 新機能
fix/[説明]            # バグ修正
docs/[説明]           # ドキュメント更新
refactor/[説明]       # コードリファクタリング
test/[説明]           # テスト追加/改善
chore/[説明]          # ビルド、依存関係など
```

例: `feature/add-character-reference-system`、`fix/metadata-extraction-bug`

### コミットメッセージ形式

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

## PRチェックリスト要件

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

## ドキュメント

- すべての関数とクラスのドックストリングを更新
- ドックストリングに使用例を含める
- 新機能追加時はREADME.mdを更新
- CHANGELOG.mdの「Unreleased」セクションを更新

## テスト

- すべての新機能についてテストを作成
- コードカバレッジを維持または改善
- テストファイルの場所: `tests/`ディレクトリ
- テスト実行: `pytest`
- テストファイル命名規約: `test_[feature_name].py`

## コードレビュープロセス

1. PR提出時にレビュアーをリクエスト
2. レビューコメントに速やかに対応
3. 変更後にレビューを再度リクエスト
4. すべての会話が解決されてからマージ
5. レビュー中にリクエストされた場合はコミットをスカッシュ

## 開始前に

- 既存のIssues/PRを確認して重複を避ける
- 大きな変更はIssuesまたはDiscussionsで事前に議論
- トラブルシューティングは`docs/`ディレクトリを参照

## サポートと質問

- **Issues**: バグ報告と機能要望
- **Discussions**: 質問、提案、一般的な相談
- **ドキュメント**: ガイドは`docs/`ディレクトリを参照

## ライセンス

このプロジェクトへのコントリビューションにより、あなたのコントリビューションがMITライセンスの下でライセンスされることに同意するものとします。
