# CLAUDE.md — novelai-userscripts-example

## プロジェクト概要

NovelAI 向けのユーザースクリプト例を収録したリポジトリです。

## マルチリポジトリ環境

このワークスペース (`novelai-userscripts-example.code-workspace`) には以下の複数リポジトリが含まれています。

| リポジトリ | パス | 役割 |
|---|---|---|
| **novelai-userscripts-example** | `.` | **メインリポジトリ（作業対象）** |
| novelai-sdk | `../novelai-sdk` | 参照用SDK |
| novelai-image-metadata | `../novelai-image-metadata` | 参照用ライブラリ |
| novelai-script-examples | `../novelai-script-examples` | 参照用サンプル |
| novelai-tokenizer | `../novelai-tokenizer` | 参照用トークナイザー |
| **voicevox-assistant-mcp** | `../voicevox-assistant-mcp` | **Claude Code の音声アシスタント（作業対象）** |

## 重要: Git 操作ルール

**git commit / git push などの書き込み操作は、必ず作業対象のリポジトリ (`novelai-userscripts-example`, `voicevox-assistant-mcp`) 内でのみ実行してください。**

- 参照用の 4 つのリポジトリ (`novelai-sdk`, `novelai-image-metadata`, `novelai-script-examples`, `novelai-tokenizer`) は**読み取り専用**として扱う。
- コードの参照・検索は他リポジトリに対して行ってよい。
- ファイルの編集・コミット・プッシュは作業対象の 2 つのリポジトリのみに限定する。
- `voicevox-assistant-mcp` はユーザー全体に `uv tool install` で入れて使っている。変更をマージしたら `uv tool upgrade voicevox-assistant-mcp` と、ウィンドウの再読み込みが必要。
- ユーザーから明示的に指示があった場合でも、他リポジトリへの git 書き込み操作は実行前に必ず確認を取る。

## ディレクトリ構成

```
novelai-userscripts-example/   ← このリポジトリ（唯一の作業対象）
├── .claude/
│   └── settings.json          ← 他リポジトリへのコミット防止設定
├── CLAUDE.md                  ← このファイル
├── CHANGELOG.md
├── CONTRIBUTING.md
├── LICENSE
└── README.md
```
