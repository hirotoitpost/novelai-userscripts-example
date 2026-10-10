# コンテンツガード

このアプリにある「性的な内容」と「未成年に見える内容」を扱う仕組みを、すべてまとめた文書です。
どこに定義があり(Python のコードか、DB か、ブラウザか)、どの機能に効き、どの機能には効かないかを書きます。

- 第1部(§1〜§3)— アプリ全体の一覧
- 第2部(§4〜§13)— キャラ別データセットのガード(タグを**止める**仕組みはここだけ)
- 第3部(§14〜§20)— そのほかの機能の仕組み
- 第4部(§21〜§22)— 既知の制限と、変更するときの注意

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

| # | 仕組み | 種類 | 効く機能 | 定義 | 節 |
|---|---|---|---|---|---|
| 1 | 基本のガード(性的なタグ20件) | ブロック | キャラ別データセットの生成・取り込み(全年齢) | コード固定 | §7 |
| 2 | 未成年ガード(未成年を示すタグ23パターン) | ブロック | キャラ別データセットの生成・取り込み(R18)、成人フラグの保存 | コード固定 | §8、§9 |
| 3 | ガードプロファイル(利用者が足すタグ) | ブロック+ネガティブ | キャラ別データセットの生成・取り込み(全年齢・R18) | **DB** | §10 |
| 4 | 成人フラグ `is_adult` | R18 を許す条件 | キャラ別データセットの R18 | **DB**(保存時の検査はコード固定) | §9 |
| 5 | 全年齢のネガティブ `SAFE_NEGATIVE` | ネガティブ | キャラ別データセット(全年齢)、キャラシートの読み込み、参照画像の候補 | コード固定 | §7.3、§16 |
| 6 | R18 のタグ `R18_POSITIVE` / `R18_NEGATIVE` | プロンプト+ネガティブ | キャラ別データセット(R18) | コード固定 | §8.3 |
| 7 | 場面タグの整理 `sanitize_scene_tags` | タグ除去(+成人向けタグの追加) | 物語のタグ付け、漫画v2のコマ生成 | コード固定 | §14 |
| 8 | 成人向けタグ付けの指示文 | LLM への指示 | 物語のタグ付け(成人向け) | コード固定 | §14.4 |
| 9 | 性的な場面のネガティブ `_ADULT_SAFETY_NEGATIVE` | ネガティブ | 漫画v2のコマ生成 | コード固定 | §15 |
| 10 | 漫画の下書きの指示文(全年齢) | LLM への指示 | 漫画の下書き(大枠・台本)、似た漫画の人物づくり | コード固定 | §17 |
| 11 | 漫画の下書きの既定ネガティブ | ネガティブ | 漫画の下書きの画面からのコマ生成 | **画面の既定値**(利用者が書き換えられる) | §17 |
| 12 | 取り込んだ漫画のタグの選別 `_is_safe` | タグ除去 | 似た漫画を作る | コード固定 | §18 |
| 13 | 成人向けの自動判定 | 表示の区分 | 本棚、ギャラリー、作品の関連 | コード固定 | §19 |
| 14 | 成人向けの手動指定 `adult_marks` | 表示の区分 | 本棚、ギャラリー | **DB** | §19.3 |
| 15 | 描き文字素材の成人向け `stamp_sources.adult` | 自動選択からの除外 | 漫画v2の描き文字の自動選択 | **DB**(取り込み時の初期値はコードで判定) | §20 |

## 2. 効く範囲(機能ごと)

「–」は、その機能に何もかからないことを表します。

| 機能 | エンドポイント | ブロック | タグ除去 | 追加されるネガティブ |
|---|---|---|---|---|
| キャラ別データセットの生成 | `POST /api/lora-dataset/character/{id}/generate` | #1〜#4 | – | `SAFE_NEGATIVE`(全年齢)/ `R18_NEGATIVE`(R18)+プロファイル |
| キャラ別データセットへの取り込み | `POST /api/lora-dataset/character/{id}/import` | #1〜#4(キャプションの文字列のみ) | – | –(生成しない) |
| キャラシートの読み込み(画像生成ページ) | `GET /api/lora-dataset/character/{id}/sheet-prompt` | – | – | `SAFE_NEGATIVE`(返す文字列に入る。画面で書き換えられる) |
| 成人フラグの保存・別名保存 | `PUT /api/story/characters/{id}/sheet`、`POST …/duplicate` | #2 | – | – |
| 参照画像の候補(手動・自動) | `POST /api/manga-v2/characters/{id}/reference-candidates`、配役の自動選択 | – | #7 | `SAFE_NEGATIVE` |
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

