# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **複数ユーザー対応の計画(`docs/multi_user_plan_jp.md`)** — `.env` の利用者情報(NovelAI のトークンなど)を利用者ごと・安全な形式で
  扱うための計画。`.env` の項目の仕分け、今 1 人用になっている箇所、ステップごとの進め方(まず運営者のトークンへの切り替えを止める)

- **キャラ一覧のページ(`/characters`)** — 登録したキャラをカードで並べる(参照画像・名前・成人/未成年・メモ・容姿と服装のタグ。
  名前・メモ・タグで探せる)。参照画像のタップで拡大。「データセットを作る」でキャラ別データセットへ(そのキャラを選んだ状態)、
  「生成したデータ(N枚)」でそのキャラのデータセットの画像を一覧・拡大(成人向けはぼかせる)、「出ている作品」から本棚・物語の
  ページへ。キャラ一覧から移った先のページの「← ホーム」は「← キャラ一覧」になり、戻ってこられる。API は
  `GET /api/story/character-works`(キャラごとの作品)と `GET /api/library/character-datasets[/{id}]`(キャラごとのデータセット)

- **話の流れのセット** — アニメでよくある巻ごとの流れ(夏休み: 星座観測 → 水着の買い物 → プール → 花火大会)に名前を付けて残す
  (`/api/story-arcs`、表 `story_arcs`)。おまかせ漫画で段階を選ぶと、テーマにその段階、補足にセットの説明とこの巻の後の流れが入る。
  既定のセット「夏休み(日本アニメの定番)」を初めて表を作るときに入れる(消したものは戻らない)。画面で足す・直す・消す
- **おまかせ漫画: シリーズの続き** — シリーズを選ぶと、シリーズの設定を補足に、既刊のあらすじを大枠に渡し、次の巻として保存する。
  できた巻のあらすじ(大枠の一文とページごとの内容)も残す。補足の欄も画面に足した

- **おまかせ漫画: ページごとにコマ数を変える** — 「コマ数をページごとに変える」で、1話=1ページにして、ページのコマ数(3〜6)を
  大枠シナリオのときに話の内容に合わせて決める(見せ場は少ないコマで大きく、会話の多い場面はコマを多く)。台本はそのコマ数で書き、
  最後のページ以外は次のページへの引きで終える。コマ割りはコマ数ごとの型(1〜6コマ。同じコマ数が続いても型を変える)を
  ページごとに保存して合成する(取り込んだ作品のコマ割りと同じ仕組み)。話数(ページ数)の上限を 4 → 10 に
- **うごイラ: 動画モデルで動かす(F)** — 元の絵から、動画モデル Wan 2.2(14B、画像から動画)で動画を作り、コマにする。
  動画モデルは Colab・GCP の GPU で動かす ComfyUI に作らせる(接続先は `.env` の `VIDEO_GEN_BASE_URL`。合言葉を確かめる中継
  `scripts/comfy_auth_proxy.py` を前に置き、`VIDEO_GEN_TOKEN` を Bearer で送る)。「繰り返す」(最初と最後を元の絵にする)と
  「自由に動かす」、長さ 2・3・5 秒(16fps)、中割りで 32fps。作ったうごイラは、中割り・書き出し・漫画への嵌め込みがそのまま
  使える。Colab のノートブック `notebooks/comfyui_wan_colab.ipynb`、GCP の Spot VM の起動スクリプト `scripts/gcp/comfyui_setup.sh`
  (使っていない時間が続くと VM を止める)、ワークフロー `assets/comfyui/`。API は `POST /api/ugoira/video`、
  `GET /api/ugoira/video/status`。手順と費用は `docs/video_remote_jp.md`。受け取ったコマは、この PC で整える(明るさを最初のコマに
  そろえる・繰り返しの継ぎ目を中割りで閉じる・4コマごとのちらつきを動きに合わせてならす。`video_smooth.py`)。ComfyUI からは VP9 の WebM で
  受け取る(PNG 33枚の 19MB が約 0.6MB になり、受け取りが約 3 分 → 8 秒)。Colab の L4 で試した記録は `docs/trials/2026-10-17_ugoira-video.md`。
  「最後の絵へ動かす」: 最初は元の絵、最後は作ったうごイラのコマ(A で顔だけ描き直したコマなど。コマの「動画の最後の絵に」で選ぶ)。
  最後が決まっているので、途中も元の絵柄のまま描かれる(自由に動かすは、終わりに近づくほど絵柄が離れた)
- **うごイラ: 部位ごとに動かす(E)** — 人物・髪・頭を分け(人物の切り分け、点で指す切り出し MobileSAM)、骨格(RTMPose)から
  首の位置を取り、髪はばねでつないだ鎖で風や頭の動きに遅れて揺らし、頭は首を中心に回す。動いて見えるようになった裏は
  LaMa で埋める。髪の表現は アニメ調(束ごと・2コマずつ止める)と リアル寄り(細かい毛筋・毎コマ・少しぶれる)。構図
  (仰向け・うつ伏せ・横顔・後ろ姿・しゃがみ・座り)は、絵のプロンプトのタグから選ぶ。どれもこの PC で動かす(モデルは合わせて
  約480MB。初めて使うときに取ってくる)。API は `POST /api/ugoira/rig`。全自動で作り、髪の取りこぼしがあれば
  **半自動で直せる**: 髪の範囲を赤く重ねて見せ、タップで足す・外す(タップした所のまわりを4倍に拡大して切り出し、元の座標に
  戻す。細い毛筋も取れる。小さい絵や「高精細」では AI 拡大を使う)。直したら、同じうごイラのコマを作り直す
  (`GET /api/ugoira/{id}/rig`、`POST .../rig/edit|undo|reset|redo`)。半自動の画面は拡大表示(1〜4倍)できる。
  **髪の裏を NovelAI で描く**こともできる(頭を除いた胴体だけを切り出し、髪の所を「頭なし・髪なし」で描かせる。Opus・1MP 以内
  なら Anlas 0。描いた所に髪が残っていたらシードを変えて描き直し、3回とも残ったら LaMa に戻す)
