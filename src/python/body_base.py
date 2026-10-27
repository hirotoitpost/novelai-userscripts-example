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
  胸・腹・骨盤・大腿の寄りの絵は描かない(全身図で確かめる。部位の nude が False のもの)。

部位は解剖学の区分(全身・頭頸部・体幹・上肢・下肢)で並べ、1枚ごとに、人体の構造として確かめる点(check)を添える。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import app_settings, content_guard
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
    # 裸の素体でも描くか(胸・腹・骨盤・大腿の寄りは描かない)
    nude: bool = True
    # この部位だけに足すネガティブ
    negative: str = ""
    # 真後ろから描く部位。選択肢に真後ろのタグ(rear_tags)があれば、そちらを使う
    rear: bool = False


GROUPS: tuple[tuple[str, str], ...] = (
    ("whole", "全身"),
    ("head", "頭頸部"),
    ("trunk", "体幹"),
    ("upper_limb", "上肢"),
    ("lower_limb", "下肢"),
)

_STAND = "full body, standing, arms at sides, expressionless"
# 真後ろ。頭も向こうを向かせる(振り返って顔を見せないように)。重みなしでは、振り返り・正面・前後の2人並びが
# 出た(2026-10 実機)ので、強調の重みを付ける
_FROM_BEHIND = "solo, 1.4::from behind::, 1.3::facing away::, straight-back, back view, back of head, head facing away"
_BACK_NEGATIVE = (
    "1.3::looking back::, looking at viewer, looking over shoulder, front view, facing viewer, face, profile, "
    "turning head, 1.4::multiple views::, 1.3::turnaround::, two views, front and back, 2girls"
)
# 手は、こちらへ突き出すと遠近で縮んで指の長さが分からない(2026-10 実機)。顔の横に平らに上げた手にする
_HAND = "hand focus, close-up, open hand, hand up, flat hand, fingers together"
_HAND_NEGATIVE = "foreshortening, reaching towards viewer, outstretched hand, extra fingers, fused fingers, multiple views"
PARTS: tuple[BodyPart, ...] = (
    BodyPart("front", "正面(解剖学的正位)", f"{_STAND}, front view, facing viewer, palms facing forward, feet together", "whole",
             "成人で7〜7.5頭身。肩幅は頭幅の約2倍(女性は1.5〜2倍)。腕を下ろすと指先は大腿の中ほど、手首は大転子(股関節)の高さ。"
             "臍は全身のほぼ中ほどより少し上", "portrait"),
    BodyPart("side", "側面(矢状面)", f"{_STAND}, from side, profile", "whole",
             "耳・肩・大転子・膝・外くるぶしがほぼ一直線(重心線)。脊柱は頸と腰で前に、胸で後ろに弯曲(S字)。"
             "胸郭は前後に厚く、骨盤は前に傾く", "portrait"),
    # from behind だけだと振り返って顔を見せやすい(2026-10 実機)。頭も真後ろを向かせ、振り返りをネガティブで抑える
    BodyPart("back", "背面(真後ろ)", f"{_STAND}, {_FROM_BEHIND}", "whole",
             "後頭部は頭の高さの下1/3で首へ細くなり、外後頭隆起の下に項のくぼみ。肩甲骨は第2〜7肋骨の高さ、内側の縁は脊柱から"
             "指3本分。腰のくぼみ(ヴィーナスのえくぼ)は上後腸骨棘の位置",
             "portrait", negative=_BACK_NEGATIVE, rear=True),
    BodyPart("three_quarter", "斜め", f"{_STAND}, three-quarter view", "whole",
             "胸郭と骨盤が同じ向きにねじれずに並ぶか。奥の肩・腰は手前より細く見える", "portrait"),
    BodyPart("a_pose", "Aポーズ(関節の確認)",
             "full body, standing, front view, facing viewer, a-pose, arms slightly away from body, legs slightly apart, expressionless",
             "whole",
             "肩関節は鎖骨の外端の下。肘は臍の高さ、手首は股の高さ。膝蓋骨は脚の長さのほぼ中ほど。"
             "腕を開いても鎖骨・三角筋の形が崩れないか", "portrait"),
    BodyPart("head_front", "頭部(正面)", "portrait, close-up, front view, facing viewer, looking at viewer, expressionless", "head",
             "目は頭の高さのほぼ中ほど。目と目の間は目1つ分。耳は眉から鼻の下までの高さ"),
    BodyPart("head_side", "頭部(側面)", "portrait, close-up, from side, profile, expressionless", "head",
             "耳は頭の前後の中ほどより後ろ。下顎角から耳の下へ顎のラインがつながる。後頭部が平らになっていないか"),
    BodyPart("head_back", "頭部(後面・後頭部)", f"upper body, close-up, {_FROM_BEHIND}, nape, hair", "head",
             "後頭部は丸く、頭頂から外後頭隆起へ張り出す(絶壁になっていないか)。耳は後ろから見ると頭の幅から少し出る。"
             "首の付け根(項)は頭の幅より細く、僧帽筋で肩へ広がる。髪の分け目・つむじの位置",
             negative=_BACK_NEGATIVE, rear=True),
    BodyPart("neck_shoulder", "頸部・肩(鎖骨)", "upper body, front view, facing viewer, collarbone, neck, shoulders, expressionless",
             "head",
             "胸鎖乳突筋が耳の後ろから鎖骨の内端へ斜めに走る。鎖骨はほぼ水平でゆるいS字。首の太さは顔の幅の半分強", nude=False),
    BodyPart("chest", "胸郭", "upper body, front view, facing viewer, chest, ribcage, arms at sides, expressionless", "trunk",
             "胸郭は卵形で、下へ広がる。胸骨の下端(みぞおち)は肘の高さより少し上", nude=False),
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


def presets() -> list[dict[str, Any]]:
    return [
        {"key": p.key, "label": p.label, "group": p.group, "tags": p.tags, "check": p.check, "nude": p.nude}
        for p in PARTS
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


def clean_extra(text: str) -> list[str]:
    """
    素体の「足すタグ」に入れられないタグ(性的な場面のタグ)。素体は体の形を見るためのもので、性的な絵ではない。
    裸は種類(nude)で決めるので、足すタグには書かせない。
    """
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
    (裸の素体はそもそも当てられない)。
    """
    nude = base.get("kind") == "nude"
    adult = (content_guard.text("body_base.male_adult_tags") if base.get("sex") == "male" else r18_positive()) if nude else ""
    return join_tags(
        face_tags(character, base),
        body_tags(base, options, modest=modest, rear=part.rear),
        adult,
        content_guard.text("body_base.nude_tags") if nude else clothes_tags(base, modest=modest),
        part.tags,
        BACKGROUND,
        character.get("style_tags"),
    )


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


def nude_block_reason(character: dict[str, Any], family: list[dict[str, Any]]) -> str | None:
    """
    裸の素体を当てられない理由(当てられれば None)。成人フラグがあり、年齢が 18 歳以上と入れてあり、未成年の定義・
    未成年を示すタグ(コンテンツガードの dataset.minor_tags と body_base.minor_tags)が無いことに加え、同じ人物の成長段階
    (family: 元のキャラと、その段階のキャラ全部)に未成年の段階があれば当てない(未成年の段階と同じ顔・同じシードで描かれる
    人物のため)。
    """
    if not character.get("is_adult"):
        return "裸の素体は、成人キャラ(キャラシートの成人フラグ)にだけ当てられます。"
    if character.get("is_minor") or (character.get("age") is not None and int(character["age"]) < 18):
        return "未成年と定義されたキャラには、裸の素体を当てられません。"
    if character.get("age") is None:
        return "裸の素体は、キャラシートに年齢(18 歳以上)を入れたキャラにだけ当てられます。"
    text = ", ".join(str(character.get(k) or "") for k in _SHEET_TEXT_FIELDS)
    found = sorted(set(character_minor_tags(character)) | set(minor_words(text, adult=content_guard.is_defined_adult(character))))
    if found:
        return f"キャラシートに未成年を示すタグがあるため、裸の素体を当てられません: {', '.join(found)}"
    minors = _minor_stages(character, family)
    if minors:
        return f"同じ人物の成長段階に未成年の段階があるため、裸の素体を当てられません: {', '.join(minors)}"
    return None


def _is_minor(character: dict[str, Any]) -> bool:
    return bool(character.get("is_minor")) or (character.get("age") is not None and int(character["age"]) < 18)


def _minor_stages(character: dict[str, Any], family: list[dict[str, Any]]) -> list[str]:
    return [c["name"] for c in family if c["id"] != character["id"] and _is_minor(c)]


def minor_coded(character: dict[str, Any], family: list[dict[str, Any]]) -> bool:
    """
    未成年の手がかりがあるキャラか(未成年の定義・年齢・未成年を示すタグ・成長段階の未成年)。成人フラグの有無は見ない
    (高校生のキャラに成人フラグが付いていることがある)。素体スーツ・水着素体を控えめに描くのに使う。
    """
    text = ", ".join(str(character.get(k) or "") for k in _SHEET_TEXT_FIELDS)
    return (
        _is_minor(character)
        or bool(minor_words(text, adult=content_guard.is_defined_adult(character)) or character_minor_tags(character))
        or bool(_minor_stages(character, family))
    )


def file_dir_name(base: dict[str, Any]) -> str:
    """描いた絵を置くデータセットのフォルダの名前(裸の素体は R18 のフォルダに分ける)。"""
    return "body_base_r18" if base.get("kind") == "nude" else "body_base"
