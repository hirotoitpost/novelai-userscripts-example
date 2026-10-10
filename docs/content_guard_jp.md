# コンテンツガード

このアプリにある「性的な内容」と「未成年に見える内容」を扱う仕組みを、すべてまとめた文書です。
どの機能に効き、どの機能には効かないか、値がどこにあり、どう編集するかを書きます。

**値(止めるタグ・取り除くタグ・足すネガティブ・LLM への指示・成人向けの判定の語)は、すべて DB にあります。**
Python のコードには書いていません。API(`/api/content-guard`)と、開発管理者のページ(`/admin` の「既定値・プリセット・ガード」)で
編集でき、保存すると次の処理から効きます(再起動は要りません)。

- 第1部(§1〜§3)— アプリ全体の一覧、値の場所、編集のしかた
- 第2部(§4〜§13)— キャラ別データセットのガード(タグを**止める**仕組みはここだけ)
- 第3部(§14〜§20)— そのほかの機能の仕組み
- 第4部(§21〜§22)— 既知の制限と、変更するときの注意

この文書に書いてあるタグの一覧は**はじめの値**です。今の値は `GET /api/content-guard/rules` か、開発管理者のページで確認してください。

判定はすべてサーバー側で行います。画面での制限は補助で、API を直接呼んでも同じ判定がかかります(画面だけの設定は §3.3 に分けて書きます)。

---

# 第1部 アプリ全体の一覧

## 1. 仕組みの一覧

働き方は5種類あります。

| 種類 | 意味 |
|---|---|
| ブロック | 該当するタグがあるとリクエストを拒否する(HTTP 422 / 403) |
| タグ除去 | 該当するタグをプロンプトから黙って取り除く(エラーにはしない) |
| ネガティブ | ネガティブプロンプトに語を足す。出にくくするだけで、出力は保証しない |
| LLM への指示 | 文章モデルへの指示文に書く。守られる保証はない |
| 表示の区分 | 生成は止めない。本棚・ギャラリーでの絞り込みとぼかしに使う |

| # | 仕組み | 種類 | 効く機能 | 値の場所(DB) | 節 |
|---|---|---|---|---|---|
| 1 | 基本のガード(性的なタグ) | ブロック | キャラ別データセットの生成・取り込み(全年齢) | `dataset.blocked_tags` | §7 |
| 2 | 未成年ガード(未成年を示すタグ) | ブロック | キャラ別データセットの生成・取り込み(R18)、成人フラグの保存 | `dataset.minor_tags` | §8、§9 |
| 3 | ガードプロファイル(データセットごとに足すタグ) | ブロック+ネガティブ | キャラ別データセットの生成・取り込み(全年齢・R18) | テーブル `guard_profiles` | §10 |
| 4 | 成人フラグ `is_adult` | R18 を許す条件 | キャラ別データセットの R18 | `characters.is_adult` | §9 |
| 5 | 全年齢のネガティブ | ネガティブ | キャラ別データセット(全年齢)、キャラシートの読み込み、参照画像の候補 | `general.safe_negative` | §7.3、§16 |
| 6 | R18 で足すタグ・ネガティブ | プロンプト+ネガティブ | キャラ別データセット(R18) | `dataset.r18_positive`、`dataset.r18_negative` | §8.3 |
| 7 | 場面タグの整理 `sanitize_scene_tags` | タグ除去(+成人向けタグの追加) | 物語のタグ付け、漫画v2のコマ生成 | `scene.sexual_tags`、`scene.minor_tags`、`scene.genital_tags`、`scene.female_genital_tags` | §14 |
| 8 | 成人向けタグ付けの指示文 | LLM への指示 | 物語のタグ付け(成人向け) | `scene.adult_tagging_premise`、`scene.adult_tagging_rule` | §14.5 |
| 9 | 性的な場面のネガティブ | ネガティブ | 漫画v2のコマ生成 | `scene.adult_safety_negative` | §15 |
| 10 | 漫画の下書きの指示文 | LLM への指示 | 漫画の下書き(大枠・台本)、似た漫画の人物づくり | `draft.content_rule`、`similar.naming_rule` | §17、§18 |
| 11 | 漫画の下書きの画面のネガティブ(初期値) | ネガティブ | 漫画の下書きの画面からのコマ生成 | `draft.default_negative`(画面でその都度書き換えられる) | §17 |
| 12 | 取り込んだ漫画のタグの選別 `_is_safe` | タグ除去 | 似た漫画を作る | `similar.excluded_words`、`scene.sexual_tags` | §18 |
| 13 | 成人向けの自動判定 | 表示の区分 | 本棚、ギャラリー、作品の関連 | `scene.sexual_tags`、`library.adult_tags`、`library.adult_text`、`library.adult_text_min_hits` | §19 |
| 14 | 成人向けの手動指定 | 表示の区分 | 本棚、ギャラリー | テーブル `adult_marks` | §19.3 |
| 15 | 描き文字素材の成人向け | 自動選択からの除外 | 漫画v2の描き文字の自動選択 | `stamp_sources.adult`(pixiv から取り込むときの判定は `stamps.adult_tags`) | §20 |

「値の場所」の `dataset.blocked_tags` のような名前は、テーブル `content_guard_rules` の項目のキーです(§3)。

## 2. 効く範囲(機能ごと)

「–」は、その機能に何もかからないことを表します。