## 3. どこに持っているか

### 3.1 Python のコードに固定(UI・API・開発管理者のページから変更できない)

| 定義 | 場所 |
|---|---|
| 基本のブロックリスト `_BLOCKED_PATTERNS` / `_BLOCKED_RE` | [src/python/character_sheet.py:58](../src/python/character_sheet.py#L58) |
| 全年齢で常に付けるネガティブ `SAFE_NEGATIVE` | [src/python/character_sheet.py:66](../src/python/character_sheet.py#L66) |
| 未成年ガード `_MINOR_PATTERNS` / `_MINOR_RE` | [src/python/character_sheet.py:94](../src/python/character_sheet.py#L94) |
| R18 で付けるタグ `R18_POSITIVE` / ネガティブ `R18_NEGATIVE` | [src/python/character_sheet.py:103-104](../src/python/character_sheet.py#L103-L104) |
| 判定関数 `minor_tags` / `character_minor_tags` | [src/python/character_sheet.py:107-116](../src/python/character_sheet.py#L107-L116) |
| UI 表示用の基本リスト `CORE_BLOCKED_TAGS` / `CORE_NEGATIVE` | [src/python/character_sheet.py:120-121](../src/python/character_sheet.py#L120-L121) |
| 判定関数 `blocked_tags` | [src/python/character_sheet.py:124](../src/python/character_sheet.py#L124) |
| プロンプト・ネガティブの組み立て `sheet_prompt` / `sheet_negative` | [src/python/character_sheet.py:147-167](../src/python/character_sheet.py#L147-L167) |
| 判定の入口 `_check_tags` | [src/python/routes/lora_dataset.py:185](../src/python/routes/lora_dataset.py#L185) |
| 成人フラグの保存時チェック `_checked_sheet` | [src/python/routes/story.py:2003](../src/python/routes/story.py#L2003) |
| 成人向けタグ付けの指示文 `_adult_tags_system_prompt` | [src/python/routes/story.py:758](../src/python/routes/story.py#L758) |
| 未成年を思わせるタグ `_MINOR_TAGS` | [src/python/routes/story.py:823](../src/python/routes/story.py#L823) |
| 性的な語 `_SEXUAL_HINT` | [src/python/routes/story.py:829](../src/python/routes/story.py#L829) |
| 性器が関わる語 `_GENITAL_HINT` / `_FEMALE_GENITAL_HINT` | [src/python/routes/story.py:839-848](../src/python/routes/story.py#L839-L848) |
| 場面タグの整理 `sanitize_scene_tags` | [src/python/routes/story.py:851](../src/python/routes/story.py#L851) |
| 性的な場面のネガティブ `_ADULT_SAFETY_NEGATIVE` | [src/python/manga_v2/prompt.py:20](../src/python/manga_v2/prompt.py#L20) |
| 性的な場面の判定 `is_sexual` | [src/python/manga_v2/prompt.py:23](../src/python/manga_v2/prompt.py#L23) |
| コマのプロンプト・ネガティブ `build_panel_prompt` / `build_panel_negative` | [src/python/manga_v2/prompt.py:29-52](../src/python/manga_v2/prompt.py#L29-L52) |
| 参照画像の候補 `make_reference_candidates` | [src/python/routes/manga_v2.py:530](../src/python/routes/manga_v2.py#L530) |
| 漫画の下書きの指示文 `_OUTLINE_SYSTEM` / `_EPISODE_SYSTEM` | [src/python/manga_draft.py:86](../src/python/manga_draft.py#L86)、[:166](../src/python/manga_draft.py#L166) |
| 取り込んだ漫画のタグの選別 `_BODY_WORDS` / `_is_safe` | [src/python/manga_similar.py:61](../src/python/manga_similar.py#L61)、[:156](../src/python/manga_similar.py#L156) |
| 似た漫画の人物づくりの指示文 `_NAMING_SYSTEM` | [src/python/manga_similar.py:284](../src/python/manga_similar.py#L284) |
| 成人向けを示すタグ `_EXTRA_ADULT_RE` | [src/python/routes/library.py:77](../src/python/routes/library.py#L77) |
| 本文で成人向けを判定する語 `_ADULT_TEXT_RE` / `_ADULT_TEXT_MIN_HITS` | [src/python/routes/library.py:80-85](../src/python/routes/library.py#L80-L85) |
| 成人向けの自動判定 `_tags_adult` / `_story_adult` | [src/python/routes/library.py:202-239](../src/python/routes/library.py#L202-L239) |
| pixiv の素材の成人向け判定 | [src/python/manga_v2/stamps.py:205](../src/python/manga_v2/stamps.py#L205) |

### 3.2 DB(`data/app.db`)

| テーブル・列 | 内容 | 変える方法 | 定義 |
|---|---|---|---|
| `guard_profiles` | 利用者が足すブロックタグとネガティブ。基本のガードへの**上乗せのみ** | キャラ別データセットの画面、開発管理者のページ、`/api/lora-dataset/guards` | [src/python/db.py:209](../src/python/db.py#L209) |
| `characters.is_adult` | 成人フラグ。R18 を許す条件 | キャラシートのチェックボックス、`PUT /api/story/characters/{id}/sheet` | [src/python/db.py:472](../src/python/db.py#L472) |
| `adult_marks` | 本・画像ごとの成人向けの**手動指定**(自動判定を上書きする) | 本棚・ギャラリーの「成人向けにする/外す」、`PUT /api/library/adult` | [src/python/db.py:365](../src/python/db.py#L365) |
| `stamp_sources.adult` | 描き文字の素材が成人向けか | 描き文字の素材の画面、`PUT /api/manga-v2/stamp-sources/{key}` | [src/python/db.py:538](../src/python/db.py#L538) |
| `app_settings` | アプリの既定値(開発管理者のページ)。**ガードの項目は入れていない**(下記) | 開発管理者のページ | [src/python/db.py:221](../src/python/db.py#L221) |

`app_settings` で変えられるのは、品質用のネガティブ2つだけです(ガードではありません)。

| キー | 既定値 | 使う場所 |
|---|---|---|
| `sheet.default_negative` | `lowres, worst quality, low quality, blurry, bad anatomy, bad hands, extra fingers, missing fingers, text, watermark` | キャラ別データセットのネガティブの先頭、リバースプロンプトの結果 |
| `manga.quality_negative` | `lowres, artistic error, scan artifacts, worst quality, bad quality, jpeg artifacts, very displeasing, watermark, signature` | 漫画v2のコマ(ネガティブ未指定のとき)、参照画像の候補 |

これらを書き換えても、`SAFE_NEGATIVE` / `R18_NEGATIVE` / `_ADULT_SAFETY_NEGATIVE` は別に連結されるので外れません。基本のガード・未成年ガード・全年齢のネガティブを `app_settings` に載せないのは意図したものです([src/python/app_settings.py](../src/python/app_settings.py) の冒頭の説明)。

開発管理者のページ(`/admin` の「既定値・プリセット・ガード」)では、基本のガードは**表示だけ**で、編集できるのはガードプロファイルです。

### 3.3 ブラウザ(端末ごと。`localStorage`)

表示のしかただけを決めます。サーバーの判定には関わりません。

| キー | 既定 | 内容 |
|---|---|---|
| `nai_library_blur_adult` | `true`(ぼかす) | 本棚・ギャラリーで成人向けの表紙・画像をぼかす(共通) |
| `nai_gallery_rating` | `all` | ギャラリーの絞り込み(すべて / 一般向けのみ / 成人向けのみ) |
| `nai_bookshelf_rating` | `all` | 本棚の絞り込み(同上) |
| `nai_manga_v2_include_adult` | `false` | 描き文字の自動選択に成人向けの素材も使う |

漫画の下書きの画面にある既定のネガティブ(§17)も画面側の値で、[src/javascript/pages/MangaDraft.tsx:88](../src/javascript/pages/MangaDraft.tsx#L88) にあります。

行番号は執筆時点(2026-10-11)のものです。ずれていたら定数名・関数名で検索してください。

---

# 第2部 キャラ別データセットのガード

キャラ別データセット(`/character-dataset`)で、生成・取り込みしてよいタグを判定する仕組みです。

## 4. 全体像

| 層 | 内容 | 変更できるか |
|---|---|---|
| 基本のガード | 性的なタグのブロックリストと、常に付けるネガティブ | **コード固定**(UI・API からは変更できない) |
| 未成年ガード | 未成年を示すタグのリスト。R18 のときに使う | **コード固定** |
| ガードプロファイル | 利用者が追加するブロックタグとネガティブ | UI / API で追加・削除・保存できる。基本のガードへの**上乗せのみ** |
| 成人フラグ | キャラごとの `is_adult`。R18 を許可する条件 | キャラシートで切り替えられる。未成年を示すタグがあると付けられない |

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

- 常に**全年齢**の組み立てです(`sheet_prompt(character)` / `sheet_negative(character)`)。成人フラグのあるキャラでも `R18_POSITIVE` / `R18_NEGATIVE` は入らず、ネガティブに `SAFE_NEGATIVE` が入ります。
- ガードプロファイルの追加分は入りません。
- ブロックの判定はしません。返した文字列は画面で自由に書き換えられ、その後の生成は通常の画像生成(ガードなし)です。

## 7. 基本のガード(全年齢)

### 7.1 ブロックするタグ

```
nsfw, nude, naked, nipples?, topless, bottomless, sex,
underwear, lingerie, panties, bra, brassiere, swimsuit, bikini,
see-through, cleavage, pussy, penis, cum, bondage
```

20件です。`nipples?` は正規表現で、`nipple` と `nipples` の両方に当たります。

### 7.2 照合のしかた

- 正規表現 `\b(パターン1|パターン2|…)\b` で照合します。大文字と小文字は区別しません。
- `\b` は単語の境目です。ほかの単語の**一部**には当たりませんが、複数語のタグの中の**単語**には当たります。
- 照合はタグを分割せず、文字列全体に対して行います。

実際の判定結果:

| 入力 | 判定 | 理由 |
|---|---|---|
| `one-piece swimsuit`、`school swimsuit` | ブロック(`swimsuit`) | 複数語の中の単語に当たる |
| `sports bra` | ブロック(`bra`) | 同上 |
| `Nude`、`{{nude}}` | ブロック(`nude`) | 大文字小文字を区別しない。強調記号は境目扱い |
| `nipple` | ブロック | `nipples?` |
| `sexy` | 通過 | `sex` は単語の一部 |
| `cumulonimbus` | 通過 | `cum` は単語の一部 |
| `swimwear` | **通過** | リストにない(既知の抜け、§21) |
| `see through`(ハイフンなし) | **通過** | リストは `see-through` のみ |
| `nude_body` | **通過** | `_` は単語の文字として扱われ、境目にならない |
| `bare shoulders`、`undressing` | 通過 | リストにない |

### 7.3 常に付けるネガティブ

全年齢の生成では、ネガティブを次の順で連結します(`join_tags` で重複を除き、先に出たものを残す)。

1. 品質用の基本のネガティブ(`app_settings` の `sheet.default_negative`。既定値は `DEFAULT_NEGATIVE` と同じ)
2. キャラシートの `negative_tags`
3. `SAFE_NEGATIVE` = `nsfw, nude, underwear, swimsuit, cleavage`
4. ガードプロファイルの `negative_tags`

1 は開発管理者のページで書き換えられますが、3 は別に連結するので、1 を空にしても外れません。

## 8. 未成年ガード(R18)

### 8.1 未成年を示すタグ

```
jk, joshi ?kousei, school ?uniform, serafuku, gym uniform, school ?swimsuit,
randoseru, kindergarten, (high|middle|elementary) school, school ?(girl|boy)s?,
students?, loli, shota, child(ren)?, kids?, teen(age|ager)?, underage,
minor, young, aged down, toddler, little girl, little boy
```

23パターンです。照合は基本のガードと同じで、`\b…\b` で大文字小文字を区別しません。` ?` は「空白があってもなくてもよい」という意味で、`school uniform` と `schooluniform` の両方に当たります。

実際の判定結果:

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

- **プロンプト** — `R18_POSITIVE` = `adult, mature female` を、トリガーワードと容姿タグの直後に入れます。
- **ネガティブ** — `SAFE_NEGATIVE` の代わりに `R18_NEGATIVE` = `child, loli, shota, young, teenage, school uniform, student, petite, flat chest` を入れます。連結の順は §7.3 と同じです。

### 8.4 既定のバリエーションとの関係

服装の既定リスト(`OUTFITS`)には `school uniform` 系と `gym uniform` が、場所の既定リスト(`LOCATIONS`)には `classroom`・`school hallway`・`school rooftop` が入っています。R18 で `school uniform` 系・`gym uniform` を選ぶと 422 になります(場所の3つは未成年ガードのパターンに当たらないので通ります)。R18 では、既定リストから外すか、「追加…」で入力したものを使ってください。

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
| `GET /api/lora-dataset/guards/core` | 基本のガード(`blocked_tags` と `negative_tags`)。読み取り専用 |
| `GET /api/lora-dataset/guards` | プロファイル一覧(名前順) |
| `POST /api/lora-dataset/guards` | 保存(同名は上書き)。本文: `{name, blocked_tags: string[], negative_tags}` |
| `DELETE /api/lora-dataset/guards/{id}` | 削除 |

`/guards/core` が返すのは基本のガード(`CORE_BLOCKED_TAGS` と `CORE_NEGATIVE` = `SAFE_NEGATIVE`)だけです。未成年ガードのリスト(`_MINOR_PATTERNS`)を返す API はありません。

保存時、サーバーは `blocked_tags` を次のように整えます。

- 前後の空白を除き、空のものを捨てる
- 大文字小文字を区別せずに重複を除く
- 基本のガード(`CORE_BLOCKED_TAGS`)と同じものを捨てる

`CORE_BLOCKED_TAGS` は `_BLOCKED_PATTERNS` から `?` を除いた表示用の一覧です(`nipples?` は `nipples` になる)。そのため `nipples` は捨てられますが、`nipple` はプロファイルに残ります(判定結果は同じなので害はありません)。

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
- 基本のガードのタグは、鍵つきの変更できないチップとして表示します。

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

**未成年を思わせるタグ `_MINOR_TAGS`**(タグ**全体**が一致したときだけ当たる。大文字小文字は区別しない):

```
child, children, kid, kids, loli, lolita, shota, toddler, baby,
young girl, little girl, young boy, little boy,
school uniform, serafuku, student, schoolgirl, schoolboy, classroom,
elementary school, middle school, high school, randoseru,
children with cameras, petite child, underage, teen, teenager
```

**性的な語 `_SEXUAL_HINT`**(`\b…\b` の単語照合。大文字小文字は区別しない):

```
nsfw, nude, naked, sex, penis, pussy, nipples, fellatio, cum, vaginal, anal,
masturbation, fingering, intercourse, topless, bottomless, erection, ejaculation, orgasm
```

**性器が関わる語 `_GENITAL_HINT`**:

```
pussy, vagina, vaginal, clitoris, labia, anus, anal, penis, testicles, sex, intercourse,
penetration, creampie, cum in pussy, cumdrip, fingering, cunnilingus, spread legs,
pubic hair, pussy juice
```

`_FEMALE_GENITAL_HINT` は、上から `anus`・`anal`・`penis`・`testicles` を除いたものです。

第2部のリスト(`_BLOCKED_PATTERNS`・`_MINOR_PATTERNS`)とは**別の定義**で、中身も照合のしかたも違います。

| | 第2部(データセット) | ここ(場面タグ) |
|---|---|---|
| 性的な語 | `_BLOCKED_PATTERNS` 20件。`underwear`・`lingerie`・`panties`・`bra`・`swimsuit`・`bikini`・`see-through`・`cleavage`・`bondage` を含む | `_SEXUAL_HINT` 19件。左の9件は**含まない**。`fellatio`・`masturbation` などの行為の語を含む |
| 未成年の語 | `_MINOR_PATTERNS`。文字列の中の単語に当たる | `_MINOR_TAGS`。タグ全体が一致したときだけ |
| 当たったとき | 拒否(422) | 黙って取り除く |

### 14.4 実際の結果

| 入力 | `adult` | 結果 | 説明 |
|---|---|---|---|
| `1girl, school uniform, classroom, smile` | 偽 | 変化なし | 性的な語がないので何もしない |
| `1girl, nude, school uniform, classroom, bed` | 偽 | `1girl, nude, bed` | 性的な語があるので、未成年を思わせるタグを除く |
| `1girl, school uniform, classroom, bed` | 真 | `1girl, bed` | 成人向けでは、性的な語がなくても除く |
| `1girl, sex, pussy, bed` | 真 | `nsfw, explicit, uncensored, 1girl, sex, pussy, bed` | 成人向けのタグの補強 |
| `nsfw, 1girl, penis, fellatio` | 真 | `nsfw, explicit, uncensored, 1girl, penis, fellatio` | `nsfw` の直後に足す |
| `1girl, kiss, bed` | 真 | 変化なし | 性的な語がないので `nsfw` は足さない |
| `1girl, completely nude, student council, bed` | 偽 | **変化なし** | `student council` はタグ全体が `student` ではないので残る(§21) |
| `1girl, nipple, school uniform` | 偽 | **変化なし** | `_SEXUAL_HINT` は `nipples` のみで、単数形に当たらない(§21) |
| `1girl, bikini, underwear, student` | 偽 | **変化なし** | `bikini`・`underwear` は `_SEXUAL_HINT` にない(§21) |

### 14.5 成人向けのタグ付けの指示文

`adult` のタグ付けは、ローカルの LLM ではなく NovelAI の文章モデル(GLM-4.6)で行います。その指示文 `_adult_tags_system_prompt` に、次の内容を書いています。

- 登場人物は全員成人である(`All characters are adults.`)
- 未成年を思わせるタグを使わない(`never use tags implying minors (child, loli, shota, school uniform, student, classroom)`)
- 本文にない場所・服装を足さない

指示文は守られる保証がないので、返ってきたタグには必ず `sanitize_scene_tags(adult=True)` をかけます。

一般向けのタグ付けの指示文 `_import_tags_system_prompt` には、性的な内容についての指示はありません。あるのは「本文に書かれていない服装・場所(制服、教室など)を足さない」だけです。

### 14.6 かからないところ

- **シーンのタグを手で編集したとき**(`PATCH /api/story/scenes/{id}`)— 保存時には整理しません。漫画v2のコマ生成のときに `build_panel_prompt` でかかります。
- **物語の挿絵(v1、`/api/story/{id}/illustrate`)** — 生成時には整理しません。DB のタグをそのまま使います(タグ付けで付いたタグなら、保存前に整理済みです)。
- **キャラシートのタグ** — コマ生成でキャラごとに渡す容姿・服装のタグ(`characterPrompts`)は整理の対象外です(§21)。

## 15. 性的な場面のネガティブ(漫画v2のコマ)

`_ADULT_SAFETY_NEGATIVE`([manga_v2/prompt.py](../src/python/manga_v2/prompt.py)):

```
child, loli, shota, young, petite, flat chest, school uniform, student
```

- コマごとに、そのシーンのタグ(`draft_prompt_tags`)が `is_sexual` に当たるかを見ます。`is_sexual` は §14.3 の `_SEXUAL_HINT` での照合です。
- 当たったコマだけ、ネガティブの末尾にこの語を足します(`build_panel_negative(…, sexual=True)`)。
- 利用者がネガティブを指定していても足します。品質のネガティブ(`manga.quality_negative`)を書き換えても外れません。
- `R18_NEGATIVE`(§8.3)と似ていますが別の定義で、こちらには `teenage` がありません。

コマのネガティブは、次の順で連結されます。

1. 品質のネガティブ(利用者の指定。無ければ `manga.quality_negative`)
2. 文字・コマ割りを消す語(`_NO_TEXT_NEGATIVE`。ガードではない)
3. モノクロのとき `sepia, colored, watercolor`(ガードではない)
4. 性的な場面のとき `_ADULT_SAFETY_NEGATIVE`
5. 登場キャラの `negative_tags`(1人のとき。2人以上ならキャラごとの欄に分ける)

**全年齢のネガティブ(`SAFE_NEGATIVE`)は、コマ生成には入りません。** 性的でない場面のコマには、未成年・露出のどちらについてもネガティブは足されません。

## 16. 参照画像の候補

`make_reference_candidates`([routes/manga_v2.py](../src/python/routes/manga_v2.py))。キャラシートから参照画像の候補(立ち絵)を作ります。次の2か所から呼ばれます。

- 手動: `POST /api/manga-v2/characters/{id}/reference-candidates`(キャラシート・漫画の下書きの「候補を作る」)
- 自動: 配役(`POST /api/story/{id}/cast`、`/auto-manga`)で参照画像を自動で1枚決めるとき

参照画像は全年齢の立ち絵にするため、ネガティブを次の順で連結します。

1. `build_panel_negative(None, color=…)`(品質+文字を消す語)
2. `SAFE_NEGATIVE` = `nsfw, nude, underwear, swimsuit, cleavage`
3. キャラシートの `negative_tags`

成人フラグのあるキャラでも、参照画像の候補は常に全年齢です。プロンプトのうち構図の部分(`reference.candidate_tags`)は開発管理者のページで変えられますが、`SAFE_NEGATIVE` は外れません。

ブロックの判定はしません。キャラシートの容姿タグに露出のタグが入っていれば、そのまま送られます(ネガティブで打ち消すだけです)。

## 17. 漫画の下書き

LLM への指示文に、全年齢向けであることを書いています([manga_draft.py](../src/python/manga_draft.py))。

| 指示文 | 書いてあること |
|---|---|
| `_OUTLINE_SYSTEM`(大枠シナリオ) | 「全年齢向けの短編漫画」「露出や性的な描写はしない」 |
| `_EPISODE_SYSTEM`(1話分の台本) | 「全年齢向け。露出・性的な描写はしない」 |

指示文なので、守られる保証はありません。出てきた台本のタグは、コマ生成のときに §14(`adult` は偽)と §15 を通ります。

漫画の下書きの**画面**は、コマ生成のネガティブの既定値に露出を避ける語を入れています([MangaDraft.tsx:88](../src/javascript/pages/MangaDraft.tsx#L88))。

```
lowres, artistic error, scan artifacts, worst quality, bad quality, jpeg artifacts, very displeasing,
watermark, signature, nsfw, nude, cleavage, underwear, sexually suggestive
```

これは画面の入力欄の初期値で、利用者が書き換えられます。サーバー側では強制していません。

## 18. 似た漫画を作る(取り込んだ漫画から)

`manga_similar.py`。取り込んだページの絵を判定モデル(WD Tagger)で読み、舞台と人物の見た目のタグを取り出して、新しい話を作ります。全年齢向けにするため、読み取ったタグを選別します。

`_is_safe(tag)` は、次のどちらにも当たらないタグだけを通します。

- タグの中の単語が `_BODY_WORDS`(体つき・露出)のどれかと一致する
- `is_sexual`(§14.3 の `_SEXUAL_HINT`)に当たる

`_BODY_WORDS`:

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
- 人物に名前と人物像を付ける指示文 `_NAMING_SYSTEM` に「全年齢向けの日常の話に出せる人物にする」「実在の人物や、既存の作品のキャラクターの名前は使わない」と書いています。
- 話づくりは §17 の指示文、コマ生成は §14・§15 を通ります。コマ生成の設定は既定値で、§17 の画面の既定ネガティブは**入りません**。

## 19. 成人向けの区分(本棚・ギャラリー)

生成は止めず、できたものを「成人向け」と「一般向け」に分けます。絞り込みと、ぼかしに使います([routes/library.py](../src/python/routes/library.py))。

### 19.1 自動判定

**タグでの判定 `_tags_adult(tags)`** — 次のどちらかに当たれば成人向け:

- `is_sexual`(§14.3 の `_SEXUAL_HINT`)
- `_EXTRA_ADULT_RE` = `explicit`、`uncensored`、`hentai`、`r18` / `r-18`(`\b…\b`、大文字小文字を区別しない)

**物語(本)の判定 `_story_adult`** — 次のどれかで成人向け:

1. どれか1シーンのタグ(`draft_prompt_tags`)が `_tags_adult` に当たる
2. 本文(シーンの本文。無ければ取り込んだままの本文)に `_ADULT_TEXT_RE` の語が合計 **3回以上**(`_ADULT_TEXT_MIN_HITS`)出てくる
3. 同じシリーズのどれか1巻が 1 か 2 に当たる(シリーズ全体を成人向けとみなす)

`_ADULT_TEXT_RE`(日本語の本文用):

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

- pixiv から取り込むとき: 年齢制限(`xRestrict` が 0 以外)か、タグに `R-18`・`R18`・`R-18G` があれば成人向けにします([manga_v2/stamps.py:205](../src/python/manga_v2/stamps.py#L205))。
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
- **ネガティブは確率的な抑制。** `SAFE_NEGATIVE` / `R18_NEGATIVE` / `_ADULT_SAFETY_NEGATIVE` は出にくくするだけで、出力を保証しません。
- **LLM への指示文は守られる保証がない。**
- **リストが機能ごとに別々。** 性的な語は `_BLOCKED_PATTERNS`・`_SEXUAL_HINT`・`_BODY_WORDS`・`_EXTRA_ADULT_RE` の4つ、未成年の語は `_MINOR_PATTERNS`・`_MINOR_TAGS`・`R18_NEGATIVE`・`_ADULT_SAFETY_NEGATIVE` の4つがあり、中身がそろっていません(§14.3 の比較)。片方に足しても、もう片方には効きません。

### キャラ別データセット

- 確認済みの抜け: `swimwear`、`see through`(ハイフンなし)、`nude_body` のような `_` つなぎ、`bare shoulders`、`undressing`
- **全年齢では、キャラシートの `appearance_tags` と `trigger_word` を検査しない**(§11)。容姿タグに基本のガードの語が入っていても、全年齢の生成は通ります(ネガティブの `SAFE_NEGATIVE` で打ち消すだけです)。
- **未成年ガードは成人表現を誤検知することがある。** `young woman` は `young` で弾かれます。
- **成人フラグはタグで判定している。** 未成年として作ったキャラでも、シートから該当タグを消せば仕組みの上では成人フラグを付けられます。キャラの設定(年齢)そのものを確認する仕組みはないので、運用で守る必要があります。高校生として作ったキャラ(例: 九条 ゆら)には成人フラグを付けません。

### 物語・漫画

- **性的な場面の判定は `_SEXUAL_HINT` だけ。** `bikini`・`underwear`・`lingerie`・`cleavage`・`see-through`・`nipple`(単数形)などは性的な場面とみなされません。その場面では、未成年を思わせるタグの除去(§14)も、ネガティブの追加(§15)も働きません。
- **`_MINOR_TAGS` はタグ全体の一致。** ほかの語と組み合わさったタグ(`student council`、`high school student`、`young woman`)は取り除かれません。`jk`・`gym uniform`・`school swimsuit`・`kindergarten`・`aged down` は `_MINOR_PATTERNS` にはありますが `_MINOR_TAGS` にはありません。
- **キャラシートのタグは整理されない。** コマ生成でキャラごとに渡す容姿・服装のタグ(`characterPrompts`)には、§14 の除去がかかりません。たとえば `outfit_tags` が `school uniform` のキャラが性的な場面に出ると、そのタグは送られます(§15 のネガティブで打ち消すだけです)。キャラの成人フラグも見ません。この点の対策は保留中です(2026-10-08)。
- **性的でない場面のコマには、露出を避けるネガティブが入らない**(§15)。漫画の下書きの画面からの生成は、画面の既定ネガティブ(§17)が入りますが、自動の漫画化・似た漫画・漫画v2の画面からの生成には入りません。
- **物語の挿絵(v1)と、手で編集したタグの保存には、整理がかからない**(§14.6)。

### 本棚・ギャラリー

- **自動判定は語の一致。** タグも本文も、リストにない言い回しは一般向けになります。外れていたら手動で指定します(§19.3)。
- **手動指定で成人向けを外せる。** 自動判定より手動指定が優先です。
- **ぼかしと絞り込みは表示の仕組み。** アクセスを制限するものではありません(§19.4)。

## 22. 変更するとき

- **基本のガード・未成年ガードにタグを足す** — `_BLOCKED_PATTERNS` / `_MINOR_PATTERNS` に正規表現で追加します。リスト全体が `\b(…)\b` で囲まれることを前提に書いてください。
- **場面タグのリストに足す** — `_SEXUAL_HINT` は `\b(…)\b`、`_MINOR_TAGS` は `^(…)$`(タグ全体の一致)です。`_SEXUAL_HINT` は、タグの除去(§14)・コマのネガティブ(§15)・似た漫画のタグの選別(§18)・本棚とギャラリーの自動判定(§19)の**4か所**で使われます。足すと、これまで一般向けだった本や画像が成人向けの表示に変わることがあります。
- **リストからタグを外す** — 外すと全年齢の保証や未成年対策が弱まります。外すのではなく、誤検知の原因になっているパターンを狭める方向で直してください(例: `young` を `young girl` などに限定する)。
- **ガードプロファイル** — 上乗せだけの設計です。基本のガードを無効にする項目は足さないでください。
- **`app_settings`(開発管理者のページ)** — ガードの項目は載せないでください。載せると、画面から外せるようになります。
- **新しい生成機能を足すとき** — §2 の表のどの行に当たるかを決め、この文書に行を足してください。何もかけない場合も「–」として書きます。

変更後は、少なくとも次を確認してください(NovelAI へのリクエストは不要。`fastapi.testclient.TestClient` や、関数の直接の呼び出しで確認できます)。ガードを対象にした自動テストは、現在 `tests/` にありません。

キャラ別データセット:

- 全年齢で、基本のガードのタグを含むバリエーションが 422 になる
- 成人フラグのないキャラで `rating: "r18"` が 403 になる
- 成人キャラでも、未成年を示すタグを含むバリエーション・キャプションが 422 になる
- 未成年を示すタグがあるキャラに成人フラグを付けると 422 になる
- プロファイルの追加タグが全年齢・R18 の両方で効く

物語・漫画:

- `sanitize_scene_tags("1girl, nude, school uniform, classroom, bed", adult=False)` が `1girl, nude, bed` を返す
- `sanitize_scene_tags("1girl, school uniform, classroom, bed", adult=True)` が `1girl, bed` を返す
- `build_panel_negative(None, color=True, sexual=True)` の末尾に `_ADULT_SAFETY_NEGATIVE` が付く
- `make_reference_candidates` のネガティブに `SAFE_NEGATIVE` が入っている

本棚・ギャラリー:

- `_tags_adult("1girl, nude")` が真、`_tags_adult("1girl, bikini")` が偽
- `PUT /api/library/adult` で `adult: null` を送ると、自動判定に戻る
