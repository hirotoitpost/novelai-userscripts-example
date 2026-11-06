# ログと生成の記録

バックエンドが残すログは3種類あります。どれも `data/`（または利用者のフォルダ）の下にあり、git には入りません。

| 種類 | 場所 | 中身 | 残す期間 |
|---|---|---|---|
| アプリのログ | `data/logs/backend.log`（日付が変わると `backend.log.YYYY-MM-DD` に切り替え） | アプリの INFO 以上、uvicorn のアクセスログ。時刻・レベル・どのモジュールか付き | 30 日（`NAI_LOG_KEEP_DAYS`） |
| 起動ごとの標準出力・標準エラー | `data/run/backend.log` / `backend.err.log`（今の起動）、`data/run/old/backend.YYYYMMDD-HHMMSS.log`（前の起動） | 起動の様子、落ちたときのトレースバック | 新しい方から 20 回分（`scripts/dev-ctl.ps1` の `$KeepRunLogs`） |
| 生成の記録 | 利用者のフォルダの `logs/generation-YYYY-MM.jsonl`（multi は `data/users/{id}/logs/`、single はリポジトリ直下の `logs/`） | NovelAI に画像を頼んだ1回ごとの、送った内容と結果（下記） | 12 か月（`NAI_GENERATION_LOG_KEEP_MONTHS`） |

以前は `dev-ctl.ps1` が起動のたびに `data/run/backend.log` を上書きしていました。そのため、再起動の前のことは何も残らず、アプリの INFO も捨てられていました。

## 生成の記録

1 行が 1 回の生成（JSON）です。プロンプトは利用者の作品なので、その人のフォルダに置きます。

- `ts`: 時刻（UTC）、`user_id`: 利用者（single は null）
- `source`
  - `novelai_image_v5`: 漫画のコマ・漫画のページ（NovelAI へ直接送る経路）
  - `sdk`: 画像生成ページ・ワード選択など（SDK 経由）
- `purpose` / `story_id` / `scene_id` / `scene_index` / `references`: 漫画のコマのとき。`references` はコマに記録した参照画像の写し（`outputs/manga/refs/used/`）と強さ
- `request`: 送った内容。画像は `sha256:...` に置き換えます。
  - キャラ参照は、実際に送る形（1024×1536 に整えたもの）のハッシュです。
  - そのため、コマの記録と画像生成ページの記録で、同じ参照画像を送ったかを比べられます。
- `result`
  - 成功：`status: ok`、出来た絵の `image_sha256`、かかった時間 `elapsed_s`、画像生成ページなら `history_id` と `image_paths`
  - 失敗：`status: error` と `error`

### 使い方の例

同じ設定で描き直しても同じ絵にならないときは、2 つの生成の `request` を比べます。違いが無ければ、原因は NovelAI 側です。

```sh
# 9話(物語 249)のコマの生成だけを見る
uv run python -c "import json,sys; [print(l.strip()) for l in open(sys.argv[1], encoding='utf-8') if json.loads(l).get('story_id') == 249]" data/users/1/logs/generation-2026-11.jsonl
```

## 設定（環境変数）

| 変数 | 既定 | 意味 |
|---|---|---|
| `NAI_LOG_DIR` | `data/logs` | アプリのログの置き場所 |
| `NAI_LOG_KEEP_DAYS` | 30 | アプリのログを残す日数 |
| `NAI_GENERATION_LOG_DIR` | （なし） | 生成の記録を、利用者に関わらずこのフォルダにまとめる（テスト用） |
| `NAI_GENERATION_LOG_KEEP_MONTHS` | 12 | 生成の記録を残す月数 |