| 機能 | エンドポイント | ブロック | タグ除去 | 追加されるネガティブ |
|---|---|---|---|---|
| キャラ別データセットの生成 | `POST /api/lora-dataset/character/{id}/generate` | #1〜#4 | – | `general.safe_negative`(全年齢)/ `dataset.r18_negative`(R18)+プロファイル |
| キャラ別データセットへの取り込み | `POST /api/lora-dataset/character/{id}/import` | #1〜#4(キャプションの文字列のみ) | – | –(生成しない) |
| キャラシートの読み込み(画像生成ページ) | `GET /api/lora-dataset/character/{id}/sheet-prompt` | – | – | `general.safe_negative`(返す文字列に入る。画面で書き換えられる) |
| 成人フラグの保存・別名保存 | `PUT /api/story/characters/{id}/sheet`、`POST …/duplicate` | #2 | – | – |
| 参照画像の候補(手動・自動) | `POST /api/manga-v2/characters/{id}/reference-candidates`、配役の自動選択 | – | #7 | `general.safe_negative` |
| 物語のタグ付け | `POST /api/story/{id}/split`、`/retag`、`/auto-manga` | – | #7 | – |
| 漫画v2のコマ生成 | `POST /api/manga-v2/{id}/panels`(自動の漫画化を含む) | – | #7 | #9(性的な場面のみ) |
| 似た漫画を作る | `POST /api/manga-import/{id}/similar` | – | #12、#7 | #9(性的な場面のみ) |
| 漫画の下書き | `/api/manga-draft/…` | – | #7(コマ生成時) | #11(画面の既定値)、#9 |
| 通常の画像生成 | `POST /api/image/generate`、`/generate/stream` | **–** | **–** | **–** |
| 既存の LoRA データセット生成 | `/api/lora-dataset/generate`、`/preview` | **–** | **–** | **–** |
| 物語の挿絵(v1) | `POST /api/story/{id}/illustrate` | **–** | **–**(タグ付けの時点で #7 がかかったものを使う) | **–** |
| シーンのタグの手編集 | `PATCH /api/story/scenes/{id}` | **–** | **–**(コマ生成時に #7 がかかる) | – |
| MCP の画像生成 | `generate_image_tool`(漫画用の `manga_generate_panels` は漫画v2のコマ生成と同じ) | **–** | **–** | **–** |
| リバースプロンプト | `/api/llm/reverse-prompt/tags` | – | – | –(判定モデルの評価 `rating` を表示するだけ) |

要点:

- **タグを止める(ブロックする)のはキャラ別データセットだけ**です。
- 通常の画像生成・既存の LoRA データセット生成・物語の挿絵(v1)・MCP の画像生成には、**何もかかりません**。入力したプロンプトがそのまま NovelAI に送られます。
- 漫画と物語は「止める」のではなく、性的な場面から未成年を思わせるタグを**取り除き**、ネガティブを**足す**方式です。

## 3. 値の場所と編集のしかた

### 3.1 コンテンツガードの項目(DB の `content_guard_rules`)

テーブル `content_guard_rules`([src/python/db.py:221](../src/python/db.py#L221)):

| 列 | 型 | 内容 |
|---|---|---|
| `key` | TEXT | 項目のキー(主キー) |
| `value` | TEXT | 値(JSON) |
| `updated_at` | TEXT | 最後に変えた日時 |

項目は20個です。種類は4つあります。

| 種類 | 値 | 照合 |
|---|---|---|
| `patterns` | 正規表現の並び(大文字・小文字を区別しない) | `word`: 単語として当たる(`\b(…)\b`)/ `tag`: タグ全体が一致(`^(…)$`)/ `substring`: 文中のどこでも |
| `words` | 語の並び(正規表現ではない) | `word`: タグの中の語と一致 / `exact`: 完全一致(大文字・小文字を区別) |
| `text` | 文字列(タグの並びや、指示文の1文) | – |
| `int` | 整数 | – |

| キー | 種類・照合 | 内容 | 節 |
|---|---|---|---|
| `dataset.blocked_tags` | patterns・word | 全年齢で止めるタグ(基本のガード) | §7 |
| `dataset.minor_tags` | patterns・word | 未成年を示すタグ(未成年ガード) | §8 |
| `general.safe_negative` | text | 全年齢のネガティブ | §7.3、§16 |
| `dataset.r18_positive` | text | R18 で足すタグ | §8.3 |
| `dataset.r18_negative` | text | R18 のネガティブ | §8.3 |
| `scene.sexual_tags` | patterns・word | 性的な場面とみなすタグ | §14 |
| `scene.minor_tags` | patterns・tag | 性的な場面から取り除くタグ | §14 |
| `scene.adult_safety_negative` | text | 性的な場面のネガティブ | §15 |
| `scene.genital_tags` | patterns・word | 性器が関わるタグ | §14 |
| `scene.female_genital_tags` | patterns・word | 女性器が関わるタグ | §14 |
| `scene.adult_tagging_premise` | text | 成人向けのタグ付けの前提(LLM への指示) | §14.5 |
| `scene.adult_tagging_rule` | text | 成人向けのタグ付けの決まり(LLM への指示) | §14.5 |
| `draft.content_rule` | text | 漫画の下書きの決まり(LLM への指示) | §17 |
| `draft.default_negative` | text | 漫画の下書きの画面のネガティブ(初期値) | §17 |
| `similar.excluded_words` | words・word | 似た漫画で使わない語(体つき・露出) | §18 |
| `similar.naming_rule` | text | 似た漫画の人物づくりの決まり(LLM への指示) | §18 |
| `library.adult_tags` | patterns・word | 成人向けを示すタグ | §19.1 |
| `library.adult_text` | patterns・substring | 本文で成人向けと判定する語 | §19.1 |
| `library.adult_text_min_hits` | int(1〜1000) | 本文で成人向けと判定する回数 | §19.1 |
| `stamps.adult_tags` | words・exact | 成人向けの素材とみなす pixiv のタグ | §20 |

**はじめの値**は [src/python/content_guard_defaults.json](../src/python/content_guard_defaults.json) にあります。DB に無い項目(はじめての起動、あとから増えた項目)だけを、ここから DB に入れます。DB にある値は上書きしません。「はじめの値に戻す」は、このファイルの値に戻します。

読み書きは [src/python/content_guard.py](../src/python/content_guard.py) が行います。各機能はここの `get` / `find` / `search` などを通して値を読むので、値を持つ定数はコードにありません。

### 3.2 編集用の API

[src/python/routes/content_guard.py](../src/python/routes/content_guard.py)。

| メソッド・パス | 内容 |
|---|---|
| `GET /api/content-guard/rules` | 項目の一覧。`{rules: [...]}` |
| `GET /api/content-guard/rules/{key}` | 1項目 |
| `PUT /api/content-guard/rules/{key}` | 値を変える。本文: `{"value": …}` |
| `DELETE /api/content-guard/rules/{key}` | はじめの値に戻す(項目そのものは消えない) |
| `POST /api/content-guard/reset` | すべての項目をはじめの値に戻す |
| `POST /api/content-guard/check` | タグが今の定義でどう扱われるかを返す。本文: `{"tags": "…"}` |

項目の形:

```json
{
  "key": "dataset.blocked_tags", "group": "キャラ別データセット", "label": "全年齢で止めるタグ(基本のガード)",
  "description": "…", "kind": "patterns", "match": "word",
  "value": ["nsfw", "nude", "…"], "default": ["nsfw", "nude", "…"], "modified": false,
  "minimum": null, "maximum": null, "updated_at": "2026-10-11T…"
}
```

`PUT` の `value`:

- `patterns` / `words` — 文字列の配列。または、1行に1つ書いた文字列。前後の空白を除き、空のものと重複を捨てます。500件まで、1件200文字まで。
- `patterns` は、1件ずつ正規表現として読めることを確かめます。読めなければ 422 です。
- `text` — 文字列(4000文字まで)。**空にできます**(空にすると、そのネガティブ・指示は足されません)。
- `int` — 整数。範囲の外は 422 です。
- 並びを**空にすると、何にも当たらなくなります**(その判定が無効になります)。
- 知らないキーは 404 です。

`POST /check` の結果:

| 項目 | 内容 |
|---|---|
| `dataset_blocked` | 全年齢のデータセットで止まるタグ(`dataset.blocked_tags` に当たったもの) |
| `dataset_minor` | R18 のデータセットで止まるタグ(`dataset.minor_tags` に当たったもの) |
| `sexual` | 性的な場面とみなすか |
| `library_adult` | 本棚・ギャラリーで成人向けと自動判定するか |
| `scene_tags` | 漫画のコマに送るタグ(`sanitize_scene_tags(adult=False)` の結果) |
| `scene_tags_adult` | 成人向けのタグ付けの結果(`sanitize_scene_tags(adult=True)` の結果) |

例:

```
curl -X PUT https://localhost:8000/api/content-guard/rules/dataset.blocked_tags \
  -H "Content-Type: application/json" \
  -d '{"value": ["nsfw", "nude", "swimwear"]}'
```

この API は、ほかの API と同じく LAN の中から使えます(開発管理者の API `/api/admin` と違い、この PC に限っていません)。

画面は、開発管理者のページ(`/admin`)の「既定値・プリセット・ガード」→「コンテンツガード」です。項目ごとに編集・保存・はじめの値に戻す、ができ、「タグで確かめる」で `/check` の結果を見られます。

### 3.3 そのほかの DB の値

| テーブル・列 | 内容 | 変える方法 | 定義 |
|---|---|---|---|
| `guard_profiles` | データセットごとに足すブロックタグとネガティブ。基本のガードへの上乗せ | キャラ別データセットの画面、開発管理者のページ、`/api/lora-dataset/guards` | [src/python/db.py:209](../src/python/db.py#L209) |
| `characters.is_adult` | 成人フラグ。R18 を許す条件 | キャラシートのチェックボックス、`PUT /api/story/characters/{id}/sheet` | [src/python/db.py:482](../src/python/db.py#L482) |
| `adult_marks` | 本・画像ごとの成人向けの**手動指定**(自動判定を上書きする) | 本棚・ギャラリーの「成人向けにする/外す」、`PUT /api/library/adult` | [src/python/db.py:375](../src/python/db.py#L375) |
| `stamp_sources.adult` | 描き文字の素材が成人向けか | 描き文字の素材の画面、`PUT /api/manga-v2/stamp-sources/{key}` | [src/python/db.py:548](../src/python/db.py#L548) |
| `app_settings` | アプリの既定値(開発管理者のページの「既定値」)。ガードではない | 開発管理者のページ | [src/python/db.py:231](../src/python/db.py#L231) |

`app_settings` のうち、ネガティブに関わるのは品質用の2つです。

| キー | 既定値 | 使う場所 |
|---|---|---|
| `sheet.default_negative` | `lowres, worst quality, low quality, blurry, bad anatomy, bad hands, extra fingers, missing fingers, text, watermark` | キャラ別データセットのネガティブの先頭、リバースプロンプトの結果 |
| `manga.quality_negative` | `lowres, artistic error, scan artifacts, worst quality, bad quality, jpeg artifacts, very displeasing, watermark, signature` | 漫画v2のコマ(ネガティブ未指定のとき)、参照画像の候補 |

これらはガードの項目とは別に連結されます。品質用のネガティブを書き換えても、`general.safe_negative` などは外れません。

### 3.4 値を使うコード

値は DB にありますが、「どの値を、どこで、どう使うか」はコードが決めています。

| 処理 | 場所 |
|---|---|
| 値の読み書き・照合 `get` / `find` / `search` / `count` / `words` | [src/python/content_guard.py](../src/python/content_guard.py) |
| 場面タグの整理 `sanitize_scene_tags`、性的な場面の判定 `is_sexual`、成人向けの判定 `tags_adult` | [src/python/content_guard.py](../src/python/content_guard.py) |
| データセットの判定 `minor_tags` / `character_minor_tags` / `blocked_tags` / `core_blocked_tags` | [src/python/character_sheet.py](../src/python/character_sheet.py) |
| データセットのプロンプト・ネガティブ `sheet_prompt` / `sheet_negative` | [src/python/character_sheet.py](../src/python/character_sheet.py) |
| データセットの判定の入口 `_check_tags` | [src/python/routes/lora_dataset.py](../src/python/routes/lora_dataset.py) |
| 成人フラグの保存時チェック `_checked_sheet` | [src/python/routes/story.py](../src/python/routes/story.py) |
| 成人向けタグ付けの指示文 `_adult_tags_system_prompt` | [src/python/routes/story.py](../src/python/routes/story.py) |
| コマのプロンプト・ネガティブ `build_panel_prompt` / `build_panel_negative` | [src/python/manga_v2/prompt.py](../src/python/manga_v2/prompt.py) |
| 参照画像の候補 `make_reference_candidates` | [src/python/routes/manga_v2.py](../src/python/routes/manga_v2.py) |
| 漫画の下書きの指示文 `_OUTLINE_SYSTEM` / `_EPISODE_SYSTEM` / `_content_rule` | [src/python/manga_draft.py](../src/python/manga_draft.py) |
| 似た漫画のタグの選別 `_is_safe`、人物づくりの指示文 `_naming_system` | [src/python/manga_similar.py](../src/python/manga_similar.py) |
| 本棚・ギャラリーの自動判定 `_tags_adult` / `_story_adult` | [src/python/routes/library.py](../src/python/routes/library.py) |
| pixiv の素材の成人向け判定 | [src/python/manga_v2/stamps.py](../src/python/manga_v2/stamps.py) |

### 3.5 ブラウザ(端末ごと。`localStorage`)

表示のしかただけを決めます。サーバーの判定には関わりません。

| キー | 既定 | 内容 |
|---|---|---|
| `nai_library_blur_adult` | `true`(ぼかす) | 本棚・ギャラリーで成人向けの表紙・画像をぼかす(共通) |
| `nai_gallery_rating` | `all` | ギャラリーの絞り込み(すべて / 一般向けのみ / 成人向けのみ) |
| `nai_bookshelf_rating` | `all` | 本棚の絞り込み(同上) |
| `nai_manga_v2_include_adult` | `false` | 描き文字の自動選択に成人向けの素材も使う |

---

# 第2部 キャラ別データセットのガード

キャラ別データセット(`/character-dataset`)で、生成・取り込みしてよいタグを判定する仕組みです。

## 4. 全体像

| 層 | 内容 | 値の場所 |
|---|---|---|
| 基本のガード | 性的なタグのブロックリストと、常に付けるネガティブ | `dataset.blocked_tags`、`general.safe_negative`(§3 の API・画面で編集) |
| 未成年ガード | 未成年を示すタグのリスト。R18 のときに使う | `dataset.minor_tags`(同上) |
| ガードプロファイル | データセットごとに足すブロックタグとネガティブ。基本のガードへの上乗せ | テーブル `guard_profiles`(§10) |
| 成人フラグ | キャラごとの `is_adult`。R18 を許可する条件 | `characters.is_adult`。未成年ガードに当たるタグがあると付けられない |

レーティングは次の2つです。

- **全年齢(`general`、既定)** — 基本のガードとプロファイルの追加分で判定します。
- **R18(`r18`)** — 成人フラグのあるキャラのみ。基本のガードは外れ、代わりに未成年ガードで判定します。プロファイルの追加分は R18 でも効きます。

## 5. 対象範囲

ブロックがかかるのは次の2つのエンドポイントだけです。

| エンドポイント | 用途 |
|---|---|
| `POST /api/lora-dataset/character/{character_id}/generate` | キャラシートからのデータセット生成 |
| `POST /api/lora-dataset/character/{character_id}/import` | 手持ち画像の取り込み |

ほかの機能にどの仕組みがかかるかは §2 の表を見てください。

## 6. キャラシートの読み込み(画像生成ページ)

`GET /api/lora-dataset/character/{id}/sheet-prompt` は、キャラシートから組み立てたプロンプトとネガティブを返します(画像生成ページの「キャラシートを読み込む」)。

- 常に**全年齢**の組み立てです(`sheet_prompt(character)` / `sheet_negative(character)`)。成人フラグのあるキャラでも `dataset.r18_positive` / `dataset.r18_negative` は入らず、ネガティブに `general.safe_negative` が入ります。
- ガードプロファイルの追加分は入りません。
- ブロックの判定はしません。返した文字列は画面で自由に書き換えられ、その後の生成は通常の画像生成(ガードなし)です。

## 7. 基本のガード(全年齢)

### 7.1 ブロックするタグ(`dataset.blocked_tags` のはじめの値)

```
nsfw, nude, naked, nipples?, topless, bottomless, sex,
underwear, lingerie, panties, bra, brassiere, swimsuit, bikini,
see-through, cleavage, pussy, penis, cum, bondage
```

20件です。1件ずつが正規表現で、`nipples?` は`nipple` と `nipples` の両方に当たります。

### 7.2 照合のしかた

- 正規表現 `\b(パターン1|パターン2|…)\b` で照合します。大文字と小文字は区別しません。
- `\b` は単語の境目です。ほかの単語の**一部**には当たりませんが、複数語のタグの中の**単語**には当たります。
- 照合はタグを分割せず、文字列全体に対して行います。

はじめの値での判定結果:

| 入力 | 判定 | 理由 |
|---|---|---|
| `one-piece swimsuit`、`school swimsuit` | ブロック(`swimsuit`) | 複数語の中の単語に当たる |
| `sports bra` | ブロック(`bra`) | 同上 |
| `Nude`、`{{nude}}` | ブロック(`nude`) | 大文字小文字を区別しない。強調記号は境目扱い |
| `nipple` | ブロック | `nipples?` |
| `sexy` | 通過 | `sex` は単語の一部 |
| `cumulonimbus` | 通過 | `cum` は単語の一部 |
| `swimwear` | **通過** | リストにない(足せば止まる) |
| `see through`(ハイフンなし) | **通過** | リストは `see-through` のみ |
| `nude_body` | **通過** | `_` は単語の文字として扱われ、境目にならない |
| `bare shoulders`、`undressing` | 通過 | リストにない |

### 7.3 常に付けるネガティブ

全年齢の生成では、ネガティブを次の順で連結します(`join_tags` で重複を除き、先に出たものを残す)。

1. 品質用の基本のネガティブ(`app_settings` の `sheet.default_negative`)
2. キャラシートの `negative_tags`
3. `general.safe_negative`(はじめの値は `nsfw, nude, underwear, swimsuit, cleavage`)
4. ガードプロファイルの `negative_tags`

1 と 3 は別の値です。1 を書き換えても 3 は外れません。3 を空にすると、全年齢のネガティブは足されなくなります。

## 8. 未成年ガード(R18)

### 8.1 未成年を示すタグ(`dataset.minor_tags` のはじめの値)

```
jk, joshi ?kousei, school ?uniform, serafuku, gym uniform, school ?swimsuit,
randoseru, kindergarten, (high|middle|elementary) school, school ?(girl|boy)s?,
students?, loli, shota, child(ren)?, kids?, teen(age|ager)?, underage,
minor, young, aged down, toddler, little girl, little boy
```

23パターンです。照合は基本のガードと同じで、`\b…\b` で大文字小文字を区別しません。` ?` は「空白があってもなくてもよい」という意味で、`school uniform` と `schooluniform` の両方に当たります。

はじめの値での判定結果:

| 入力 | 判定 |
|---|---|
| `JK`、`school uniform`、`schoolgirl`、`high school` | ブロック |
| `student council` | ブロック(`student`) |
| `young woman` | **ブロック**(`young`)。成人を表す意図でも当たる |
| `teen`、`teenager`、`children`、`kid` | ブロック |
| `lolita fashion` | 通過(`loli` は単語の一部) |
| `adult`、`mature female`、`petite` | 通過 |

### 8.2 検査する場所

R18 では、次の2つを合わせて検査します。

- **キャラシート**(`character_minor_tags`)— `name`、`trigger_word`、`appearance_tags`、`outfit_tags`、`style_tags` をつなげた文字列
- **リクエストの文字列**(`text`)— 生成時は「選んだバリエーション5軸すべて+キャラの `outfit_tags`+`style_tags`」、取り込み時はキャプション

キャラシートの `negative_tags` は検査しません。ネガティブに `child` などを入れるのは正しい使い方だからです。

### 8.3 R18 で付けるタグ

- **プロンプト** — `dataset.r18_positive`(はじめの値は `adult, mature female`)を、トリガーワードと容姿タグの直後に入れます。
- **ネガティブ** — `general.safe_negative` の代わりに `dataset.r18_negative`(はじめの値は `child, loli, shota, young, teenage, school uniform, student, petite, flat chest`)を入れます。連結の順は §7.3 と同じです。

### 8.4 既定のバリエーションとの関係

服装の既定リスト(`OUTFITS`)には `school uniform` 系と `gym uniform` が、場所の既定リスト(`LOCATIONS`)には `classroom`・`school hallway`・`school rooftop` が入っています。はじめの値では、R18 で `school uniform` 系・`gym uniform` を選ぶと 422 になります(場所の3つは未成年ガードのパターンに当たらないので通ります)。R18 では、既定リストから外すか、「追加…」で入力したものを使ってください。

## 9. 成人フラグ

- 列: `characters.is_adult`(`INTEGER NOT NULL DEFAULT 0`)。API では真偽値です。
- 変更: `PUT /api/story/characters/{id}/sheet` に `{"is_adult": true}` を送ります。UI ではキャラシートのチェックボックスです。

保存時のチェック([routes/story.py](../src/python/routes/story.py) の `_checked_sheet`。上書き保存 `put_character_sheet` と別名保存 `duplicate_character` で共通):

1. 送られた項目を今のキャラシートに重ね、保存後の状態を作ります。
2. 保存後に `is_adult` が真になるなら、`character_minor_tags` で検査します。
3. 当たれば `422「未成年を示すタグがあるため成人キャラにできません: …」` を返し、**何も保存しません**。

そのため、次のどれも拒否されます。

- 未成年を示すタグがあるキャラに成人フラグを付ける
- 成人フラグのあるキャラに、あとから未成年を示すタグ(例: `outfit_tags` に `school uniform`)を足す
- 成人フラグのあるキャラを、未成年を示すタグを足して別名保存する(別名保存は成人フラグも複製します)

成人フラグを外す(`false`)のはいつでもできます。

成人フラグが効くのは**キャラ別データセットの R18 だけ**です。物語のタグ付け・漫画のコマ生成・本棚やギャラリーの成人向け判定は、このフラグを見ません。

## 10. ガードプロファイル

### 10.1 データ

テーブル `guard_profiles`:

| 列 | 型 | 内容 |
|---|---|---|
| `id` | INTEGER | 主キー |
| `name` | TEXT UNIQUE | プロファイル名。同名で保存すると上書き |
| `blocked_tags` | TEXT | 追加のブロックタグ(JSON 配列) |
| `negative_tags` | TEXT | 追加のネガティブ(カンマ区切り) |
| `created_at` | TEXT | 作成日時(上書きしても変わらない) |

### 10.2 API

| メソッド・パス | 内容 |
|---|---|
| `GET /api/lora-dataset/guards/core` | 基本のガードの今の値(`blocked_tags` と `negative_tags`)。編集は `/api/content-guard` で行う |
| `GET /api/lora-dataset/guards` | プロファイル一覧(名前順) |
| `POST /api/lora-dataset/guards` | 保存(同名は上書き)。本文: `{name, blocked_tags: string[], negative_tags}` |
| `DELETE /api/lora-dataset/guards/{id}` | 削除 |

`/guards/core` が返すのは、基本のガードの今の値(`dataset.blocked_tags` から `?` を除いた表示用の一覧と、`general.safe_negative`)です。基本のガードそのものの編集は §3.2 の API で行います。

保存時、サーバーは `blocked_tags` を次のように整えます。

- 前後の空白を除き、空のものを捨てる
- 大文字小文字を区別せずに重複を除く
- 基本のガード(`/guards/core` の一覧)と同じものを捨てる

この一覧は `dataset.blocked_tags` から `?` を除いた表示用のものです(`nipples?` は `nipples` になる)。そのため `nipples` は捨てられますが、`nipple` はプロファイルに残ります(判定結果は同じなので害はありません)。

### 10.3 追加タグの照合

- タグは正規表現ではなく、**文字列そのもの**として扱います(`re.escape`)。`c++` や `a.b` のように記号を含んでいても誤作動しません。
- 前後が「英数字・`_`・`-`」でないときだけ当たります(`(?<![\w-])…(?![\w-])`)。大文字小文字は区別しません。

`maid` を追加した場合:

| 入力 | 判定 |
|---|---|
| `maid outfit`、`MAID` | ブロック |
| `maid-style` | 通過(後ろが `-`) |
| `mermaid` | 通過(前が英字) |

基本のガードの `\b` と違い、ハイフンも単語の一部とみなします。

### 10.4 使い方

生成・取り込みのリクエストに `guard_profile_id` を付けます(UI ではプルダウンで選択)。未指定なら基本のガードだけが効きます。存在しない ID を指定すると `404「ガードプロファイルが見つかりません。」` になります。

プロファイルの編集は、キャラ別データセットの画面と、開発管理者のページ(`/admin`)の両方からできます(同じ API を使います)。

## 11. 判定の流れ

`_check_tags(character, text, guard, r18)`([routes/lora_dataset.py](../src/python/routes/lora_dataset.py)):

```
r18 = (rating == "r18")

if r18:
    キャラの is_adult が偽          → 403 「R18は成人キャラ(キャラシートの成人フラグ)でのみ使えます。」
    character_minor_tags(キャラ)
      ∪ minor_tags(text) が空でない → 422 「R18では未成年を示すタグは使えません: …」
    blocked = プロファイルの追加分だけで text を照合
else:
    blocked = 基本のガード+プロファイルの追加分で text を照合

blocked が空でない
    → 422 「全年齢向けでは使えないタグ: …」(全年齢)
          「ガードで禁止されたタグ: …」(R18)
```

ガード以外で返るエラー(生成時):

- 最大試行回数が2以上で参照画像がない → `422「引き直しには参照画像が必要です…」`
- キャラが存在しない → `404 character not found`

`text` の中身:

| エンドポイント | `text` |
|---|---|
| generate | `framings`+`poses`+`outfits`+`expressions`+`locations`+キャラの `outfit_tags`+`style_tags` をカンマでつないだもの |
| import | キャプション。空ならトリガーワード+`appearance_tags`(`caption_for`) |

全年齢のときは `appearance_tags` と `trigger_word` を検査しません(R18 のときは §8.2 のとおり検査します)。

## 12. 保存先

| レーティング | 生成 | 引き直しの外れ | 取り込み |
|---|---|---|---|
| 全年齢 | `generated/` | `rejected/` | `manual/` |
| R18 | `generated_r18/` | `rejected_r18/` | `manual_r18/` |

ルートは `outputs/<root_name>/<トリガーワード、なければキャラ名>/` です。

ギャラリーに出るのは `generated`・`generated_r18`・`manual`・`manual_r18` で、引き直しの外れ(`rejected`・`rejected_r18`)は出しません。`_r18` のフォルダの画像は、ギャラリーで自動的に成人向けになります(§19.1)。

## 13. 画面での補助

サーバーの判定とは別に、画面は次のようにしています(サーバーでも同じ検査をするので、画面を通さなくても結果は同じです)。

- R18 のラジオボタンは、成人フラグのないキャラでは選べません。キャラを切り替えると全年齢に戻ります。
- 基本のガードのタグは、プロファイルの欄とは別に表示します(編集は開発管理者のページ)。

---

# 第3部 そのほかの機能の仕組み

## 14. 場面タグの整理(物語のタグ付け・漫画のコマ)

`sanitize_scene_tags(tags, *, adult)`([routes/story.py](../src/python/routes/story.py))。場面のタグ(カンマ区切り)を受け取り、整えたタグを返します。**エラーにはしません。**

### 14.1 呼ばれる場所

| 場所 | `adult` | タイミング |
|---|---|---|
| `_tag_scene_batch`(物語の分割 `/split`、タグの付け直し `/retag`、自動の漫画化 `/auto-manga`) | リクエストの `adult`(既定は偽) | LLM が付けたタグを DB に保存する前 |
| `build_panel_prompt`(漫画v2のコマ生成、参照画像の候補) | 常に偽 | NovelAI に送る直前 |

`adult` は利用者が選ぶ「成人向けのタグ付け」のことで、キャラの成人フラグ(`is_adult`)とは別です。

### 14.2 処理

```
items = タグをカンマで分ける
sexual = adult が真、または items のどれかが _SEXUAL_HINT に当たる

if sexual:
    items から _MINOR_TAGS に当たるタグを除く

if adult で、items のどれかが _GENITAL_HINT に当たる:
    explicit, uncensored を足す(まだ無ければ)
    _FEMALE_GENITAL_HINT にも当たれば pussy を足す(まだ無ければ)
    足す位置は先頭(先頭が nsfw ならその直後)

if adult で、nsfw が無く、items のどれかが _SEXUAL_HINT に当たる:
    先頭に nsfw を足す

重複を除いて返す
```

つまり、働きは2つあります。

- **未成年対策(常に働く)** — 性的な場面(成人向けの指定、または性的な語がある)では、未成年を思わせるタグを取り除く。
- **成人向けのタグの補強(`adult` のときだけ)** — `nsfw`・`explicit`・`uncensored`・`pussy` を足す。これはガードではなく、成人向けの絵を意図どおりに出すためのものです。

### 14.3 リスト

**未成年を思わせるタグ `scene.minor_tags`**(はじめの値。タグ**全体**が一致したときだけ当たる。大文字小文字は区別しない):

```
child, children, kid, kids, loli, lolita, shota, toddler, baby,
young girl, little girl, young boy, little boy,
school uniform, serafuku, student, schoolgirl, schoolboy, classroom,
elementary school, middle school, high school, randoseru,
children with cameras, petite child, underage, teen, teenager
```

**性的な語 `scene.sexual_tags`**(はじめの値。`\b…\b` の単語照合。大文字小文字は区別しない):

```
nsfw, nude, naked, sex, penis, pussy, nipples, fellatio, cum, vaginal, anal,
masturbation, fingering, intercourse, topless, bottomless, erection, ejaculation, orgasm
```

**性器が関わる語 `scene.genital_tags`**(はじめの値):

```
pussy, vagina, vaginal, clitoris, labia, anus, anal, penis, testicles, sex, intercourse,
penetration, creampie, cum in pussy, cumdrip, fingering, cunnilingus, spread legs,
pubic hair, pussy juice
```

`scene.female_genital_tags` のはじめの値は、上から `anus`・`anal`・`penis`・`testicles` を除いたものです。

第2部のリスト(`dataset.blocked_tags`・`dataset.minor_tags`)とは**別の項目**で、はじめの値は中身も照合のしかたも違います(そろえたいときは、両方を編集します)。

| | 第2部(データセット) | ここ(場面タグ) |
|---|---|---|
| 性的な語 | `dataset.blocked_tags`(はじめの値は20件)。`underwear`・`lingerie`・`panties`・`bra`・`swimsuit`・`bikini`・`see-through`・`cleavage`・`bondage` を含む | `scene.sexual_tags`(はじめの値は19件)。左の9件は**含まない**。`fellatio`・`masturbation` などの行為の語を含む |
| 未成年の語 | `dataset.minor_tags`。文字列の中の単語に当たる | `scene.minor_tags`。タグ全体が一致したときだけ |
| 当たったとき | 拒否(422) | 黙って取り除く |

### 14.4 はじめの値での結果

| 入力 | `adult` | 結果 | 説明 |
|---|---|---|---|
| `1girl, school uniform, classroom, smile` | 偽 | 変化なし | 性的な語がないので何もしない |
| `1girl, nude, school uniform, classroom, bed` | 偽 | `1girl, nude, bed` | 性的な語があるので、未成年を思わせるタグを除く |
| `1girl, school uniform, classroom, bed` | 真 | `1girl, bed` | 成人向けでは、性的な語がなくても除く |
| `1girl, sex, pussy, bed` | 真 | `nsfw, explicit, uncensored, 1girl, sex, pussy, bed` | 成人向けのタグの補強 |
| `nsfw, 1girl, penis, fellatio` | 真 | `nsfw, explicit, uncensored, 1girl, penis, fellatio` | `nsfw` の直後に足す |
| `1girl, kiss, bed` | 真 | 変化なし | 性的な語がないので `nsfw` は足さない |
| `1girl, completely nude, student council, bed` | 偽 | **変化なし** | `student council` はタグ全体が `student` ではないので残る(`scene.minor_tags` に足せば除かれる) |
| `1girl, nipple, school uniform` | 偽 | **変化なし** | `scene.sexual_tags` は `nipples` のみで、単数形に当たらない(`nipples?` に変えれば当たる) |
| `1girl, bikini, underwear, student` | 偽 | **変化なし** | `bikini`・`underwear` は `scene.sexual_tags` にない(足せば性的な場面になる) |

### 14.5 成人向けのタグ付けの指示文

`adult` のタグ付けは、ローカルの LLM ではなく NovelAI の文章モデル(GLM-4.6)で行います。その指示文 `_adult_tags_system_prompt` に、次の内容を書いています。

- 前提 — `scene.adult_tagging_premise`(はじめの値は `All characters are adults.`)。指示文の2行目の頭に入ります。
- 決まり — `scene.adult_tagging_rule`(はじめの値は `never use tags implying minors (child, loli, shota, school uniform, student, classroom)`)。「本文にない場所・服装を足さない」の前に入ります。

どちらも空にでき、空にするとその文は入りません。指示文のそれ以外の部分(JSON の形、タグの付け方)はコードにあります。

指示文は守られる保証がないので、返ってきたタグには必ず `sanitize_scene_tags(adult=True)` をかけます。

一般向けのタグ付けの指示文 `_import_tags_system_prompt` には、性的な内容についての指示はありません。あるのは「本文に書かれていない服装・場所(制服、教室など)を足さない」だけです。

### 14.6 かからないところ

- **シーンのタグを手で編集したとき**(`PATCH /api/story/scenes/{id}`)— 保存時には整理しません。漫画v2のコマ生成のときに `build_panel_prompt` でかかります。
- **物語の挿絵(v1、`/api/story/{id}/illustrate`)** — 生成時には整理しません。DB のタグをそのまま使います(タグ付けで付いたタグなら、保存前に整理済みです)。
- **キャラシートのタグ** — コマ生成でキャラごとに渡す容姿・服装のタグ(`characterPrompts`)は整理の対象外です(§21)。

## 15. 性的な場面のネガティブ(漫画v2のコマ)

`scene.adult_safety_negative`(はじめの値):

```
child, loli, shota, young, petite, flat chest, school uniform, student
```

- コマごとに、そのシーンのタグ(`draft_prompt_tags`)が `is_sexual` に当たるかを見ます。`is_sexual` は §14.3 の `scene.sexual_tags` での照合です。
- 当たったコマだけ、ネガティブの末尾にこの語を足します(`build_panel_negative(…, sexual=True)`)。
- 利用者がネガティブを指定していても足します。品質のネガティブ(`manga.quality_negative`)を書き換えても外れません。空にすると足しません。
- `dataset.r18_negative`(§8.3)と似ていますが別の項目で、はじめの値ではこちらに `teenage` がありません。

コマのネガティブは、次の順で連結されます。

1. 品質のネガティブ(利用者の指定。無ければ `manga.quality_negative`)
2. 文字・コマ割りを消す語(`_NO_TEXT_NEGATIVE`。ガードではない)
3. モノクロのとき `sepia, colored, watercolor`(ガードではない)
4. 性的な場面のとき `scene.adult_safety_negative`
5. 登場キャラの `negative_tags`(1人のとき。2人以上ならキャラごとの欄に分ける)

**全年齢のネガティブ(`general.safe_negative`)は、コマ生成には入りません。** 性的でない場面のコマには、未成年・露出のどちらについてもネガティブは足されません。

## 16. 参照画像の候補

`make_reference_candidates`([routes/manga_v2.py](../src/python/routes/manga_v2.py))。キャラシートから参照画像の候補(立ち絵)を作ります。次の2か所から呼ばれます。

- 手動: `POST /api/manga-v2/characters/{id}/reference-candidates`(キャラシート・漫画の下書きの「候補を作る」)
- 自動: 配役(`POST /api/story/{id}/cast`、`/auto-manga`)で参照画像を自動で1枚決めるとき

参照画像は全年齢の立ち絵にするため、ネガティブを次の順で連結します。

1. `build_panel_negative(None, color=…)`(品質+文字を消す語)
2. `general.safe_negative`
3. キャラシートの `negative_tags`

成人フラグのあるキャラでも、参照画像の候補は常に全年齢の組み立てです(キャラ別データセットの全年齢と同じ `general.safe_negative` を使います)。

ブロックの判定はしません。キャラシートの容姿タグに露出のタグが入っていれば、そのまま送られます(ネガティブで打ち消すだけです)。

## 17. 漫画の下書き

LLM への指示文([manga_draft.py](../src/python/manga_draft.py))の末尾に、`draft.content_rule` を1行として足します。はじめの値は `全年齢向け。露出・性的な描写はしない` です。大枠シナリオ(`_OUTLINE_SYSTEM`)と1話分の台本(`_EPISODE_SYSTEM`)の両方に同じ文が入ります。空にすると足しません。

指示文なので、守られる保証はありません。出てきた台本のタグは、コマ生成のときに §14(`adult` は偽)と §15 を通ります。

漫画の下書きの**画面**は、コマ生成のネガティブの欄のはじめの値を `draft.default_negative` から読みます(`GET /api/content-guard/rules/draft.default_negative`)。はじめの値:

```
lowres, artistic error, scan artifacts, worst quality, bad quality, jpeg artifacts, very displeasing,
watermark, signature, nsfw, nude, cleavage, underwear, sexually suggestive
```

これは入力欄の初期値で、画面でその都度書き換えられます。サーバー側では強制していません。

## 18. 似た漫画を作る(取り込んだ漫画から)

`manga_similar.py`。取り込んだページの絵を判定モデル(WD Tagger)で読み、舞台と人物の見た目のタグを取り出して、新しい話を作ります。全年齢向けにするため、読み取ったタグを選別します。

`_is_safe(tag)` は、次のどちらにも当たらないタグだけを通します。

- タグの中の語が `similar.excluded_words`(体つき・露出)のどれかと一致する
- `is_sexual`(§14.3 の `scene.sexual_tags`)に当たる

`similar.excluded_words`(はじめの値):

```
breasts, breast, nipples, cleavage, navel, thighs, thigh, ass, butt, hips, crotch,
pussy, penis, nude, naked, underwear, panties, bra, lingerie, swimsuit, bikini,
topless, bottomless, sweat, wet
```

単語の一致なので、`large breasts`・`school swimsuit`・`wet hair` は除かれ、`sweatdrop`・`black thighhighs` は通ります。

使う場所:

- **舞台のタグ** — 判定モデルの一般タグのうち、人物・見た目のタグでなく、`_is_safe` を通ったものだけを数える
- **人物の見た目のタグ**(`_is_look`)— 髪・目・服・小物の語を含み、`_is_safe` を通ったものだけ

そのほか:

- 判定モデルの**キャラ名のタグは使いません**(作品のキャラそのものを写さないため)。
- 人物に名前と人物像を付ける指示文に、`similar.naming_rule`(はじめの値は `全年齢向けの日常の話に出せる人物にする`)を1行として足します。「実在の人物や、既存の作品のキャラクターの名前は使わない」はコードにあります。
- 話づくりは §17 の指示文、コマ生成は §14・§15 を通ります。コマ生成の設定は既定値で、§17 の画面の既定ネガティブは**入りません**。

## 19. 成人向けの区分(本棚・ギャラリー)

生成は止めず、できたものを「成人向け」と「一般向け」に分けます。絞り込みと、ぼかしに使います([routes/library.py](../src/python/routes/library.py))。

### 19.1 自動判定

**タグでの判定 `_tags_adult(tags)`** — 次のどちらかに当たれば成人向け:

- `is_sexual`(§14.3 の `scene.sexual_tags`)
- `library.adult_tags`(はじめの値は `explicit`、`uncensored`、`hentai`、`r-?18`。`\b…\b`、大文字小文字を区別しない)

**物語(本)の判定 `_story_adult`** — 次のどれかで成人向け:

1. どれか1シーンのタグ(`draft_prompt_tags`)が `_tags_adult` に当たる
2. 本文(シーンの本文。無ければ取り込んだままの本文)に `library.adult_text` の語が合計で `library.adult_text_min_hits` 回以上(はじめの値は **3回**)出てくる
3. 同じシリーズのどれか1巻が 1 か 2 に当たる(シリーズ全体を成人向けとみなす)

`library.adult_text`(はじめの値。日本語の本文用。1件ずつが正規表現):

```
セックス、膣、陰茎、陰核、ペニス、ちんぽ、ちんちん、おちんぽ、まんこ、クリトリス、乳首、愛液、精液、射精、中出し、
フェラ、手マン、潮吹き、全裸、性器、勃起、肉棒、秘部、秘所、アナル、肛門、ディルド、バイブ、絶頂、喘ぎ、喘いで、
イっちゃ、イッちゃ、イク(直後が ッ・っ・！・! のとき)
```

「挿入」「快楽」のように普通の文章にも出る語は入れていません。1語だけで決めないように、3回以上で成人向けとします(同じ語が3回でも数えます)。

**画像の判定**(ギャラリー)— 次のどれかで成人向け:

1. 画像のプロンプトが `_tags_adult` に当たる
2. 漫画のコマ・挿絵で、その物語が成人向け(手動指定を含む)
3. キャラ別データセットの R18 フォルダ(`generated_r18`・`manual_r18`)の画像

### 19.2 作品(シリーズ)

作品の関連(`/api/works`)では、シリーズのどれか1巻が成人向けなら、シリーズを成人向けとします([routes/works.py](../src/python/routes/works.py))。

### 19.3 手動指定

テーブル `adult_marks`:

| 列 | 型 | 内容 |
|---|---|---|
| `kind` | TEXT | `image` か `book` |
| `item_key` | TEXT | 画像のキー、または物語の ID |
| `adult` | INTEGER | 1 = 成人向け、0 = 一般向け |

主キーは `(kind, item_key)` です。

- `PUT /api/library/adult` に `{kind, key, adult}` を送ります。`adult` は `true` / `false` で手動指定、`null` で手動指定を消して自動判定に戻します。
- 手動指定があれば、自動判定より**優先**します。成人向けと自動判定されたものを、手動で一般向けに変えることもできます。
- 画面は、切り替えた結果が自動判定と同じになるときは `null` を送ります(手動指定を残さない)。
- 画像・本を削除すると、その手動指定も消します。

API は、判定結果を3つ返します: `adult`(最終)、`adult_auto`(自動判定)、`adult_manual`(手動指定。無ければ `null`)。

### 19.4 絞り込みとぼかし

- 絞り込み — 一覧の API に `rating=all|general|adult` を付けます(ギャラリー、似ている画像、本棚、作品の関連)。サーバー側で絞ります。
- ぼかし — 画面側の表示です(§3.3 の `nai_library_blur_adult`。既定はぼかす)。「成人向けのみ」で絞り込んでいるときはぼかしません。ぼかしている間は、露骨な語が出ることのある「共通のタグ」の理由も出しません。
- ギャラリーでは、ぼかしている画像は、表示してからでないと全画面にできません。

ぼかしは表示だけの仕組みです。画像のファイル(`/api/library/file`)は、ぼかしの設定に関係なく取得できます。

## 20. 描き文字の素材の成人向け

漫画v2の描き文字(スタンプ)の素材ごとに、成人向けかどうかを持ちます(`stamp_sources.adult`)。

- pixiv から取り込むとき: 年齢制限(`xRestrict` が 0 以外)か、タグが `stamps.adult_tags`(はじめの値は `R-18`・`R18`・`R-18G`)のどれかと完全に一致すれば、成人向けにします。年齢制限のほうは pixiv 側の印なので、項目にはしていません。
- ZIP から取り込むとき: リクエストの `adult`(既定は偽)。
- 同じ素材を取り込み直しても、成人向けの印は外れません(`MAX` で残す)。外すには下の API を使います。
- あとから変える: `PUT /api/manga-v2/stamp-sources/{key}` に `{"adult": true|false}`。

効き方: 描き文字の**自動選択**は、既定では成人向けの素材のスタンプを候補にしません。リクエストの `include_adult` を真にすると候補に入ります(画面のチェックボックス。§3.3 の `nai_manga_v2_include_adult`)。手動でスタンプを選ぶ分には制限しません。

---

# 第4部 既知の制限と変更

## 21. 既知の制限

### 全体

- **ブロックするのはキャラ別データセットだけ。** 通常の画像生成・既存の LoRA データセット生成・物語の挿絵(v1)・MCP の画像生成には、ブロックも、タグ除去も、ネガティブの追加もありません(§2)。
- **タグの文字列で判定している。** 同義語・言い換え・綴りの揺れは通ります。日本語のタグは対象外です(本文の判定 §19.1 を除く)。
- **画像そのものは判定しない。** できた絵を見て止める仕組みはありません。取り込み時に見るのはキャプションの文字列だけです。
- **ネガティブは確率的な抑制。** `general.safe_negative` / `dataset.r18_negative` / `scene.adult_safety_negative` は出にくくするだけで、出力を保証しません。
- **LLM への指示文は守られる保証がない。**
- **リストが機能ごとに別々。** 性的な語は `dataset.blocked_tags`・`scene.sexual_tags`・`similar.excluded_words`・`library.adult_tags` の4つ、未成年の語は `dataset.minor_tags`・`scene.minor_tags`・`dataset.r18_negative`・`scene.adult_safety_negative` の4つがあり、はじめの値は中身がそろっていません(§14.3 の比較)。片方に足しても、もう片方には効きません。どれも編集できるので、そろえるときはそれぞれに足します。
- **値を空にすると、その仕組みは働かなくなる。** 並びを空にすると何にも当たらず、ネガティブ・指示を空にすると足されません。止める確認はありません。
- **正規表現の書き方しだいで、当たりすぎる・当たらないことがある。** 保存の前後に `POST /api/content-guard/check`(開発管理者のページの「タグで確かめる」)で確かめてください。

### キャラ別データセット

- はじめの値の抜け(確認済み): `swimwear`、`see through`(ハイフンなし)、`nude_body` のような `_` つなぎ、`bare shoulders`、`undressing`
- **全年齢では、キャラシートの `appearance_tags` と `trigger_word` を検査しない**(§11)。容姿タグに基本のガードの語が入っていても、全年齢の生成は通ります(ネガティブの `general.safe_negative` で打ち消すだけです)。
- **未成年ガードは成人表現を誤検知することがある。** `young woman` は `young` で弾かれます。
- **成人フラグはタグで判定している。** 未成年として作ったキャラでも、シートから該当タグを消せば仕組みの上では成人フラグを付けられます。キャラの設定(年齢)そのものを確認する仕組みはないので、運用で守る必要があります。高校生として作ったキャラ(例: 九条 ゆら)には成人フラグを付けません。

### 物語・漫画

- **性的な場面の判定は `scene.sexual_tags` だけ。** はじめの値では、`bikini`・`underwear`・`lingerie`・`cleavage`・`see-through`・`nipple`(単数形)などは性的な場面とみなされません。その場面では、未成年を思わせるタグの除去(§14)も、ネガティブの追加(§15)も働きません。
- **`scene.minor_tags` はタグ全体の一致。** ほかの語と組み合わさったタグ(`student council`、`high school student`、`young woman`)は取り除かれません。はじめの値では、`jk`・`gym uniform`・`school swimsuit`・`kindergarten`・`aged down` は `dataset.minor_tags` にはありますが `scene.minor_tags` にはありません。
- **キャラシートのタグは整理されない。** コマ生成でキャラごとに渡す容姿・服装のタグ(`characterPrompts`)には、§14 の除去がかかりません。たとえば `outfit_tags` が `school uniform` のキャラが性的な場面に出ると、そのタグは送られます(§15 のネガティブで打ち消すだけです)。キャラの成人フラグも見ません。この点の対策は保留中です(2026-10-08)。
- **性的でない場面のコマには、露出を避けるネガティブが入らない**(§15)。漫画の下書きの画面からの生成は、画面の既定ネガティブ(§17)が入りますが、自動の漫画化・似た漫画・漫画v2の画面からの生成には入りません。
- **物語の挿絵(v1)と、手で編集したタグの保存には、整理がかからない**(§14.6)。

### 本棚・ギャラリー

- **自動判定は語の一致。** タグも本文も、リストにない言い回しは一般向けになります。外れていたら手動で指定します(§19.3)。
- **手動指定で成人向けを外せる。** 自動判定より手動指定が優先です。
- **ぼかしと絞り込みは表示の仕組み。** アクセスを制限するものではありません(§19.4)。

## 22. 変更するとき

### 値を変える

API(§3.2)か、開発管理者のページで変えます。コードの変更も再起動も要りません。

- **`patterns` の項目** — 1件ずつ正規表現で書きます。照合のしかた(`word` / `tag` / `substring`)は項目ごとに決まっていて、全体が `\b(…)\b` などで囲まれます。記号をそのままの文字として使うときは `\` を付けます(例: `c\+\+`)。
- **`scene.sexual_tags`** — タグの除去(§14)・コマのネガティブ(§15)・似た漫画のタグの選別(§18)・本棚とギャラリーの自動判定(§19)の**4か所**で使われます。足すと、これまで一般向けだった本や画像が成人向けの表示に変わることがあります。
- **リストからタグを外す・空にする** — その分だけ、止める・取り除く・打ち消す働きが弱まります(§21)。誤検知を直したいときは、外す代わりにパターンを狭める方法もあります(例: `young` を `young (girl|boy)` にする)。
- **変えたあと** — `POST /api/content-guard/check` で、気になるタグがどう扱われるかを確かめます。
- **戻す** — `DELETE /api/content-guard/rules/{key}`(1項目)、`POST /api/content-guard/reset`(全部)。

DB を消す・作り直すと、すべての項目がはじめの値で入り直します。編集した値を残したいときは、DB のバックアップ(開発管理者のページ)を取ってください。

### はじめの値を変える

[content_guard_defaults.json](../src/python/content_guard_defaults.json) を編集します。すでに DB にある項目には効きません(効かせるには、その項目を「はじめの値に戻す」)。

### 項目を足す

1. `content_guard_defaults.json` に項目(`key`・`group`・`label`・`description`・`kind`・`match`・`value`)を足す。次に読み込んだときに DB に入ります。
2. 使う側のコードで `content_guard.get(key)` / `find` / `search` などで読む。値をコードの定数に書かないでください。
3. この文書の §1・§3.1 の表に行を足す。

### 新しい生成機能を足すとき

§2 の表のどの行に当たるかを決め、この文書に行を足してください。何もかけない場合も「–」として書きます。

コードを変えたあとは、少なくとも次を確認してください(NovelAI へのリクエストは不要)。はじめの値での判定と、API での編集がすぐ効くことは、[tests/test_content_guard.py](../tests/test_content_guard.py) で確かめています(`uv run pytest tests/test_content_guard.py`)。以下は、はじめの値のときの結果です。

キャラ別データセット:

- 全年齢で、基本のガードのタグを含むバリエーションが 422 になる
- 成人フラグのないキャラで `rating: "r18"` が 403 になる
- 成人キャラでも、未成年を示すタグを含むバリエーション・キャプションが 422 になる
- 未成年を示すタグがあるキャラに成人フラグを付けると 422 になる
- プロファイルの追加タグが全年齢・R18 の両方で効く

物語・漫画:

- `sanitize_scene_tags("1girl, nude, school uniform, classroom, bed", adult=False)` が `1girl, nude, bed` を返す
- `sanitize_scene_tags("1girl, school uniform, classroom, bed", adult=True)` が `1girl, bed` を返す
- `build_panel_negative(None, color=True, sexual=True)` の末尾に `scene.adult_safety_negative` の値が付く
- `make_reference_candidates` のネガティブに `general.safe_negative` の値が入っている

本棚・ギャラリー:

- `_tags_adult("1girl, nude")` が真、`_tags_adult("1girl, bikini")` が偽
- `PUT /api/library/adult` で `adult: null` を送ると、自動判定に戻る