- **うごイラの違和感を減らす** — 描き直した所の明るさ・色を、元の絵に合わせてから重ねる(コマごとのトーンのずれを抑える)。
  作るときに中割りを足せる(画面の既定は1枚)。新しく **D. 変形で動かす**(描き直さない): 元の絵の画素を少しずつ動かして、
  呼吸・髪がなびく・首をかしげる・うなずく を作る。人物を背景から切り分けるモデル(skytnt/anime-seg、Apache-2.0、約176MB。
  初めて使うときに取ってくる)で人物だけを動かす。NovelAI は使わない。API は `POST /api/ugoira/warp`
- **うごイラを嵌め込んだ漫画** — 漫画 v2 のシーンごとに「このコマを動かす」でうごイラを選ぶと、合成したときにそのページが
  動くページ(アニメ WebP)になる。吹き出し・描き文字・ほかのコマはそのまま。1ページに動くコマが2つ以上あっても1本の
  アニメにまとめる。本棚のビューアでは動いて見え、動くページはページごとにアニメ WebP・GIF・Pixiv うごイラ(ZIP)で
  書き出せる(長い辺を縮められる)。PDF・画像のダウンロードは今までどおり静止画。各シーンから、そのコマのうごイラを作る画面へ
  移れる。API は `GET/PUT /api/manga-v2/{id}/ugoira`、`GET /api/manga-v2/{id}/animated/{page}`
- **おまかせ漫画** — 漫画ドラフトの「おまかせで作る」で、テーマ1行(とジャンル・話数)から、登場人物(選ばなければテーマに合う人物を
  考えてキャラシートと参照画像を作る)→ 大枠シナリオ → 台本 → コマの生成と合成まで、1つの処理で作る。台本までで止めることもできる
- **台本の見直しと、絵の確かめ・描き直し** — 生成の前に、セリフと筋から絵に要る物・状態を作画タグに足す(絵にしにくい状態は
  translucent body のように言い換える)。生成の後に、判定モデルで物や人数が描かれているかを確かめ、外れたコマは NovelAI の
  強調(1.5::タグ::)・抑制(-1::1girl::)を使って描き直す(よくなったときだけ置き換える)。おまかせ漫画と漫画ドラフトで使える。
  API は `POST /api/manga-v2/{id}/check-panels`。試行記録は `docs/trials/2026-10-14_panel-check.md`
- **うごイラの中割り** — コマとコマのあいだの絵を、この PC でフレーム補間のモデル(RIFE、MIT、約23MB。初めて使うときに取ってくる)
  で作って足す(あいだに1〜3枚、最後から最初へ戻るところにも)。元のうごイラは残し、中割りを足したうごイラを新しく作る
- **うごイラ(全体を少しずつ描き直す)** — 手を振る・首をかしげるなどの大きい動き。元の絵から img2img(強さ 0.7)で描き直し、
  体ごとの動きでは背景を元の絵のまま重ねる。動きの範囲に「体ごと」「絵全体」を足し、既定の動きに 手を振る・首をかしげる を足した
  (既定の動きは、まだ入れていないものだけ足す。消したものは入れ直さない)
- **うごイラ(部分だけ描き直す)** — ギャラリーの絵の目・口・髪だけを NovelAI のインペイントで描き直して、まばたき・口パク・
  笑顔に変わる・髪の揺れのパラパラ漫画にする(範囲の外は元の絵のまま。Opus なら Anlas 0)。コマの並び・表示時間を直し、
  プレビューで確かめて、Pixiv 用 ZIP・GIF・アニメ WebP に書き出せる。動きは画面から足せる。ギャラリーの「うごイラを作る」と
  ホームから開く。試行記録は `docs/trials/2026-10-16_ugoira.md`
- **Docker で動かす** — アプリ本体(バックエンド + ビルドした画面)のイメージ(`Dockerfile`)と、compose の `app` サービス
  (profile `app`)を追加した。画面と API を :8000 から配り、LLM は compose の中の Ollama を使う。data/・outputs/・.env・
  バックアップの写し先・Windows のフォント(読み取り専用)をボリュームで渡す。開発は今までどおり dev-ctl.ps1。
  管理ページは、コンテナで動いているときはそれに合わせて表示する。使い方は `docs/docker.md`
- **データの点検** — 管理ページのコンテンツガードの「今の設定でデータを点検する」で、今の設定(未成年キャラの安全対策)で
  止まるはずのものが前に作ったデータに残っていないかを一覧にする: 未成年の手がかりがあるのに成人・未定義のキャラ、
  未成年がいる性的な場面とそのコマ・挿絵、本文に未成年を示す語がある成人向けの物語、性的で未成年を思わせるタグの
  生成画像・データセット。点検はデータを変えない。画像は選んでごみ箱へ、キャラはその場で未成年と定義できる。見本はぼかして出す
- **ディスクの整理** — 管理ページ(操作とデータ)の「使われていないファイルの整理」で、種類ごとに一覧を開いて見本を見ながら
  選べる。片付けたものはごみ箱に入り、30日のあいだは元に戻せる(これまでは、すぐに消えて戻せなかった)。
  合成したページの前の版(合成し直す前のページ)と、どの物語にも使われていない v1 の挿絵を、片付けられる種類に足した
