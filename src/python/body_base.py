"""
キャラシートの「素体」: 服を着せる前の体(モデリングでいう素体)を、キャラの顔・髪・目のまま描いて、部位ごとに見られるようにする。

素体は、キャラとは別に一覧で持つ(肌の色・体格・身長・プロポーション・胸・筋肉の付き方・足すタグ)。キャラシートでは、
どの素体を当てるかを選ぶ。描くときは、キャラの容姿から体型と肌の色のタグを外し、素体のタグに置き換え、服装の代わりに
素体の種類のタグを入れる。

種類は3つ:
- suit(素体スーツ): 体の線が分かる無地のスーツ(上・腰・脚のセパレート。タグは開発管理者の設定)。全年齢の扱いで、どのキャラにも使える。
- swimsuit(水着素体): 無地の水着(上下のセパレート。タグは開発管理者の設定)。素体スーツと同じく全年齢で、どのキャラにも使える
  (腕・脚・肩の形まで見え、漫画の水着の場面にも使える)。
- nude(裸の素体): 乳首は描き、性器は描かない(タグはコンテンツガードの body_base.nude_*)。成人キャラだけに使える(nude_block_reason)。
  腹・骨盤・大腿の寄りの絵は描かない(全身図で確かめる。部位の nude が False のもの)。胸のアップは 3D の乳首の部品のために描く。

部位は解剖学の区分(全身・頭頸部・体幹・上肢・下肢)で並べ、1枚ごとに、人体の構造として確かめる点(check)を添える。
部位はコードの既定(PARTS)に、DB(sheet_items の sheet='body')の上書き(名前・タグ・ネガティブ・サイズ・確かめる点)と、
利用者が足した部位を重ねたもの(parts)。全キャラで共通。足した部位は、裸の素体では描かない(胸・腹の寄りと同じ扱い)。
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from typing import Any

from . import app_settings, content_guard
from .safety_floor import is_minor_age
from .character_sheet import character_minor_tags, join_tags, r18_negative, r18_positive, split_body, split_tags


@dataclass(frozen=True)
class BodyPart:
    key: str
    label: str
    # 足すタグ(構図を含む)
    tags: str
    # whole: 全身 / head: 頭頸部 / trunk: 体幹 / upper_limb: 上肢 / lower_limb: 下肢
    group: str
    # 人体の構造として確かめる点(画面に出す)
    check: str
    size: str = "square"
    # 裸の素体でも描くか(腹・骨盤・大腿の寄りは描かない。胸のアップは描く)
    nude: bool = True
    # この部位だけに足すネガティブ
    negative: str = ""
    # 真後ろから描く部位。選択肢に真後ろのタグ(rear_tags)があれば、そちらを使う
    rear: bool = False
    # 裸の素体だけで描く部位(乳首のアップ。素体スーツ・水着素体では描かない)
    nude_only: bool = False
    # 脚を開くポーズ(可動域のヨガ)。素体のネガティブの「脚を開かない」タグ(ガードの body_base.open_legs_allowed)を、
    # この部位だけ外す
    open_legs: bool = False


GROUPS: tuple[tuple[str, str], ...] = (
    ("whole", "全身"),
    ("head", "頭頸部"),
    ("trunk", "体幹"),
    ("upper_limb", "上肢"),
    ("lower_limb", "下肢"),
    ("rom", "可動域(ヨガ)"),
    ("pregnancy", "妊娠の経過"),
)

_STAND = "full body, standing, arms at sides, expressionless"
# 全身の部位は、はだしにする(2026-10 実機: 背面でハイヒールを履いた)。服を着せる絵(outfit_prompt)は衣装の靴を使うので、
# Aポーズのタグには入れず prompt() で足す
_BARE_FEET = "barefoot"
_BARE_FEET_NEGATIVE = "high heels, shoes, boots, sandals, footwear"
# 真後ろ。頭も向こうを向かせる(振り返って顔を見せないように)。重みなしでは、振り返り・正面・前後の2人並びが
# 出た(2026-10 実機)ので、強調の重みを付ける
_FROM_BEHIND = "solo, 1.4::from behind::, 1.3::facing away::, straight-back, back view, back of head, head facing away"
# 真後ろからは見えない物のタグ(目・乳首・股・臍・肩の前に垂らした髪)。入れると正面に引き戻される
# (2026-10 実機: 裸の素体の nipples・キャラの brown eyes などで、背面が3回とも正面向きになった)ので、真後ろの部位では外す。
# 前髪・額・表情・胸の大きさ・胸の横のタグも外す(2026-10-30 実機: parted bangs・forehead・smile・medium breasts で、
# 振り返った斜め後ろになった)。後ろからのぞく胸(breasts peek)は残す
_REAR_HIDDEN_RE = re.compile(
    r"\beyes\b|\beyelashes\b|\bnipples?\b|featureless crotch|\bnavel\b|hair over (shoulders?|breasts?)"
    r"|bangs\b|\bforehead\b|\bsmil(e|ing)\b|\bgrin\b|\bexpressionless\b|\bbreasts?\b(?! peek)|\bsideboob\b",
    re.IGNORECASE,
)
_BACK_NEGATIVE = (
    "1.3::looking back::, looking at viewer, looking over shoulder, front view, facing viewer, face, profile, "
    "turning head, 1.4::multiple views::, 1.3::turnaround::, two views, front and back, 2girls"
)
# 真横。重みなしでは、正面と横の2人並び(設定画)が出た(2026-10-30 実機: スーツ・水着で3回とも)ので、強調の重みを付け、
# 並びと正面向きをネガティブで抑える
_FROM_SIDE = "solo, 1.3::from side::, 1.2::profile::, side view"
_SIDE_NEGATIVE = (
    "1.4::multiple views::, 1.3::turnaround::, two views, front and side, 2girls, front view, facing viewer, "
    "looking at viewer, from behind"
)
# 手は、こちらへ突き出すと遠近で縮んで指の長さが分からない(2026-10 実機)。顔の横に平らに上げた手にする
_HAND = "hand focus, close-up, open hand, hand up, flat hand, fingers together"
_HAND_NEGATIVE = "foreshortening, reaching towards viewer, outstretched hand, extra fingers, fused fingers, multiple views"
PARTS: tuple[BodyPart, ...] = (
    BodyPart("front", "正面(解剖学的正位)", f"{_STAND}, front view, facing viewer, palms facing forward, feet together", "whole",
             "成人で7〜7.5頭身。肩幅は頭幅の約2倍(女性は1.5〜2倍)。腕を下ろすと指先は大腿の中ほど、手首は大転子(股関節)の高さ。"
             "臍は全身のほぼ中ほどより少し上", "portrait", negative=_BARE_FEET_NEGATIVE),
    BodyPart("side", "側面(矢状面)", f"{_STAND}, {_FROM_SIDE}", "whole",
             "耳・肩・大転子・膝・外くるぶしがほぼ一直線(重心線)。脊柱は頸と腰で前に、胸で後ろに弯曲(S字)。"
             "胸郭は前後に厚く、骨盤は前に傾く", "portrait", negative=f"{_SIDE_NEGATIVE}, {_BARE_FEET_NEGATIVE}"),
    # from behind だけだと振り返って顔を見せやすい(2026-10 実機)。頭も真後ろを向かせ、振り返りをネガティブで抑える
    BodyPart("back", "背面(真後ろ)", f"{_STAND}, {_FROM_BEHIND}, back, shoulder blades, ass", "whole",
             "後頭部は頭の高さの下1/3で首へ細くなり、外後頭隆起の下に項のくぼみ。肩甲骨は第2〜7肋骨の高さ、内側の縁は脊柱から"
             "指3本分。腰のくぼみ(ヴィーナスのえくぼ)は上後腸骨棘の位置",
             "portrait", negative=f"{_BACK_NEGATIVE}, {_BARE_FEET_NEGATIVE}", rear=True),
    BodyPart("three_quarter", "斜め", f"{_STAND}, three-quarter view", "whole",
             "胸郭と骨盤が同じ向きにねじれずに並ぶか。奥の肩・腰は手前より細く見える", "portrait",
             negative=_BARE_FEET_NEGATIVE),
    BodyPart("a_pose", "Aポーズ(関節の確認)",
             "full body, standing, front view, facing viewer, a-pose, arms slightly away from body, legs slightly apart, expressionless",
             "whole",
             "肩関節は鎖骨の外端の下。肘は臍の高さ、手首は股の高さ。膝蓋骨は脚の長さのほぼ中ほど。"
             "腕を開いても鎖骨・三角筋の形が崩れないか", "portrait", negative=_BARE_FEET_NEGATIVE),
    BodyPart("head_front", "頭部(正面)", "portrait, close-up, front view, facing viewer, looking at viewer, expressionless", "head",
             "目は頭の高さのほぼ中ほど。目と目の間は目1つ分。耳は眉から鼻の下までの高さ"),
    BodyPart("head_side", "頭部(側面)", f"portrait, close-up, {_FROM_SIDE}, expressionless", "head",
             "耳は頭の前後の中ほどより後ろ。下顎角から耳の下へ顎のラインがつながる。後頭部が平らになっていないか",
             negative=_SIDE_NEGATIVE),
    BodyPart("head_back", "頭部(後面・後頭部)", f"upper body, close-up, {_FROM_BEHIND}, nape, hair", "head",
             "後頭部は丸く、頭頂から外後頭隆起へ張り出す(絶壁になっていないか)。耳は後ろから見ると頭の幅から少し出る。"
             "首の付け根(項)は頭の幅より細く、僧帽筋で肩へ広がる。髪の分け目・つむじの位置",
             negative=_BACK_NEGATIVE, rear=True),
    BodyPart("neck_shoulder", "頸部・肩(鎖骨)", "upper body, front view, facing viewer, collarbone, neck, shoulders, expressionless",
             "head",
             "胸鎖乳突筋が耳の後ろから鎖骨の内端へ斜めに走る。鎖骨はほぼ水平でゆるいS字。首の太さは顔の幅の半分強", nude=False),
    # 裸の素体でも描く(3Dモデルの乳首の部品の形を見るため。ユーザーの依頼 2026-11-08)
    BodyPart("chest", "胸郭", "upper body, front view, facing viewer, chest, ribcage, arms at sides, expressionless", "trunk",
             "胸郭は卵形で、下へ広がる。胸骨の下端(みぞおち)は肘の高さより少し上"),
    # 乳首のアップ(3Dモデルの乳首の部品を作るため、いろいろな向きから。裸の素体だけ。ユーザーの依頼 2026-11-08)
    BodyPart("nipple_front", "乳首(正面)", "close-up, breast close-up, nipple, areola, front view, facing viewer, chest focus, "
             "head out of frame", "trunk",
             "乳輪は円形で、乳首はその中心。乳房の頂点よりわずかに外下を向く。左右の乳首の間隔は胸の幅の約半分",
             nude_only=True),
    BodyPart("nipple_side", "乳首(側面)", "close-up, breast close-up, nipple, areola, from side, side view, profile, "
             "chest focus, head out of frame", "trunk",
             "横から見ると乳首は乳輪から前へ突き出す(陥没なら乳輪の面より奥)。乳輪はわずかに盛り上がる", nude_only=True),
    BodyPart("nipple_three_quarter", "乳首(斜め)", "close-up, breast close-up, nipple, areola, three-quarter view, "
             "chest focus, head out of frame", "trunk",
             "斜めから見ると乳輪は楕円に見え、乳首の高さと乳輪の盛り上がりが分かる", nude_only=True),
    BodyPart("nipple_below", "乳首(下から)", "close-up, breast close-up, nipple, areola, from below, underboob, "
             "chest focus, head out of frame", "trunk",
             "下から見ると乳房の下縁(乳房下溝)の丸みと、乳首の向き(わずかに上・外)が分かる", nude_only=True),
    BodyPart("abdomen", "腹部", "midriff, navel, abdomen, cropped torso, front view", "trunk",
             "臍は腸骨稜(骨盤の上縁)のやや上。腹直筋は臍の上下に分かれて見える。脇腹は外腹斜筋のふくらみ", nude=False),
    BodyPart("upper_back", "背部(肩甲骨・脊柱)", f"upper body, {_FROM_BEHIND}, back, shoulder blades, spine", "trunk",
             "脊柱は真ん中のくぼみとして通る。肩甲骨は腕の動きでずれる三角の板。僧帽筋が首から肩へなだらかに下がる",
             negative=_BACK_NEGATIVE, rear=True),
    BodyPart("pelvis", "骨盤・腰", "cowboy shot, front view, facing viewer, hips, waist, arms at sides, expressionless", "trunk",
             "女性は骨盤が広く、腰のくびれは臍の高さ。男性は肩幅に対して骨盤が狭い。股の高さは身長のほぼ半分", nude=False),
    BodyPart("arm", "上腕・肘", "arm focus, upper arm, elbow, arm extended to side, close-up", "upper_limb",
             "上腕の長さは肩から肘まで頭1つ半ほど。肘は外側へ少し反る(運搬角、女性で大きい)。上腕二頭筋は前、三頭筋は後ろ"),
    BodyPart("forearm", "前腕・手首", "forearm focus, wrist, close-up", "upper_limb",
             "前腕は肘側が太く手首側で細い。手首の幅は前腕の中ほどの約2/3。尺骨の頭が手首の小指側に出る"),
    BodyPart("hand_palm", "手(手掌)", f"solo, {_HAND}, palm, palm facing viewer, five fingers", "upper_limb",
             "指は5本。中指が最長で、手の長さは顔の顎から生え際の手前ほど。母指球・小指球のふくらみ。指の関節は3つ(親指は2つ)",
             negative=_HAND_NEGATIVE),
    BodyPart("hand_back", "手(手背)", f"solo, {_HAND}, back of hand, five fingers, fingernails", "upper_limb",
             "指の付け根の関節(MP関節)は弧を描いて並ぶ。爪は指先の背側だけ。指の長さは中指>薬指≒人差し指>小指",
             negative=_HAND_NEGATIVE),
    BodyPart("thigh_knee", "大腿・膝", "lower body, knees, thighs, front view, standing", "lower_limb",
             "大腿は股関節から膝へ内側に寄る(Q角、女性で大きい)。膝蓋骨は膝の前のやや上", nude=False),
    BodyPart("calf", "下腿(すね・ふくらはぎ)", "lower legs focus, calves, shins, close-up, standing", "lower_limb",
             "すね(脛骨)はまっすぐで皮膚の下に近い。ふくらはぎ(腓腹筋)は内側が低く大きい。内くるぶしは外くるぶしより高い"),
    # 足の裏をこちらへ突き出す構図(soles, foot focus)は、形が縮んで分からず、性的な構図にもなりやすい。床に立った足を横から描く
    BodyPart("foot", "足(内側・足背)",
             "solo, barefoot, feet only, lower legs only, head out of frame, standing on floor, from side, ankles, toes, close-up",
             "lower_limb",
             "足の長さは前腕とほぼ同じ。内側から見ると土踏まず(内側縦アーチ)が浮き、踵・母指球・小指側の縁で接地する。"
             "内くるぶしは外より高く前。親指が最も太く、指先は弧を描く",
             negative="soles, feet up, foreshortening, sitting, lying, presenting foot, bent over, bending forward, full body, face"),
)

# 可動域(ヨガ): 3Dモデルの関節の動く範囲を見るため、股関節を大きく動かすポーズを全身で描く(ユーザーの依頼 2026-11-09)。
# 股関節まわり(腸骨稜・鼠径部のしわ・大転子・殿溝・内転筋)を詳しく描く。股の扱いは素体の種類のまま
# (裸の素体は featureless crotch。ガードの body_base.nude_tags)。前後開脚・鳩・ローランジは、2026-11 の試しでは
# 座りや膝立ちになりやすかった(ユーザーの判断で、そのまま残す)
_HIP = ("1.2::hip joint::, hip bones, iliac crest, inguinal crease, 1.1::groin crease::, greater trochanter, inner thighs, "
        "adductor muscles, gluteal fold, detailed anatomy, anatomical accuracy")
_ROM_FACE = "expressionless, closed mouth"
# 股・肛門・修正の打ち消しは、ガードの body_base.rom_negative(part_negative でまとまりごとに足す)。
# 脚を開かせないタグ(裸の素体のガード body_base.nude_negative の spread legs)は、open_legs の部位では外す
# (外すタグはガードの body_base.open_legs_allowed)
ROM_PARTS: tuple[BodyPart, ...] = tuple(
    BodyPart(key, label, f"full body, {pose}, {_HIP}, {_ROM_FACE}", "rom", check,
             negative=join_tags(_BARE_FEET_NEGATIVE, negative), open_legs=True)
    for key, label, pose, check, negative in (
        ("yoga_front_split", "前後開脚(屈曲・伸展)",
         "1.4::front split::, hanumanasana, splits, front leg extended forward, back leg extended backward, both legs straight, "
         "groin near floor, hips square, hands on floor, from side, side view",
         "前脚の股関節は屈曲、後ろ脚は伸展。骨盤は正面を向き、鼠径部のしわは前脚側で深く、後ろ脚側で伸びる", ""),
        ("yoga_middle_split", "左右開脚(外転)",
         "1.4::middle split::, straddle split, flexible, sitting on floor, legs spread wide sideways, legs straight, toes up, "
         "hands on floor in front, front view, facing viewer",
         "大腿骨は寛骨臼を中心に横へ回る。内転筋が伸び、鼠径部のしわは左右へ斜めに開く", ""),
        ("yoga_butterfly", "合蹠(外旋・外転)",
         "butterfly pose, bound angle pose, sitting on floor, soles of feet together, knees out to sides, hands holding feet, "
         "back straight, front view, facing viewer",
         "股関節の外旋。膝は床へ下がり、大腿の内側が上を向く。大転子は後ろへ回る", ""),
        ("yoga_garland", "深いスクワット(深い屈曲)",
         "garland pose, malasana, deep squat, heels on floor, knees apart, palms together at chest, elbows pressing knees, "
         "front view, facing viewer",
         "股関節の深い屈曲と外転。腹と大腿が近づき、鼠径部は深く折れる", ""),
        ("yoga_pigeon", "鳩のポーズ(外旋+伸展)",
         "1.4::pigeon pose::, yoga, one leg bent in front on floor with shin across, other leg straight back, "
         "upper body upright, hands on floor, three-quarter view",
         "前脚は外旋、後ろ脚は伸展。後ろ脚の鼠径部は伸び、前脚の殿部は床に沈む", ""),
        ("yoga_low_lunge", "ローランジ(伸展)",
         "1.4::low lunge::, anjaneyasana, yoga, kneeling on one knee, front foot flat on floor, front knee bent at right angle, "
         "back leg extended behind, back knee and top of foot on floor, hips pushed forward, arms raised, from side, side view",
         "後ろ脚の股関節の伸展。腸腰筋の線(上前腸骨棘から大腿前面)が伸びる", ""),
        ("yoga_forward_bend", "立位前屈(屈曲)",
         "1.4::standing forward bend::, uttanasana, bent over, torso against legs, legs straight, legs together, "
         "palms flat on floor, head down, from side, side view",
         "骨盤ごと前へ倒れる(腰だけ曲げない)。坐骨が上を向き、殿溝が伸びる", ""),
        ("yoga_bridge", "ブリッジ(伸展)",
         "1.4::glute bridge::, bridge pose, lying on back, shoulders and head on floor, knees bent at right angle, "
         "feet flat on floor, hips lifted, straight line from knees to shoulders, arms on floor, from side, side view",
         "股関節の伸展。膝・腰・肩が一直線、大殿筋が締まり、鼠径部が平らに伸びる",
         "backbend, wheel pose, arched back, plow pose"),
    )
)

# 妊娠の経過: 3か月・5か月・7か月・臨月を、全身の正面・側面・背面・斜めで描く(ユーザーの依頼 2026-11-10)。
# 裸の素体・素体スーツ・水着素体のどれでも描ける。お腹の大きさは段階ごとに重みで抑える(2026-11 の試し: 重みなしでは
# 5か月がもう臨月に近かった)。正面・側面・斜めは全身を強める(裸の素体で、腰の上で切れた)。背面は全身を強めると、
# お腹を背中側に描いた(ゆら・臨月)ので、全身の背面のまま(ユーザーの判断で、最初の絵に戻した)
_PREG_FULL = "1.3::full body::, head to toe, feet visible"
_PREG_FULL_NEGATIVE = "close-up, cropped legs, feet out of frame, lower body cropped"
# 股・修正の打ち消しはガードの body_base.pregnancy_negative、成人キャラだけにするのはガードの
# body_base.adult_only_groups、お腹とぶつかる体型のタグは設定の body_base.pregnant_drop_tags(どれも画面で編集する)
_PREG_STAGES = (
    ("3", "3か月", "1.1::slight belly bulge::, small belly, lower belly slightly rounded",
     "big belly, huge belly, pregnant belly", "外見はほぼ変わらない。下腹(恥骨の上)がわずかに丸くなる"),
    ("5", "5か月", "0.8::pregnant::, small round belly, slight belly bulge",
     "big belly, huge belly, very large belly", "下腹がはっきりふくらむ。子宮底は臍の少し下"),
    ("7", "7か月", "pregnant, round belly, 0.9::big belly::",
     "flat stomach, huge belly, very large belly", "臍の上までふくらむ。臍が浅くなり、腰が少し反る"),
    ("9", "臨月", "1.1::pregnant::, big belly, 1.1::huge belly::, belly protruding",
     "flat stomach, small belly", "子宮底はみぞおちの近く。お腹が前へ突き出し、反り腰で重心が後ろへ寄る"),
)
_PREG_VIEWS = ("front", "side", "back", "three_quarter")


def _pregnancy_part(stage: tuple[str, str, str, str, str], view: BodyPart) -> BodyPart:
    key, label, tags, negative, check = stage
    full = view.key != "back"
    return dataclasses.replace(
        view, key=f"preg{key}_{view.key}", label=f"妊娠{label}・{view.label}", group="pregnancy", check=check,
        tags=join_tags(_PREG_FULL if full else "", view.tags, tags),
        negative=join_tags(view.negative, negative, _PREG_FULL_NEGATIVE if full else ""),
    )


PREGNANCY_PARTS: tuple[BodyPart, ...] = tuple(
    _pregnancy_part(stage, next(p for p in PARTS if p.key == view)) for stage in _PREG_STAGES for view in _PREG_VIEWS
)
PARTS = PARTS + ROM_PARTS + PREGNANCY_PARTS

ITEMS: dict[str, BodyPart] = {part.key: part for part in PARTS}

# 素体の背景(絵ごとの違いを部位だけにするため、無地にする)
BACKGROUND = "simple background, grey background"
SIZES = {"square": (1024, 1024), "portrait": (832, 1216)}
# 1枚に同じキャラが何人も並ぶ設定画になりやすいので抑える(face_sheet.NEGATIVE と同じ理由)
NEGATIVE = "multiple views, character sheet, reference sheet, turnaround, split screen, multiple girls, multiple boys"

KINDS = {"suit": "素体スーツ(全年齢)", "swimsuit": "水着素体(全年齢)", "nude": "裸の素体(成人キャラだけ)"}
# 全年齢の種類の服のタグ(開発管理者の設定のキー: ふだん, 控えめに描くキャラ)
CLOTHES_SETTINGS = {
    "suit": ("body_base.suit_tags", "body_base.modest_suit_tags"),
    "swimsuit": ("body_base.swimsuit_tags", "body_base.modest_swimsuit_tags"),
}
# 素体スーツのタグは開発管理者の設定(app_settings の body_base.suit_tags。未成年の手がかりがあるキャラは
# body_base.modest_suit_tags: 体に貼り付けない服。体型(胸の設定も)はキャラのまま。寄り・構図での強調はネガティブで抑える)
# 守りにかかわるタグ(裸の素体のタグ・ネガティブ、未成年を示すタグ)は、コンテンツガード(DB)の body_base.* にある

# 素体の設定の項目(プルダウン)と画面の名前。選択肢(値・名前・タグ)は DB(body_base_options)にあり、素体の設定のページで編集する
FIELD_LABELS: dict[str, str] = {
    "sex": "性別",
    "skin_tone": "肌の色",
    "build": "体格",
    "height": "身長",
    "proportion": "プロポーション",
    "chest": "胸",
    "muscle": "筋肉の付き方",
}
FIELDS = tuple(FIELD_LABELS)
# 選択肢の表: 項目 → 値 → 選択肢(label・tags・rear_tags)
Options = dict[str, dict[str, dict[str, Any]]]

# キャラの容姿から外す肌の色のタグ(素体で肌の色を決めたときだけ)
_SKIN_COLOR_RE = re.compile(
    r"\b(pale|fair|light|tan|tanned|olive|brown|dark|white|black)[ _-]?(skin|skinned)\b|^tan$|^tanned$|dark-skinned",
    re.IGNORECASE,
)


def options_map(rows: list[dict[str, Any]]) -> Options:
    """DB の選択肢の行(db.list_body_base_options)を、項目 → 値 → 選択肢 の表に。"""
    table: Options = {field: {} for field in FIELDS}
    for row in rows:
        if row["field"] in table:
            table[row["field"]][row["value"]] = row
    return table


def option_problems(text: str) -> list[str]:
    """選択肢のタグ・足すタグに入れられないタグ(性的な場面のタグと、未成年を示すタグ)。"""
    return sorted(set(clean_extra(text)) | set(minor_words(text)))


def parts(rows: list[dict[str, Any]] | None = None) -> dict[str, BodyPart]:
    """
    使う部位: コードの既定に、DB の上書き・足した部位(db.list_sheet_items(conn, "body") の行)を重ねる。
    上書きでは、裸の素体で描くか・真後ろの部位かは既定のまま。足した部位は裸の素体では描かない。
    """
    result = dict(ITEMS)
    for row in rows or []:
        fields = {
            "label": row["label"], "tags": row["tags"], "group": row["grp"], "check": row["check_text"],
            "size": row["size"], "negative": row["negative"],
        }
        builtin = ITEMS.get(row["key"])
        result[row["key"]] = dataclasses.replace(builtin, **fields) if builtin else BodyPart(row["key"], nude=False, **fields)
    return result


def presets(rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """画面の部位の一覧。builtin はコードの部位(消すと既定に戻る)、edited は DB で上書き・足したもの。"""
    edited = {row["key"] for row in rows or []}
    return [
        {
            "key": p.key, "label": p.label, "group": p.group, "tags": p.tags, "check": p.check, "nude": p.nude,
            "negative": p.negative, "size": p.size, "builtin": p.key in ITEMS, "edited": p.key in edited,
        }
        for p in parts(rows).values()
    ]


def _modest_skips(field: str, value: str) -> bool:
    """控えめに描くとき、その設定を使わないか(コンテンツガードの body_base.modest_skip: 項目 か 項目:値)。"""
    skips = content_guard.words("body_base.modest_skip")
    return field in skips or f"{field}:{value}" in skips


def body_tags(base: dict[str, Any], options: Options, *, modest: bool = False, rear: bool = False) -> str:
    """
    素体の体のタグ(性別・肌の色・体格・身長・プロポーション・胸・筋肉+足すタグ)。選択肢に無い値は何も入れない。
    modest(未成年の手がかりがあるキャラ)は body_base.modest_skip の設定を外す。rear(真後ろの部位)は、選択肢に
    真後ろのタグ(rear_tags)があればそれを使う(胸の large breasts は、背面を正面・振り返りに引き戻すため)。
    """
    tags = []
    for field in FIELDS:
        value = base.get(field) or ""
        if modest and _modest_skips(field, value):
            continue
        option = options.get(field, {}).get(value)
        if option is None:
            continue
        tags.append(option["rear_tags"] if rear and option.get("rear_tags") is not None else option["tags"])
    return join_tags(*tags, base.get("extra_tags"))


_WEIGHT_RE = re.compile(r"^-?\d+(\.\d+)?::|::$")


def _plain_weight(tag: str) -> str:
    return _WEIGHT_RE.sub("", tag.strip()).strip().lower()


def clean_extra(text: str, *, nude: bool = False) -> list[str]:
    """
    素体の「足すタグ」に入れられないタグ(性的な場面のタグ)。素体は体の形を見るためのもので、性的な絵ではない。
    裸は種類(nude)で決めるので、足すタグには書かせない。ただし裸の素体では、乳首・乳輪の形と妊娠のタグ
    (ガードの body_base.nude_detail_tags。完全一致)は入れてよい(3Dモデルの乳首の部品を形ごとに作るため)。
    """
    if nude:
        allowed = {t.lower() for t in content_guard.words("body_base.nude_detail_tags")}
        # 強調・抑制の書き方(1.5::puffy nipples::)の外側は見ずに比べる
        text = ", ".join(t for t in split_tags(text) if _plain_weight(t) not in allowed)
    return content_guard.find("scene.sexual_tags", text or "")


def face_tags(character: dict[str, Any], base: dict[str, Any]) -> str:
    """キャラの容姿から、体型(素体で置き換える)と、素体で決めるなら肌の色を外したもの(顔・髪・目)。"""
    face, _ = split_body(character.get("appearance_tags") or "")
    if base.get("skin_tone"):
        face = ", ".join(t for t in split_tags(face) if not _SKIN_COLOR_RE.search(t.strip()))
    return face


def clothes_tags(base: dict[str, Any], *, modest: bool = False) -> str:
    """全年齢の種類(素体スーツ・水着素体)の服のタグ(開発管理者の設定)。"""
    normal, quiet = CLOTHES_SETTINGS.get(base.get("kind") or "suit", CLOTHES_SETTINGS["suit"])
    return app_settings.get(quiet if modest else normal)


def prompt(character: dict[str, Any], base: dict[str, Any], part: BodyPart, options: Options, *, modest: bool = False) -> str:
    """
    1枚のプロンプト。modest(未成年の手がかりがあるキャラ)は、体に貼り付けないスーツにする(コンテンツガードの
    body_base.modest_skip にある設定は外す。既定では外さず、少年誌の一般的なキャラと同じく体型はキャラのまま描く)
    (裸の素体はそもそも当てられない)。真後ろの部位は、後ろから見えない物のタグを外す(_REAR_HIDDEN_RE)。
    """
    nude = base.get("kind") == "nude"
    adult = (content_guard.text("body_base.male_adult_tags") if base.get("sex") == "male" else r18_positive()) if nude else ""
    tags = join_tags(
        face_tags(character, base),
        body_tags(base, options, modest=modest, rear=part.rear),
        adult,
        content_guard.text("body_base.nude_tags") if nude else clothes_tags(base, modest=modest),
        part.tags,
        _BARE_FEET if part.group in ("whole", "rom", "pregnancy") else "",
        BACKGROUND,
        character.get("style_tags"),
    )
    if part.rear:
        tags = ", ".join(t for t in split_tags(tags) if not _REAR_HIDDEN_RE.search(t))
    if part.group == "pregnancy":
        drop = {_plain_weight(t) for t in split_tags(str(app_settings.get("body_base.pregnant_drop_tags")))}
        tags = ", ".join(t for t in split_tags(tags) if _plain_weight(t) not in drop)
    return tags


# 髪の層の下を描き直すとき(2Dモデル): 長い髪をまた描かないよう、髪の長さのタグを外し、ネガティブで抑える
HAIR_LENGTH_RE = re.compile(r"\b(long|very long|absurdly long|medium)\s+hair\b|hair over (shoulder|breasts)|hair between", re.IGNORECASE)
# 髪は頭から続いて描かれやすい(2026-10 実機: ネガティブだけでは長い髪をまた描いた)ので、短い髪のタグを足し、重みで強く抑える
BEHIND_HAIR_TAGS = "short hair, hair above shoulders, nape, bare shoulders line"
BEHIND_HAIR_NEGATIVE = (
    "1.6::long hair::, 1.4::very long hair::, 1.3::hair over shoulder::, hair strand, loose hair, hair down, hair on body"
)

# 服を着せるとき(素体の Aポーズの絵の首から下を描き直す)、素体スーツ・水着に戻らないように抑える
OUTFIT_NEGATIVE = (
    "plain grey turtleneck shirt, grey bike shorts, grey pantyhose, plain grey bodysuit, bodysuit, swimsuit, bikini, nude"
)


def outfit_prompt(character: dict[str, Any], base: dict[str, Any], outfit_tags: str, options: Options, *, modest: bool = False) -> str:
    """素体の Aポーズの絵に服を着せるときのプロンプト(素体スーツ・裸のタグの代わりに服のタグ。姿勢は Aポーズのまま)。"""
    return join_tags(
        face_tags(character, base),
        body_tags(base, options, modest=modest),
        outfit_tags,
        ITEMS["a_pose"].tags,
        BACKGROUND,
        character.get("style_tags"),
    )


def part_negative(negative_prompt: str, part: BodyPart) -> str:
    """
    1枚のネガティブ: 素体全体のネガティブに部位のネガティブを足す。脚を開くポーズ(open_legs)では、
    素体のネガティブの「脚を開かない」タグ(ガードの body_base.open_legs_allowed)を外す。まとまりごとの
    打ち消し(ガードの body_base.<まとまり>_negative。可動域・妊娠の経過)も足す。
    """
    tags = split_tags(negative_prompt)
    if part.open_legs:
        allowed = {_plain_weight(t) for t in content_guard.words("body_base.open_legs_allowed")}
        tags = [t for t in tags if _plain_weight(t) not in allowed]
    group = _GROUP_NEGATIVE.get(part.group)
    return join_tags(", ".join(tags), part.negative, content_guard.text(group) if group else "")


# まとまりごとに足す打ち消し(ガードの項目)。部位の画面で部位のネガティブを書き換えても外れない
_GROUP_NEGATIVE = {"rom": "body_base.rom_negative", "pregnancy": "body_base.pregnancy_negative"}


def adult_only(part: BodyPart) -> bool:
    """成人キャラだけで描く部位か(まとまりがガードの body_base.adult_only_groups にある。素体の種類によらない)。"""
    return part.group in content_guard.words("body_base.adult_only_groups")


def negative(base: dict[str, Any], part: BodyPart | None = None, *, modest: bool = False) -> str:
    """素体の種類・部位ごとに足すネガティブ(キャラシートのネガティブ・年齢区分の安全タグは別に入る)。"""
    extra = part.negative if part else ""
    if base.get("kind") == "nude":
        return join_tags(NEGATIVE, content_guard.text("body_base.nude_negative"), r18_negative(), extra, base.get("negative_tags"))
    return join_tags(
        NEGATIVE,
        content_guard.text("body_base.suit_negative"),
        content_guard.text("body_base.modest_negative") if modest else "",
        extra,
        base.get("negative_tags"),
    )


_SHEET_TEXT_FIELDS = ("name", "trigger_word", "appearance_tags", "body_tags", "outfit_tags", "style_tags")


def minor_words(text: str, *, adult: bool = False) -> list[str]:
    """
    未成年を示す語(コンテンツガードの body_base.minor_tags)。キャラ別データセットの dataset.minor_tags とは別に持つ
    (あちらを空にしても、素体はこちらで判定する)。adult(年齢を入れた成人キャラ)か、組み合わせで成人を表していれば、
    成人の衣装(制服 など)は除く。
    """
    found = content_guard.find("body_base.minor_tags", text or "")
    if adult or content_guard.adult_marked(text or ""):
        found = content_guard.without_costumes(found)
    return found


def nude_block_reason(character: dict[str, Any]) -> str | None:
    """
    裸の素体を当てられない理由(当てられれば None)。成人フラグがあり、年齢が 18 歳以上と入れてあり、未成年の定義・
    未成年を示すタグ(コンテンツガードの dataset.minor_tags と body_base.minor_tags)が無いキャラにだけ当てる。見るのは
    そのキャラのキャラシートだけ。成長段階の紐付け(growth_of)やシードは、ほかの段階と共有していても見ない。
    """
    if not character.get("is_adult"):
        return "裸の素体は、成人キャラ(キャラシートの成人フラグ)にだけ当てられます。"
    if character.get("is_minor") or is_minor_age(character.get("age")):
        return "未成年と定義されたキャラには、裸の素体を当てられません。"
    if character.get("age") is None:
        return "裸の素体は、キャラシートに年齢(18 歳以上)を入れたキャラにだけ当てられます。"
    found = _sheet_minor_tags(character)
    if found:
        return f"キャラシートに未成年を示すタグがあるため、裸の素体を当てられません: {', '.join(found)}"
    return None


def _is_minor(character: dict[str, Any]) -> bool:
    return bool(character.get("is_minor")) or is_minor_age(character.get("age"))


def _sheet_minor_tags(character: dict[str, Any]) -> list[str]:
    text = ", ".join(str(character.get(k) or "") for k in _SHEET_TEXT_FIELDS)
    return sorted(set(character_minor_tags(character)) | set(minor_words(text, adult=content_guard.is_defined_adult(character))))


def minor_coded(character: dict[str, Any]) -> bool:
    """
    未成年の手がかりがあるキャラか(未成年の定義・年齢・未成年を示すタグ)。成人フラグの有無は見ない(高校生のキャラに
    成人フラグが付いていることがある)。素体スーツ・水着素体を控えめに描くのに使う。
    """
    return _is_minor(character) or bool(_sheet_minor_tags(character))


def file_dir_name(base: dict[str, Any]) -> str:
    """描いた絵を置くデータセットのフォルダの名前(裸の素体は R18 のフォルダに分ける)。"""
    return "body_base_r18" if base.get("kind") == "nude" else "body_base"
