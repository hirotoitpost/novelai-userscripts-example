# キャラ別データセットのコンテンツガード

キャラ別データセット(`/character-dataset`)で、生成・取り込みしてよいタグを判定する仕組みの説明です。
判定はすべてサーバー側で行います。画面での制限は補助で、API を直接呼んでも同じ判定がかかります。

## 1. 全体像

| 層 | 内容 | 変更できるか |
|---|---|---|
| 基本のガード | 性的なタグのブロックリストと、常に付けるネガティブ | **コード固定**(UI・API からは変更できない) |
| 未成年ガード | 未成年を示すタグのリスト。R18 のときに使う | **コード固定** |
| ガードプロファイル | 利用者が追加するブロックタグとネガティブ | UI / API で追加・削除・保存できる。基本のガードへの**上乗せのみ** |
| 成人フラグ | キャラごとの `is_adult`。R18 を許可する条件 | キャラシートで切り替えられる。未成年を示すタグがあると付けられない |

レーティングは次の2つです。

- **全年齢(`general`、既定)** — 基本のガードとプロファイルの追加分で判定します。
- **R18(`r18`)** — 成人フラグのあるキャラのみ。基本のガードは外れ、代わりに未成年ガードで判定します。プロファイルの追加分は R18 でも効きます。

## 2. 対象範囲

ガードがかかるのは次の2つのエンドポイントだけです。

| エンドポイント | 用途 |
|---|---|
| `POST /api/lora-dataset/character/{character_id}/generate` | キャラシートからのデータセット生成 |
| `POST /api/lora-dataset/character/{character_id}/import` | 手持ち画像の取り込み |

次の機能には**かかりません**。

- 通常の画像生成(`/generate`)
- 既存の LoRA データセット生成(`/api/lora-dataset/generate`、`/preview`)
- 物語の挿絵(`/api/story/{id}/illustrate`)
- 漫画v2 のコマ生成。こちらは性的な場面に対して独自の未成年対策ネガティブを持ちます(`manga_v2/prompt.py` の `_ADULT_SAFETY_NEGATIVE`)。

## 3. 定義の場所