- **作品単位の書き出し・取り込み** — 本棚の物語1つ(本文・タグ・コマ・ページ・合成したページ・登場キャラのキャラシートと
  参照画像)を ZIP にし、別の PC の本棚に新しい物語として取り込める。登場キャラは、同じ名前のキャラを使うか新しく入れるかを選ぶ。
  性的な場面に成人でないキャラがいるコマ・ページの画像は、書き出しにも取り込みにも入れない
- **キャラシートの書き出し・取り込み** — 選んだキャラのキャラシート(容姿・服装・画風・ネガティブ・トリガーワード・基準シード・
  メモ・年齢区分)と参照画像を ZIP にし、別の PC で取り込める。同じ名前があれば、キャラごとに 別名 / 上書き / 入れない を選ぶ。
  取り込みも画面からの保存と同じ検査(未成年を示すタグがあると成人にできない・未成年の定義を外す上書きはパスワード)。
  キャラ別データセットのページと、物語のページの登場人物の欄から使える
- **画像・作品データのバックアップ** — 管理ページ(操作とデータ)で写し先のフォルダ(外付けドライブなど)を指定すると、DB と
  生成画像・漫画のコマとページ・取り込んだ作品・素材・出版物・ごみ箱を写す(キャラ別データセットは選べる)。中身ごとに1回だけ
  置くので、2回目からは増えた・変わったファイルだけを写す。1日1回の自動バックアップ、7回分を残す。「この時点に戻す」は、
  先に今の状態を1回バックアップしてから DB とファイルを書き戻す。モデル・フォント・評価データと `.env` は入れない
- **ごみ箱** — ギャラリー(生成画像・コマ・挿絵・データセットの画像)・本棚の物語・キャラを消すと、ごみ箱に入り、30日のあいだは
  元に戻せる(物語はシーン・コマ・ページ・登場キャラの割り当て・シリーズ・作者・ブックマークごと)。期限を過ぎると自動で片付く。
  ホーム・ギャラリー・本棚から開ける。API は `/api/trash`
- **ギャラリーの画像全体の画質向上** — 画像の画面の「画質を上げる(画像全体)」で、この PC の AI 拡大(2倍・4倍、長い辺
  4096px まで)か、NovelAI の img2img で弱めに描き直してから AI 拡大する(Opus なら Anlas 0)。時間がかかるので処理を切り離し、
  進み具合を出す。結果は新しい画像としてギャラリーに残る(拡大表示の範囲を高解像度にする機能はそのまま)。
  性的な画像に未成年を思わせるタグがあれば、範囲・全体とも画質を上げない。API は `/api/image/enhance`
- **未成年キャラの安全対策(試行)** — 性的な場面(コマ・ページ・状態の描き直し)に成人でないキャラがいれば止める。
  キャラシートに年齢区分(未成年 / 成人 / 未定義)を足し、未成年の手がかり(キャラシートのタグ、名前と同じ文にある
  女子高生・〇歳 などの語)があれば成人と定義していても未成年として扱う。未定義のキャラの扱い(止める / 全年齢で描く)は
  管理ページの項目。本文に未成年を示す語がある物語は、成人向けのタグ付けをしない。止めた記録は管理ページで見られる。
  試行記録は `docs/trials/2026-10-15_minor-safeguard.md`
- **コンテンツガードのパスワード** — 守りを緩める変更(止める語を減らす・未定義の扱いを変える・未成年の定義を外す・
  ガードプロファイルを減らす/消す)だけにパスワードを求める。管理ページで設定・変更でき、忘れたときはこの PC で
  `scripts/reset_guard_password.py`。はじめの値に戻す・強める変更には要らない
- **コンテンツガードの書き出し・取り込み** — すべての項目とガードプロファイルを JSON で書き出し、取り込む前に項目ごとの
  違い(強める / 緩める)を見せて選んで取り込める。「すべてはじめの値に戻す」を欄の上に移した
- **物語のシーンごとの登場人物** — 物語のページのシーン一覧で、シーンごとに登場人物を外す・登録済みのキャラから足す・
  名前を入れて新しく作って足すことができる(抽出の当たり外れを直せる)
- **台本の書き方** — 本文に `名前：「セリフ」` と書くと、その人のセリフとして吹き出しのしっぽを向け、
  `名前：（ガタッ、ドキドキ）` と書くと、そのシーンの描き文字の候補になる。ここに書いた名前は、登場人物の抽出で
  人物と分かっているものとして扱う
- **登場人物の抽出の切り替え** — 名前の候補が見つかっても判定で誰も人物と認められなかったときは、
  本文を文章モデルに読ませて登場人物を挙げる方法に切り替える
- **ギャラリーの拡大表示の範囲を高解像度に** — 全画面で拡大して見ている範囲を切り出して、高い解像度にする。
  「描き直し」は、この PC で AI 拡大(アニメ向けの超解像。CDC のモデル、初回に約160MB を取ってくる)してから、
  NovelAI の img2img で弱めに描き直す(強さは 控えめ 0.25 / ふつう 0.35 / 強め 0.5。Opus なら Anlas 0)。
  「AI拡大だけ」は NovelAI を使わず、絵の中身を変えずに4倍にする。結果はギャラリーに新しい画像として残り、
  「元の画像を開く」で元に戻れる。API は `POST /api/image/region-redraw`。試行記録は `docs/trials/2026-10-15_region-redraw.md`
