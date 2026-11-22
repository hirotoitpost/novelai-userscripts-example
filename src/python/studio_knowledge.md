# NAI Diffusion 4.5 のタグの効き方(対話生成のアシスタントに渡すメモ)

実際の生成で確かめたことだけを書く。新しく分かったことは、ここに足していく。

## 書き方
- タグは英語の Danbooru タグをカンマ区切り。重みは `1.3::tag::`(1.1〜1.6 くらい。2 を超えると絵が崩れやすい)。
- 前回のタグを土台にして、変えるのは指摘された所だけ。重みを黙って消さない。頼まれていない背景・服・小物を足さない。
- キャラの見た目(髪・目・肌・体型)はアプリが入れる。scene には書かない。キャラの名前もタグにしない(効かないうえ、別人が混ざる)。
- 効かないタグを強めても変わらないときは、邪魔しているタグ(強すぎる別の指定)を外すか、ネガティブへ移す。

## 構図・カメラ
- 下から: `from below`。もっと真下から見上げる: `pov, viewer lying on floor under her, 2::from directly below::, 1.6::looking up::, 1.5::fisheye::, wide angle`。ネガティブに `eye level, from front`。
- 上から: `from above`、真上から: `from directly above, overhead view`。
- 全身: `1.6::full body::, feet in frame`。ネガティブに `close-up, upper body, cropped legs`。
- 顔や胸の重み・`face focus`・`breast focus` は、全身・お腹・腰を画面から押し出す。全身を描かせたいときは顔と胸の重みを外し、ネガティブに `face focus, breast focus, close-up, upper body`。見せたい所(`stomach, navel, midriff, hips` など)に重みを付ける。
- 横長(landscape)で1人の全身を描くと、同じ人物が2人並ぶことがある。1人の絵は縦長(portrait)が既定。ネガティブに `multiple views, 2girls`。
- 顔が逆さま: `face right side up, head at top of frame`、ネガティブに `upside-down, inverted face`。ただし頭が腰より下に来る姿勢(ダウンドッグ)を真下から見ると、形の上で顔は逆さまになる。

## ポーズ
- 四つん這い: `all fours`。正面からだと膝が床につきやすい。
- 手足を伸ばした四つん這い: `downward dog, 1.4::straight arms::, 1.5::straight legs::, 1.4::knees off ground::`、ネガティブに `kneeling, knees on ground, on knees`。横から(`from side`)だと効きやすく、正面からだと膝が床に戻りやすい。
- 「頭を上げる」(`head up`)を強めると、膝が床に戻りやすい(伸ばした脚と両立しにくい)。
- 足を開く: `legs apart`、`wide stance`。

## 体型
- `plump thighs` は全身を太らせる(お腹・腰まで)。外すとはっきり細くなる。
- `1.3::narrow waist::` ははっきり効く(重み 1.0 では弱い)。`flat stomach` は弱い。
- ネガティブの `skinny` は逆効果(かえって太る)。細くしたいときはネガティブに `chubby, fat, belly`。
- お腹・腰の言葉は、お腹が見える服(ビキニ・下着・裸)のときしか差が出ない。

## 服
- ビキニ: `1.4::white bikini::, string bikini, side-tie bikini bottom` のように色・形まで書く。
- 服を変えるときは、キャラの普段の服を使わない(use_outfit を false)。