| 定義 | 場所 |
|---|---|
| 基本のブロックリスト `_BLOCKED_PATTERNS` | [src/python/character_sheet.py:51](../src/python/character_sheet.py#L51) |
| 全年齢で常に付けるネガティブ `SAFE_NEGATIVE` | [src/python/character_sheet.py:59](../src/python/character_sheet.py#L59) |
| 品質用の既定ネガティブ `DEFAULT_NEGATIVE` | [src/python/character_sheet.py:61](../src/python/character_sheet.py#L61) |
| 未成年ガード `_MINOR_PATTERNS` | [src/python/character_sheet.py:87](../src/python/character_sheet.py#L87) |
| R18 で付けるタグ `R18_POSITIVE` / ネガティブ `R18_NEGATIVE` | [src/python/character_sheet.py:96-97](../src/python/character_sheet.py#L96-L97) |
| 判定関数 `minor_tags` / `character_minor_tags` / `blocked_tags` | [src/python/character_sheet.py:100-128](../src/python/character_sheet.py#L100-L128) |
| UI 表示用の基本リスト `CORE_BLOCKED_TAGS` | [src/python/character_sheet.py:113](../src/python/character_sheet.py#L113) |
| 判定の入口 `_check_tags` | [src/python/routes/lora_dataset.py:185](../src/python/routes/lora_dataset.py#L185) |
| 成人フラグの保存時チェック `put_character_sheet` | [src/python/routes/story.py:1683](../src/python/routes/story.py#L1683) |
| ガードプロファイルのテーブル `guard_profiles` | [src/python/db.py:209](../src/python/db.py#L209) |
| 成人フラグの列 `characters.is_adult` | [src/python/db.py:437](../src/python/db.py#L437) |

行番号は執筆時点のものです。ずれていたら定数名・関数名で検索してください。

## 4. 基本のガード(全年齢)

### 4.1 ブロックするタグ

```
nsfw, nude, naked, nipples?, topless, bottomless, sex,
underwear, lingerie, panties, bra, brassiere, swimsuit, bikini,
see-through, cleavage, pussy, penis, cum, bondage
```

`nipples?` は正規表現で、`nipple` と `nipples` の両方に当たります。

### 4.2 照合のしかた

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
| `swimwear` | **通過** | リストにない(既知の抜け、§9) |
| `see through`(ハイフンなし) | **通過** | リストは `see-through` のみ |
| `nude_body` | **通過** | `_` は単語の文字として扱われ、境目にならない |
| `bare shoulders`、`undressing` | 通過 | リストにない |

### 4.3 常に付けるネガティブ

全年齢の生成では、ネガティブを次の順で連結します(`join_tags` で重複を除き、先に出たものを残す)。

1. `DEFAULT_NEGATIVE`(品質用)
2. キャラシートの `negative_tags`
3. `SAFE_NEGATIVE` = `nsfw, nude, underwear, swimsuit, cleavage`
4. ガードプロファイルの `negative_tags`

## 5. 未成年ガード(R18)

### 5.1 未成年を示すタグ

```
jk, joshi ?kousei, school ?uniform, serafuku, gym uniform, school ?swimsuit,
randoseru, kindergarten, (high|middle|elementary) school, school ?(girl|boy)s?,
students?, loli, shota, child(ren)?, kids?, teen(age|ager)?, underage,
minor, young, aged down, toddler, little girl, little boy
```

照合は基本のガードと同じで、`\b…\b` で大文字小文字を区別しません。` ?` は「空白があってもなくてもよい」という意味で、`school uniform` と `schooluniform` の両方に当たります。

実際の判定結果:

| 入力 | 判定 |
|---|---|
| `JK`、`school uniform`、`schoolgirl`、`high school` | ブロック |
| `student council` | ブロック(`student`) |
| `young woman` | **ブロック**(`young`)。成人を表す意図でも当たる |
| `teen`、`teenager`、`children`、`kid` | ブロック |
| `lolita fashion` | 通過(`loli` は単語の一部) |
| `adult`、`mature female`、`petite` | 通過 |

### 5.2 検査する場所

R18 では、次の2つを合わせて検査します。

- **キャラシート**(`character_minor_tags`)— `name`、`trigger_word`、`appearance_tags`、`outfit_tags`、`style_tags` をつなげた文字列
- **リクエストの文字列**(`text`)— 生成時は「選んだバリエーション4軸すべて+キャラの `outfit_tags`+`style_tags`」、取り込み時はキャプション

キャラシートの `negative_tags` は検査しません。ネガティブに `child` などを入れるのは正しい使い方だからです。

### 5.3 R18 で付けるタグ

- **プロンプト** — `R18_POSITIVE` = `adult, mature female` を、トリガーワードと容姿タグの直後に入れます。
- **ネガティブ** — `SAFE_NEGATIVE` の代わりに `R18_NEGATIVE` = `child, loli, shota, young, teenage, school uniform, student, petite, flat chest` を入れます。連結の順は §4.3 と同じです。

### 5.4 既定のバリエーションとの関係

服装の既定リスト(`OUTFITS`)には `school uniform` 系と `gym uniform` が入っています。R18 でこれらを選ぶと 422 になります。R18 では、既定リストから外すか、「追加…」で入力したものを使ってください。

## 6. 成人フラグ

- 列: `characters.is_adult`(`INTEGER NOT NULL DEFAULT 0`)。API では真偽値です。
- 変更: `PUT /api/story/characters/{id}/sheet` に `{"is_adult": true}` を送ります。UI ではキャラシートのチェックボックスです。

保存時のチェック([routes/story.py](../src/python/routes/story.py) の `put_character_sheet`):

1. 送られた項目を今のキャラシートに重ね、保存後の状態を作ります。
2. 保存後に `is_adult` が真になるなら、`character_minor_tags` で検査します。
3. 当たれば `422「未成年を示すタグがあるため成人キャラにできません: …」` を返し、**何も保存しません**。

そのため、次のどちらも拒否されます。

- 未成年を示すタグがあるキャラに成人フラグを付ける
- 成人フラグのあるキャラに、あとから未成年を示すタグ(例: `outfit_tags` に `school uniform`)を足す

成人フラグを外す(`false`)のはいつでもできます。

## 7. ガードプロファイル

### 7.1 データ

テーブル `guard_profiles`:

| 列 | 型 | 内容 |
|---|---|---|
| `id` | INTEGER | 主キー |
| `name` | TEXT UNIQUE | プロファイル名。同名で保存すると上書き |
| `blocked_tags` | TEXT | 追加のブロックタグ(JSON 配列) |
| `negative_tags` | TEXT | 追加のネガティブ(カンマ区切り) |
| `created_at` | TEXT | 作成日時(上書きしても変わらない) |

### 7.2 API

| メソッド・パス | 内容 |
|---|---|
| `GET /api/lora-dataset/guards/core` | 基本のガード(`blocked_tags` と `negative_tags`)。読み取り専用 |
| `GET /api/lora-dataset/guards` | プロファイル一覧(名前順) |
| `POST /api/lora-dataset/guards` | 保存(同名は上書き)。本文: `{name, blocked_tags: string[], negative_tags}` |
| `DELETE /api/lora-dataset/guards/{id}` | 削除 |

保存時、サーバーは `blocked_tags` を次のように整えます。

- 前後の空白を除き、空のものを捨てる
- 大文字小文字を区別せずに重複を除く
- 基本のガード(`CORE_BLOCKED_TAGS`)と同じものを捨てる

`CORE_BLOCKED_TAGS` は `_BLOCKED_PATTERNS` から `?` を除いた表示用の一覧です(`nipples?` は `nipples` になる)。そのため `nipples` は捨てられますが、`nipple` はプロファイルに残ります(判定結果は同じなので害はありません)。

### 7.3 追加タグの照合

- タグは正規表現ではなく、**文字列そのもの**として扱います(`re.escape`)。`c++` や `a.b` のように記号を含んでいても誤作動しません。
- 前後が「英数字・`_`・`-`」でないときだけ当たります(`(?<![\w-])…(?![\w-])`)。大文字小文字は区別しません。

`maid` を追加した場合:

| 入力 | 判定 |
|---|---|
| `maid outfit`、`MAID` | ブロック |
| `maid-style` | 通過(後ろが `-`) |
| `mermaid` | 通過(前が英字) |

基本のガードの `\b` と違い、ハイフンも単語の一部とみなします。

### 7.4 使い方

生成・取り込みのリクエストに `guard_profile_id` を付けます(UI ではプルダウンで選択)。未指定なら基本のガードだけが効きます。存在しない ID を指定すると `404「ガードプロファイルが見つかりません。」` になります。

## 8. 判定の流れ

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
| generate | `poses`+`outfits`+`expressions`+`locations`+キャラの `outfit_tags`+`style_tags` をカンマでつないだもの |
| import | キャプション。空ならトリガーワード+`appearance_tags`(`caption_for`) |

全年齢のときは `appearance_tags` と `trigger_word` を検査しません(R18 のときは §5.2 のとおり検査します)。

### 保存先

| レーティング | 生成 | 引き直しの外れ | 取り込み |
|---|---|---|---|
| 全年齢 | `generated/` | `rejected/` | `manual/` |
| R18 | `generated_r18/` | `rejected_r18/` | `manual_r18/` |

ルートは `outputs/<root_name>/<トリガーワード、なければキャラ名>/` です。

## 9. 既知の制限

- **タグの文字列で判定している。** 同義語・言い換え・綴りの揺れは通ります。
  - 確認済みの抜け: `swimwear`、`see through`(ハイフンなし)、`nude_body` のような `_` つなぎ、`bare shoulders`、`undressing`
  - 日本語タグは対象外です。
- **画像そのものは判定しない。** 取り込み時に見るのはキャプションの文字列だけです。
- **未成年ガードは成人表現を誤検知することがある。** `young woman` は `young` で弾かれます。
- **成人フラグはタグで判定している。** 未成年として作ったキャラでも、シートから該当タグを消せば仕組みの上では成人フラグを付けられます。キャラの設定(年齢)そのものを確認する仕組みはないので、運用で守る必要があります。高校生として作ったキャラ(例: 九条 ゆら)には成人フラグを付けません。
- **ネガティブは確率的な抑制。** `SAFE_NEGATIVE` / `R18_NEGATIVE` は出にくくするだけで、出力を保証しません。

## 10. 変更するとき

- **基本のガード・未成年ガードにタグを足す** — `_BLOCKED_PATTERNS` / `_MINOR_PATTERNS` に正規表現で追加します。リスト全体が `\b(…)\b` で囲まれることを前提に書いてください。
- **リストからタグを外す** — 外すと全年齢の保証や R18 の未成年対策が弱まります。外すのではなく、誤検知の原因になっているパターンを狭める方向で直してください(例: `young` を `young girl` などに限定する)。
- **ガードプロファイル** — 上乗せだけの設計です。基本のガードを無効にする項目は足さないでください。

変更後は、少なくとも次を確認してください(NovelAI へのリクエストは不要。`fastapi.testclient.TestClient` で確認できます)。

- 全年齢で、基本のガードのタグを含むバリエーションが 422 になる
- 成人フラグのないキャラで `rating: "r18"` が 403 になる
- 成人キャラでも、未成年を示すタグを含むバリエーション・キャプションが 422 になる
- 未成年を示すタグがあるキャラに成人フラグを付けると 422 になる
- プロファイルの追加タグが全年齢・R18 の両方で効く