- **描き直しで作る人物の状態(透明・幽霊・石化 など)** — 台本で人物の動作のタグ(`invisible` など)が状態に当たると、コマを描いたあとに、
  その人物の範囲だけをインペイントで描き直す(透明 → 空の服が浮いている。ほかの人物と背景はそのまま)。人物は名前順に左から並ぶので、
  頭の検出を左から対応させる(数が合わないコマは描き直さずに知らせる)。状態は漫画ドラフトの「描き直しで作る状態の管理」で足し・直し・消しができる。
  おまかせ漫画・漫画ドラフト・物語の自動漫画化で既定でオン。API は `POST /api/manga-v2/{id}/redraw-states` と
  `/api/manga-v2/redraw-states`(一覧・追加・書き換え・削除)。試行記録は `docs/trials/2026-10-14_panel-check.md`
- **キャストのセット** — よく使うキャラの組み合わせに名前を付けて保存し、漫画ドラフトとおまかせ漫画で押すだけで選べる
- **物語の自動漫画化を最後まで** — 取り込んだ物語を、冒頭のシーンだけでなく最後まで漫画にできる
- **題材の候補** — ジャンルを選ぶと、小説家になろう(なろう小説API)と AniList の今の人気の傾向(キーワード・タグ)を手がかりに、
  新しいテーマの候補を5つ出す(特定の作品の筋は使わない。外部に問い合わせられなければ、ジャンルだけから考える)
- **入力値をキャラシートに反映** — 画像生成ページの入力(プロンプト・キャラごとのプロンプト・ネガティブ・シード値)や、
  メタデータ管理ページで読んだ画像の設定を、キャラシートに反映できる。プロンプトを容姿・服装・画風に分けた下書きを
  確かめて、新しいキャラとして追加するか、既存のキャラに上書きする(反映するのはチェックした項目だけ)
- **作品の取り込みで描き文字(手描きの効果音)を特定** — 描き文字の場所の候補を見つけ、取り込みの画面に切り抜きを並べる
  (見つかるのは半分強。違うものは外せる)。「自分の作品を作り直す」では読みを入れると台本の効果音になり、見落としは
  読みだけで足せる。読みを入れた描き文字に形の似た候補には、読みの候補を出す(同じ取り込みの中だけ)。
  「似た漫画を作る」では読みは持たず、描き文字の有無と大きさを台本作りに渡す
- **キャラ別データセットのバリエーションを、プロンプトから読み込む** — 画像生成で使ったプロンプト(貼り付け・最近の履歴)や、
  画像から読んだプロンプト(NovelAI の埋め込み。無ければリバースプロンプトの推定)から、構図・ポーズ・服装・表情・場所の
  キーワードを取り出して、バリエーションの候補に足せる。容姿・人数・品質・画風のタグは取り出さない。どの軸か分からない
  ものと成人向けのものは、軸を選んだときだけ足す
- **バリエーションのキーワードを、プロンプトチャンクから読み込む** — キーワードの読み込みで、プロンプトチャンクを選べる
  (分類で絞り込み・検索・まとめて選択)。チャンクの分類の名前(表情・服・風景など)から、軸の見当を付ける。
  取り出したキーワードは軸ごとの枠に並び、足すものをチェックで選ぶ。分類が違うもの・分からないものは、別の枠へ
  ドラッグするか、横の欄で振り分ける
- **キャラシートの標準の体型** — キャラシートに「標準の体型」を足し、容姿(顔・髪・目)と分けた。キャラ別データセットの状態は、
  体型の欄を丸ごと置き換える(外すタグの書き漏らしで体型がぶつからない)。容姿から体型のタグを移すボタン付き
- **状態を、部位だけの描き直しで作る(インペイント)** — キャラ別データセットで、ふだんの体の絵から、状態の部位(お腹・胸・上半身・脚)
  だけを描き直して状態の絵を作れる。顔・服・背景が同じで体だけが違う組ができる。部位の範囲は、頭の検出から見積もる
  (見つからなければ状態のタグで生成し直す)。試行記録は `docs/trials/2026-10-15_dataset-body-parts.md`
- **データセットに顔の切り抜き** — 生成した絵から顔のアップを切り抜いて `faces/` に保存できる
- **キャラ別データセットのシチュエーションと状態** — シチュエーション(場所・服装・ポーズなどをひとまとまりにしたもの。
  ジムで運動、カフェで休憩 など)を選ぶと、1枚ごとにどれか1つを使い、軸どうしがばらばらに組み合わさらない。状態
  (キャラシートの容姿から外すタグと足すタグ。食べ過ぎでお腹が出る など)を選ぶと、容姿を一時的に変える(ふだんの体の枚も
  混ぜられる)。どちらも DB で持ち、画面で足す・直す・消す、プロンプトチャンクから読み込むができる。試行記録は
  `docs/trials/2026-10-14_dataset-situations.md`
- **成人キャラだけに使うタグ**(コンテンツガード `dataset.adult_only_tags`。妊娠など) — キャラ別データセットで、成人フラグの
  あるキャラにだけ使え、未成年を示すタグとは一緒に使えない(全年齢・R18 のどちらでも)
- **キーワードの読み込みから、キャラシートも変える** — 読み込んだキーワードのうち、容姿(髪・目・体)と画風のものは、
  キャラシートの枠(容姿・普段の服装・画風)に並ぶ。チェックを入れると、選んでいるキャラのキャラシートに入る
  (いまの内容に足す / 選んだもので置き換える。置き換えるときは、消えるキーワードを先に見せ、人数のタグは残す)。
  バリエーションの枠とキャラシートの枠のあいだも、ドラッグや欄で移せる。チャンクの分類(髪型・体型・絵柄・
  アーティストなど)からも、キャラシートの項目の見当を付ける
- **バリエーションの候補を DB で管理** — 候補(これまではコードに固定)を DB に移し、「追加…」で足したものが残るようにした。
  「候補を整理」で消せる。API は `GET / POST / DELETE /api/lora-dataset/variations` と `POST …/variations/extract`
- **ギャラリーの画像から漫画を作る** — ギャラリーで選んだ画像(選んだ順に1枚=1コマ、24枚まで)から漫画を作る。各画像の
  プロンプトから似た絵をコマとして生成し直し、指定したジャンルに合う話・セリフ・描き文字を文章モデルが付ける。
  ジャンルに合わせて表情・動作のタグも足し引きする(人数と見た目のタグは変えない)。台本までで止めることもできる
- **取り込んだ作品にジャンルを割り当て** — 作品の取り込みで、漫画ドラフトのジャンルを割り当てられる(取り込むときか、あとから)。
  漫画ドラフトでそのジャンルを選ぶと、構成の参考の先頭に出る(オプションで、参考をランダムに選ぶ)。似た漫画を作るときの
  ジャンルにもなる
- **ジャンルの追加・削除** — ジャンルの候補を DB で持ち、漫画ドラフトと作品の取り込みの「ジャンルの管理」から足したり
  消したりできる(これまでは画面に固定で書いてあった)
- **ギャラリーから本棚の作品を直接開くリンク** — 物語の画像を開いた画面(「本棚の作品を開く」と、詳細の物語名)と、物語で
  絞り込んだときの絞り込み欄から、本棚をその作品で開ける。本棚の作品の「編集」は「物語のページを開く」に名前を変えた
- **ギャラリーから似た画像を生成** — 画像を開いた画面の「似た画像を生成(画像生成ページへ)」で、その画像のプロンプト・
  キャラごとのプロンプト・モデル・サイズ・シード値などを画像生成ページに入れる。生成設定が埋め込まれていない画像は、
  ギャラリーの記録にあるプロンプト・モデル・サイズ・シード値だけを入れる

### Fixed

- **コマの生成が途中で止まる** — NovelAI の画像生成で、一時的な失敗(通信の切断・タイムアウト・429・5xx)を間を空けて3回まで
  やり直す(40コマ前後の漫画で、1回の失敗で全体が止まっていた)。止まったときは、例外の種類をエラーに出し、ログに残す
  (メッセージの空の例外で、何で止まったか分からなかった)
- **場面ごとの服装** — プールや花火大会の場面でも、キャラシートのふだんの服装(制服)で描かれた。台本に人物ごとの「その場面の服装」
  (`outfits`。水着・浴衣・私服など、ふだんと違うときだけ)を書かせ、場面の人物(`scene_characters.outfit_tags`)に残して、コマの
  生成・確かめ・描き直しで、ふだんの服装の代わりに使う。`PUT /api/story/scenes/{id}/characters` の `outfits` で手直しできる
- **コンテンツガードの既定値を一般誌・少年誌の水準に** — 全年齢のネガティブから swimsuit・cleavage を外す。漫画の下書きの決まりを
  「性的な描写はしない。水着・体操服・パジャマなど、日常や行事で自然な服装は描いてよい」に(「露出」の禁止で、プールや水着の場面を
  避けていた)。下書きの画面のネガティブから cleavage を、似た漫画で使わない語から navel・thighs・hips・sweat・wet・swimsuit などを
  外す。前の既定値は `previous` に残し、DB の値が前の既定値のまま(画面で変えていない)なら新しい既定値に入れ替える
- **ページごとにコマ数を変える漫画で、となりのページの繰り返し** — 台本をページごとに書くとき、それまでのページのセリフを
  全部渡し(これまでは直前の4コマだけで、6コマのページの前半が見えなかった)、前のページの出来事・提案・セリフを繰り返さない
  よう指示する。大枠でも、ページごとに新しい出来事を入れ、前のページと同じ出来事を繰り返さないようにする
- **ギャラリーの全画面で拡大したときのメニュー** — しばらく操作しないと閉じるボタンなどが隠れるのに合わせて、「この範囲を
  高解像度に」のメニューも隠す(拡大した絵の下が見えなかった)。真ん中をタップすると、また出る。作っている途中・できた・エラーの
  表示は残す
- **うごイラ E(部位ごとに動かす)の仕上がり** — 首をかしげるとき、垂れた髪も首を中心に回す(肩に掛かった毛筋が引き伸ばされて
  筋に見えた)。髪の裏を NovelAI で描いたあと、首の横を描き直した胴体を手がかりに埋め直す(うすい灰色が残った)。胴体を塗る所の
  真ん中が黒くなるのを直した。半自動の「外す」で、タップした赤い塊をまるごと外す(一部しか外れなかった)
- **うごイラの中割り・動くページのちらつき** — まばたき・口パクのように一部だけが変わるコマを「同じ絵」と見なして中割りを
  飛ばしていたのを直した。中割りは前後のコマが違う所だけに使う(ほかの所がわずかにずれて、ちらついた)。アニメ WebP は、
  前のコマと同じ所をそのまま使って書き出す(動かないコマもちらついた。大きさも半分ほどになる)
- **物語の登場人物をそろえると、別の物語の同じ名前のキャラを書き換える** — 名前が同じだけの別の物語のキャラ(「お母さん」など)を
  使い、そのキャラに容姿や参照画像・基準シードを入れていた。別の物語で使っているキャラは使わず、この物語用のキャラを
  「名前(物語の題名)」で新しく作る(同じシリーズの巻のキャラと、どの物語にも使っていないキャラは、これまでどおり使う)
- **物語の自動漫画化で、キャラ参照を必ず使う** — 参照画像のあるキャラがいると、1コマ +5 Anlas のキャラ参照を必ず使っていた。
  「キャラ参照を使う」で切れるようにした(切ると、キャラシートと基準シードで見た目をそろえる)
- **スマホでファイルを選ぶと、選択や入力が消える** — 開発サーバーの自動更新(HMR)の接続が切れて戻ると、ページが
  読み込み直されていた。自動更新を既定でオフにした(`NAI_HMR=1` でオンにできる)
- **学習済みモデルが使えないとき、取り込みでセリフが1つも読めない** — 文字の領域を数えたあとの読み取りが、場所だけの
  結果を返していた(RapidOCR が直前の呼び出しの指定を覚えているため)。読むときの指定をいつも渡すようにした
- **開発サーバーが勝手に止まる(取り込んだ作品が 0 件に見える)** — 自動更新をオフにしている間、自動更新用の接続要求
  (WebSocket)が来て途中で切れると、開発サーバーごと落ちていた。接続要求はすぐ閉じるようにした

### Planned

- Inpainting UI
- Character Reference UI
- Vibe Transfer / ControlNet UI
- Batch generation UI

---

## [0.8.0] - 2026-10-11

### Added

- **物語と漫画(漫画v2)** — 物語エディタから漫画まで。コマ単位で絵を生成し、コマ割り・吹き出し・ナレーション・描き文字を
  合成してページにする。漫画ドラフト(大枠シナリオ → 台本 → 漫画)、斜めのコマ・枠なし・裁ち切り・見開きにも対応
- **ギャラリーと本棚** — 生成した画像と、物語・漫画を一覧して読む。全画面表示(ズーム・スワイプ・慣性つき)、
  ブックマーク、成人向けの区分とぼかし、シリーズ・作者・似ている作品
- **作品の取り込み** (`/manga-import`) — PDF・画像・zip からコマ割りと構成を読み取る。取り込むときに使い方を選ぶ:
  - 似た漫画を作る — コマ運び・場面・所作を再現し、話とセリフは新しく作る(セリフは型だけを読み、文面は保存しない)
  - 自分の作品を作り直す — セリフの文面も読み、セリフごと台本に起こして描き直す(読み取ったセリフは画面で直せる)
  - 仕様は docs/manga_import_jp.md
- **登場人物をそろえる** — 物語の内容から既存のキャラを割り当て、いなければ作る。参照画像も候補から自動で1枚選ぶ。
  物語をそのまま漫画にする(自動の漫画化)
- **出版(パブリッシュ)** (`/publications`) — ギャラリー・本棚で選んだ画像や本から NovelAI の生成情報(メタデータ)を
  取り除き、出版物として別に保存して ZIP にまとめる。アイテムの表示画面からも出版でき、作成後にダウンロードする
  オプションつき。仕様は docs/publish_jp.md
- **画像から設定を読み込んで生成** — NovelAI の画像を渡すと、プロンプト・キャラごとのプロンプト・モデル・サイズ・
  サンプラー・シード値などを画像生成ページに入れる(メタデータ管理ページからも渡せる)
- **タグ補完** — 画像生成ページのプロンプト欄で、打っている途中のタグから候補を出す。日本語から英語のタグも引ける
- **表情の変更と2倍の拡大** — できた画像の仕上げ(NovelAI のディレクターツール)
- **V5 モデルとキャラの位置の指定** — 画像生成ページで V5 Full / Curated を選べる。キャラごとのプロンプトの位置を
  指定するか、AI にまかせるかを選べる
- **リバースプロンプトの作り直し** — 画像に埋め込まれた設定の読み取りと、WD Tagger による推定。タグごとの確率、
  人物ごとのキャラプロンプト、構図・白黒の判定。検証は docs/trials/2026-10-10_reverse-prompt.md
- **処理状況のトレイ** — 時間のかかる処理を最小化しても、どのページからでも戻れる。再読み込みしても引き継ぐ
- **スマホにインストール(PWA)** — LAN 用の証明書(`scripts/make_lan_cert.py`)で https にして、ホーム画面に追加できる
- **開発管理者のページ** (`/admin`) — サーバーの状態とログ、動いている処理、設定(.env)、再起動、保存領域の掃除、
  DB のバックアップ、既定値・プリセット。LAN の中から使える。設定を変えると、反映に何が要るか
  (PC の再起動 / サーバーの再起動 / すぐ反映)をダイアログで知らせる
- **コンテンツガードの編集** — 止めるタグ・取り除くタグ・足すネガティブ・LLM への指示・成人向けの判定の語を、
  API (`/api/content-guard`) と開発管理者のページで編集できる。仕様は docs/content_guard_jp.md
- **CI** — GitHub Actions で ruff・pytest・フロントのビルドを実行
- **キャラシート** — 登場人物に容姿(固定)・普段の服装・画風・ネガティブ・基準シード・トリガーワードを持たせ、
  物語の挿絵/漫画v2のコマ生成に自動で反映(シード未指定時はキャラの基準シードを使用)
- **キャラ別データセット** (`/character-dataset`) — キャラシートを元にポーズ/服装/表情/場所を差し替えて1枚ずつ生成(全年齢向けのみ)
  - 参照画像との類似度(色の近さ / 画像AIによる採点)が基準未満なら、シードをずらして引き直し、最も似た1枚を採用。外れは `rejected/` に保存
  - 手持ち画像の取り込み(`manual/` にPNG+キャプションで保存)
  - 構図の軸(full body / cowboy shot / from side など)を追加し、組み合わせは各軸の値が均等に出るように選ぶ
  - 参照する範囲(キャラのみ / キャラ＋画風)を選択可能に
  - 既定値を検証結果に合わせて変更: 参照画像オフ(使うときはキャラのみ・強さ0.7)、引き直しなし、横顔は `from side, profile`。
    検証は docs/trials/2026-10-06_character-dataset-stability.md
  - 画像AIの採点は空応答・タイムアウト時に最大3回まで採点し直す
  - キャラ別データセットの画面でキャラの追加とキャラシートの編集(参照画像の登録・差し替えを含む)ができるように。
    編集部分は物語の「登場人物」と共通の部品(`CharacterSheetEditor`)
  - キャラシートの別名保存(参照画像も複製、トリガーワードの重複は拒否)と、使われている物語を示してからの削除
  - 生成枚数を組み合わせの数より多く指定できるように(一巡ごとにシードをずらして繰り返す)。生成ボタンの横でも枚数を指定可能
- **ギャラリーにデータセットを表示** — キャラ別データセットの画像(generated / manual、R18 は成人向け扱い)を「データセット」として一覧。
  削除すると学習用フォルダからも画像とキャプションを消す(確認ダイアログで明示)。rejected/ は出さない
- **キャラの付け替え** — 物語の「登場人物」で、その物語のシーンに割り当てたキャラを別のキャラに一括で付け替え
  - ガードプロファイル — ブロックするタグ/ネガティブを追加・削除して名前を付けて保存し、選んで使用。
    基本のブロックリストは固定で、プロファイルは上乗せのみ
  - R18 — キャラシートで成人フラグを付けたキャラのみ。未成年を示すタグ(school uniform, student 等)は
    キャラシート・バリエーション・キャプションのどこにあっても拒否し、成人フラグも付けられない。
    保存先は `generated_r18/`・`manual_r18/` に分離

### Changed

- **novelai-sdk を 0.8.1 から 0.14.1 に更新**
- **コンテンツガードの定義を DB に移動** — これまで Python の定数だった値を、テーブル `content_guard_rules` で持つ
  (はじめの値は `src/python/content_guard_defaults.json`)。はじめの値のままなら判定は変わらない
- **開発サーバーの既定を http に** — `scripts/dev-ctl.ps1` は何も付けなければ http で起動する。https は `-Https`
  (または環境変数 `NAI_HTTPS=1`)
- **漫画の取り込みで読むものが増えた** — 使い方を選んで取り込むと、場面・所作・セリフも読む。前からある取り込みは
  「構成のみ」として残り、読み取り直すと切り替えられる

### Fixed

- **キャラごとのプロンプトの位置が効いていなかった** — 古い SDK は位置を送っても NovelAI に無視される形だった
  (SDK の更新で解消)
- **UC プリセット「なし」が使えなかった** — SDK の検証で弾かれていたのを、アプリ側で回避 (`src/python/sdk_compat.py`)
- **キャラごとのプロンプトの変更が、生成に反映されないことがあった**

---

## [0.7.1] - 2026-05-11

### Fixed

- **Ollama 対応** — vLLM の代わりに Ollama (llama.cpp ベース) を LLM バックエンドとして使用
  - vLLM 0.20.x は CUDA compute capability 7.5+ が必要 (GTX 10xx / Pascal 世代は対象外)
  - Ollama は Pascal GPU (sm_61) を含む旧世代 GPU で動作
  - `docker-compose.yml` を `vllm/vllm-openai` から `ollama/ollama` へ変更
- **Qwen3-VL 思考モード対応** — `routes/llm.py` を Ollama ネイティブ API に切り替え
  - OpenAI 互換 API では Qwen3 の思考トークン (reasoning) が `content` を空にする問題を回避
  - Ollama ネイティブ `/api/chat` を `think: false` で呼び出し、content フィールドを正常に取得
  - ストリーム完了後に `<think>...</think>` ブロックをレスポンスから除去
  - vLLM バックエンドでは従来通り OpenAI SDK ストリーミングを使用 (後方互換)
- **デフォルトモデル更新** — `Qwen/Qwen2-VL-7B-Instruct` → `qwen3-vl:2b` (Ollama 用)

### Configuration

モデル起動手順（Ollama）:

```bash
docker compose up -d
docker compose exec ollama ollama pull qwen3-vl:2b
```

`.env`:
```
VLLM_BASE_URL=http://localhost:11434/v1
VLLM_MODEL=qwen3-vl:2b
VLLM_VISION_BASE_URL=http://localhost:11434/v1
VLLM_VISION_MODEL=qwen3-vl:2b
```

---

## [0.7.0] - 2026-05-11

### Added

- **AI アシスタント** (`/llm`) — vLLM 経由のローカル LLM を活用した 6 機能のタブ UI
  - **プロンプト整形** — 曖昧な日本語・英語の説明を NovelAI コンマ区切りタグに変換
  - **キャラ設定生成** — キャラクター概念 → ビジュアルタグ JSON（name / positive_tags / negative_tags / notes）
  - **物語ドラフト** — 前提設定 → シーン付き物語文 + 各シーンの生成プロンプト候補
  - **補助テキスト生成** — コンセプト → positive / negative プロンプトの最適ペア
  - **メタデータ生成** — コンセプト → 全生成パラメータ JSON（steps / scale / sampler 等を一括提案）
  - **リバースプロンプト** — アニメ・イラスト画像をアップロード → 再現用プロンプトを逆算（ビジョン LLM）
- **「画像生成に使用」ボタン** — 各パネルの出力を `localStorage` 経由で `/generate` に一括転送
- **「🤖 強化」ボタン** — `/generate` のプロンプト入力欄から直接 AI アシスタントへ往復
- **`docker-compose.yml`** — GPU 1 枚構成（Qwen2-VL-7B-Instruct 単一インスタンスでテキスト+ビジョン両対応）
  - コメントアウトで GPU 2 枚の text/vision 分離構成に切り替え可能
- **`src/python/llm_client.py`** — `openai.AsyncOpenAI` シングルトン（text / vision）、起動時に自動初期化
- **`src/python/llm_models.py`** — 6 機能分の Pydantic リクエストモデル
- **`POST /api/llm/prompt-format`** — プロンプト整形（SSE ストリーミング）
- **`POST /api/llm/char-gen`** — キャラ設定生成（SSE）
- **`POST /api/llm/story-draft`** — 物語ドラフト生成（SSE）
- **`POST /api/llm/aux-text`** — 補助テキスト生成（SSE）
- **`POST /api/llm/metadata-gen`** — メタデータ生成（SSE）
- **`POST /api/llm/reverse-prompt`** — リバースプロンプト（SSE、ビジョンモデル使用）
- `openai>=1.0.0` を Python 依存関係に追加

### Configuration

vLLM を使用するには `.env` に以下を設定してください:

```
VLLM_BASE_URL=http://localhost:8001/v1
VLLM_MODEL=Qwen/Qwen2-VL-7B-Instruct
VLLM_VISION_BASE_URL=http://localhost:8001/v1
VLLM_VISION_MODEL=Qwen/Qwen2-VL-7B-Instruct
HF_TOKEN=hf_...
```

設定なしでもサーバーは起動し、LLM エンドポイントのみ 503 を返します。

---

## [0.6.0] - 2026-05-10

### Added

- **Metadata management UI** (`/metadata`)
  - Drag & drop or click-to-browse file upload (PNG / WebP)
  - Auto-extracts metadata on upload via `POST /api/metadata/extract`; nested JSON
    strings (e.g. NovelAI `Comment` field) are expanded inline for readability
  - Erase controls: target selector (`png_info` / `alpha` / `both`) + erase button
    via `POST /api/metadata/erase`; download cleaned image as `<name>_clean.png`
  - Wired up `/metadata` route and Home dashboard card
- **Image-to-Image mode** on the image generation page
  - Toggle in the sidebar enables img2img controls: reference image upload (drag &
    drop or click), Strength slider (0.01–0.99), Noise slider (0–0.99)
  - Generate button disabled until a reference image is provided while in img2img
    mode
  - Reference image passed to `POST /api/image/generate/stream` via the existing
    `i2i` field; streaming preview works as in text-to-image mode

---

## [0.5.0] - 2026-05-10

### Added

- **Anlas cost preview** – estimated Anlas consumption shown below the generate button
  - Calls `POST /api/image/anlas` with 500 ms debounce whenever parameters change
  - Displays "推定消費: N Anlas"; silently suppressed on network error
- **SSE streaming generation** – live intermediate preview during image generation
  - Switched from `POST /api/image/generate` to `POST /api/image/generate/stream`
  - Uses `fetch` + `ReadableStream` to consume SSE (supports `Authorization` header,
    unlike `EventSource`)
  - Intermediate frames render progressively in the canvas with a spinning overlay badge
  - Final frame replaces the preview automatically when generation completes

---

## [0.4.0] - 2026-05-10

### Added

- **Settings persistence** – all generation parameters saved to `localStorage`
  - Prompt, negative prompt, model, size, steps, scale, UC preset, and quality toggle
    persist across page visits and browser restarts
  - Text inputs debounced at 300 ms to avoid excessive writes; discrete controls write
    immediately
  - Seed intentionally excluded (random per generation is the expected UX)
- **`src/javascript/hooks/useLocalStorage.ts`** – generic debounced `localStorage`
  sync hook; flushes pending timer on component unmount

---

## [0.3.0] - 2026-05-10

### Added

- **Image generation page** (`/generate`) with full parameter control
  - Prompt / negative prompt textareas
  - Model selector (V4.5 Full/Curated, V4 Full/Curated, V3)
  - Size selector (Portrait, Landscape, Square, Large variants)
  - Steps (1–50) and Scale (0–10) sliders
  - Seed input, UC preset selector, quality tag toggle
  - Ctrl+Enter keyboard shortcut to trigger generation
  - Download and re-generate buttons on result
- **`src/javascript/api.ts`** – authenticated `fetch` utility (Bearer token injection)
- **Home dashboard** now links to the image generation page

### Changed

- Home page: 画像生成 card is now clickable and navigates to `/generate`

---

## [0.2.0] - 2026-05-10

### Added

- **NovelAI account login** (email + password)
  - `src/python/auth_utils.py`: argon2id key derivation matching the official client
  - `POST /api/auth/login` endpoint (`src/python/routes/auth.py`)
- **React frontend** with routing (`react-router-dom`)
  - Login page with NovelAI dark theme
  - Home dashboard with feature overview
  - Protected routes (redirect to `/login` when unauthenticated)
- **Dynamic Bearer token support** – every API request uses the token from the
  `Authorization` header; falls back to `NOVELAI_API_TOKEN` env var
- **Vite proxy** – `/api/*` forwarded to `http://127.0.0.1:8000`
- `argon2-cffi` and `httpx` added to Python dependencies

### Security

- Prompts and generated images live in browser memory only; never persisted
- Raw password discarded immediately after key derivation
- Access token stored in `localStorage` (browser-local, cleared on logout)
- `outputs/` and `samples/` excluded from git (binary files / third-party HTML)

---

## [0.1.0] - 2026-05-06

### Added

- **FastAPI relay server** (`src/python/`) wrapping novelai-sdk
  - `POST /api/image/generate` – image generation
  - `POST /api/image/generate/stream` – SSE streaming generation
  - `POST /api/image/anlas` – Anlas consumption estimate
  - `POST /api/metadata/extract` – PNG metadata extraction
  - `POST /api/metadata/erase` – metadata erasure
- Pydantic v2 request/response models (`src/python/models.py`)
- React + TypeScript scaffold with Vite
- `scripts/generate_cat_garden.py` – standalone SDK usage example
- Project initialization: LICENSE, README, CONTRIBUTING, SECURITY
