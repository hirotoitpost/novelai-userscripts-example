"""
下着・水着のコレクションと、からかいコレクションの漫画を、好きなキャラで作るためのテンプレート。

テーマ(衣装)とポーズを組み合わせて、台本(1シーン=1コマ。routes/story.py の /api/story/scripted と同じ形)を作る。
- コレクション: テーマごとに、キャラが一人ずつ立ちポーズで登場する(見出しの風景のコマ付き)。
- からかいコレクション: 選んだポーズを、キャラ全員がそれぞれ1コマずつ演じる(衣装はテーマを順に回す)。
  食い込み(wedgie)のポーズは、自分で引き上げてからかう場面としてだけ使う。

描いてはいけないもの(乳首・乳輪・性器・cameltoe など)はネガティブで必ず打ち消す。成人キャラ専用(年齢を入れた
18 歳以上のキャラだけ。呼び出し側で確かめる)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from . import content_guard
from . import vocabulary as vocab

Kind = Literal["lingerie", "swimsuit", "tease_lingerie", "tease_swimsuit"]

KINDS: dict[str, str] = {
    "lingerie": "下着コレクション",
    "swimsuit": "水着コレクション",
    "tease_lingerie": "からかい下着コレクション",
    "tease_swimsuit": "からかい水着コレクション",
}

# 場所(作画タグ)
STAGES = {
    "lingerie": "indoors, photo studio, curtain backdrop, soft lighting, elegant interior",
    "swimsuit": "outdoors, beach, summer, blue sky, ocean, sunlight, white sand",
    "tease_lingerie": "indoors, bedroom, bed, soft lighting, curtains, warm light",
    "tease_swimsuit": "outdoors, beach, summer, blue sky, ocean, sunlight, white sand",
}
ITEM = {"lingerie": "lingerie", "swimsuit": "swimsuit", "tease_lingerie": "lingerie", "tease_swimsuit": "swimsuit, wet, water drops"}

# 体つき(場面の服装の先頭に足す)。sheet はキャラシートの体型のまま
BODIES = {
    "sheet": "",
    # 引き締まった体(athletic・toned)は入れない(腹筋・背筋が強く出る)。戦うキャラなどはキャラシートの体型に書く
    "slender": "shiny skin, slender, slim waist, long legs, skindentation",
    "curvy": "shiny skin, curvy, slim waist, wide hips, thick thighs, skindentation",
}

# 打ち消す: 描いてはいけないもの(どのコマにも入れる)は、コンテンツガードの collection.content_negative(開発管理者のページ)
HAND_NEGATIVE = "bad hands, malformed hands, extra fingers, missing fingers, fused fingers, twisted wrist, extra arms, extra hands"
# 食い込みのポーズ以外では、引っ張る・食い込む絵にしない
NO_WEDGIE = "wedgie, pulling panties"
FACE_AWAY_NEGATIVE = "looking back, looking at viewer, looking over shoulder"
SIDE_NEGATIVE = "from side, side view, profile, three-quarter view, head visible, hair hanging beside legs"
# 前屈で、お尻の上に頭(お団子・ポニーテールの根元)が出る絵にしない
HEAD_ABOVE_NEGATIVE = (
    "head above hips, head visible above buttocks, hair bun above buttocks, ponytail above buttocks, head on top"
)
HAIR_UP_NEGATIVE = "ponytail, high ponytail, hair bun, updo, hair tie"
# 前屈で床へ垂れる髪(長い髪のキャラだけ。短い髪のキャラに付けるとロングヘアになる)
HANGING_HAIR = "hair hanging down to floor between hands"


def has_long_hair(appearance: str | None) -> bool:
    tags = {t.strip().lower() for t in (appearance or "").split(",")}
    return any(word in tags for word in vocab.items("collection.long_hair_tags"))


def hair_down_appearance(appearance: str | None) -> str | None:
    """容姿からまとめ髪のタグを外し、髪を下ろした容姿にする。まとめ髪のタグが無ければ None(そのまま使う)。"""
    tags = [t.strip() for t in (appearance or "").split(",") if t.strip()]
    kept = [t for t in tags if not set(t.lower().replace("_", " ").split()) & vocab.words("collection.hair_up_words")]
    if len(kept) == len(tags):
        return None
    return _join(", ".join(kept), "hair down, hair hanging down")
# お尻を上げたポーズで、脚の間から後頭部が見える(顔の向きが体と合わない)絵にしない。逆さの顔は「股のぞき」のポーズだけ
HEAD_BETWEEN_LEGS = "head between legs, looking through legs, back of head between legs"


@dataclass(frozen=True)
class Theme:
    name: str
    scenery: str
    # キャラごとの衣装(キャラの並び順に回して使う)
    looks: tuple[str, ...]


LINGERIE_THEMES = (
    Theme("白レース", "white flowers, mannequins in white lingerie",
          ("white lace bra, white lace panties, red ribbon", "black lace bra, black lace panties, red trim")),
    Theme("紐", "silk ribbons, mannequins in lingerie",
          ("lavender lace bra, lavender side-tie panties", "red lace bra, red side-tie panties")),
    Theme("Tバック", "black lace fabric, mannequins in lingerie",
          ("black lace bra, black lace thong", "white lace bra, white thong")),
    Theme("ガーター", "roses, mannequins in garter lingerie",
          ("white lace bra, white lace panties, white garter belt, white thighhighs",
           "crimson lace bra, crimson lace panties, garter belt, black thighhighs")),
    Theme("サテン", "silk fabric draped, mannequins in satin lingerie",
          ("silver satin bra, silver satin panties", "black satin bra, black satin panties")),
    Theme("ベビードール", "bedroom set, moonlight, mannequins in nightwear",
          ("sheer white babydoll, white panties", "sheer red babydoll, red panties")),
)
SWIM_THEMES = (
    Theme("三角ビキニ", "red and white decorations", ("white string bikini, red ties", "red string bikini")),
    Theme("ハイレグ", "beach umbrella, deck chair",
          ("black high-leg one-piece swimsuit, highleg", "white high-leg one-piece swimsuit, highleg")),
    Theme("フリル", "hibiscus flowers, tropical plants", ("lavender frilled bikini", "red frilled bikini")),
    Theme("スポーティ", "beach volleyball net", ("grey sports bikini", "black sports bikini")),
    Theme("競泳", "swimming pool lane, starting block", ("navy competition swimsuit", "black competition swimsuit, red lines")),
    Theme("ホルター", "palm trees, sunset", ("white halter bikini", "red halter bikini, side-tie bottom")),
)
THEMES = {"lingerie": LINGERIE_THEMES, "swimsuit": SWIM_THEMES, "tease_lingerie": LINGERIE_THEMES, "tease_swimsuit": SWIM_THEMES}


@dataclass(frozen=True)
class Pose:
    key: str
    label: str
    action: str
    framing: str
    line: str
    wedgie: bool = False
    # 顔を見せない後ろ姿(振り向きのタグを打ち消す)
    face_away: bool = False
    # しゃがみ(膝をつく絵に流れやすいので打ち消す)
    squat: bool = False
    # 脚の間から逆さの顔を見せる(股のぞき)。これ以外のポーズでは、脚の間の頭を打ち消す
    through_legs: bool = False
    # 真後ろから描く(斜め横から描くと頭が脚の横に出るので、横向きを打ち消す)
    side_negative: bool = False
    # 頭が下がる前屈。お尻の上に頭・まとめ髪が出るのを打ち消し、まとめ髪のキャラはそのコマだけ髪を下ろす
    bend: bool = False
    # コマの生成サイズ。前屈・股のぞきは体が絵の半分ほどなので、縦長だと脚が伸びる → 正方形
    size: str | None = None
    # 全身(足先まで)を描かせる(full body だけだと太ももで切れる)。足の切れを打ち消す
    whole: bool = False


# コレクションの正面の立ちポーズ(テーマごとに順に回す)
STAND_POSES = (
    Pose("hip", "腰に手", "front view, hand on hip, confident smile, looking at viewer", "cowboy shot", "……どう？"),
    Pose("behind", "手を後ろ", "front view, hands behind back, smile, looking at viewer", "cowboy shot", "似合ってる？"),
    Pose("hair", "髪に手", "front view, one hand in hair, posing, looking at viewer", "cowboy shot", "……ちょっと、照れる"),
    Pose("stretch", "伸び", "front view, arms raised, stretching, smile", "cowboy shot", "ん〜っ"),
)
# コレクションの背面のポーズ(テーマごとに正面のあとに入れる。振り返るものと、顔を見せない後ろ姿を交互に)
BACK_POSES = (
    Pose("back_look", "後ろ姿(振り返る)", "from behind, back, looking back over shoulder, smile", "cowboy shot", "後ろも、見て"),
    Pose("back_away", "後ろ姿", "from behind, back view, facing away, back, hands behind head", "cowboy shot", "……背中、どう？",
         face_away=True),
    Pose("back_hip", "後ろ姿(腰に手)", "from behind, back, hand on hip, looking back, smile", "full body", "うしろ姿も、自信あり"),
    Pose("back_hair", "後ろ姿(髪をかき上げる)", "from behind, back view, facing away, hand in hair, back", "cowboy shot",
         "……見てる？", face_away=True),
    # 立位体前屈: 膝を伸ばして足をそろえ(足首を交差させ)、腰から前に倒れて手を床につく。頭は脚の向こうで顔は見えない
    Pose("forward_bend", "立位体前屈",
         "standing forward bend, bent over, straight legs, legs together, touching toes, hands on floor, from behind, "
         "directly behind, symmetrical, ass focus, head hidden behind legs, face hidden", "full body", "……やわらかいでしょ",
         face_away=True, side_negative=True, bend=True, size="square"),
)

TEASE = "teasing, smug, smirk, blush"
WHOLE_BODY = "1.4::full body::, feet, wide shot"
CROPPED_NEGATIVE = "feet out of frame, cropped legs, close-up, cowboy shot"
# お尻を突き出す・前かがみ系のポーズは、腰から上(cowboy shot)だと脚・足先が切れるので全身で描く(ユーザーの依頼 2026-11-09)。
# 縦長で比率が崩れなければ縦長のまま。崩れる前屈・股のぞきだけ正方形(size)
# からかいのポーズ(キャラ全員がそれぞれ演じる)
TEASE_POSES = (
    Pose("bent_over", "前かがみでお尻を突き出す",
         "bent over, hands on floor, ass up, from behind, ass focus, looking back over shoulder", WHOLE_BODY, "……見てた？", whole=True),
    Pose("lean_lips", "前かがみで唇に指",
         "leaning forward, hands on knees, cleavage, finger to lips, looking at viewer", "upper body", "……近い？"),
    Pose("lying_side", "横寝", "lying on side, head on hand, hand on hip, looking at viewer", "full body", "……となり、空いてる"),
    Pose("squat_chin", "しゃがんで頬杖", "squatting, knees together, chin on hands, looking up at viewer", "full body",
         "……ここから、見上げる", squat=True),
    Pose("all_fours_back", "四つんばいで振り返る",
         "on all fours, arched back, from behind, looking back over shoulder, ass focus", WHOLE_BODY, "……ころん", whole=True),
    Pose("cross_legs", "脚組み座り", "sitting, crossed legs, leaning back, hand on thigh, looking at viewer", "full body",
         "……座って、待ってる"),
    Pose("hand_butt", "お尻に手を当てて振り返る",
         "standing, from behind, hand on own butt, looking back over shoulder, ass focus", WHOLE_BODY, "……さわりたい？", whole=True),
    Pose("hands_head", "頭の後ろで手を組む", "hands behind head, arms up, armpits, hip sway, looking at viewer", "cowboy shot",
         "……腋、見せたげる"),
    Pose("squat_back", "後ろ向きにしゃがむ",
         "squatting, from behind, ass focus, looking back over shoulder, hands on knees", "full body", "……しゃがむと、こう", squat=True),
    Pose("squat_front", "前かがみでしゃがむ",
         "squatting, heels up, on toes, knees bent, elbows on knees, hands on cheeks, leaning forward, cleavage, "
         "looking at viewer, tongue out", "full body", "ほら、目の前", squat=True),
    Pose("squat_away", "しゃがみ(背面)",
         "squatting, facing away, from behind, back, ass focus, looking back over shoulder", "full body", "……後ろ、見てて",
         squat=True),
    Pose("squat_arms", "しゃがみ(両手を頭の後ろ)",
         "squatting, arms up, hands behind head, armpits, heels up, knees bent, looking at viewer", "full body",
         "これで、いい？", squat=True),
    Pose("upside_down", "仰向けで逆さ(見上げる)",
         "lying on back, upside-down, head down, legs up, legs together, hair spread out, looking up at viewer, from above",
         "full body", "……上から、見ないで"),
    Pose("all_fours_away", "四つんばい(背面・顔なし)",
         "all fours, facing away, from behind, ass focus, head out of frame, lower body", "lower body", "……どこ、見てるの",
         face_away=True),
    Pose("through_legs", "股のぞき(逆さの顔)",
         "bent over, looking through legs, upside-down face, face between legs, looking at viewer, from behind, ass focus, "
         "hands on knees", "full body", "……さかさまの、わたし", through_legs=True, size="square"),
    Pose("wedgie_stand", "食い込み(立って引き上げる)",
         "standing, from behind, wedgie, pulling own panties up, looking back over shoulder, ass focus", "cowboy shot",
         "……くいこんだ", wedgie=True),
    Pose("wedgie_bent", "食い込み(前かがみで引っ張る)",
         "bent over, hand on knee, wedgie, tugging own panties, from behind, looking back over shoulder, ass focus",
         WHOLE_BODY, "こういうの、好きなんでしょ", wedgie=True, whole=True),
    Pose("wedgie_squat", "食い込み(しゃがんで引き上げる)",
         "squatting, facing away, wedgie, pulling own panties, looking back over shoulder, ass focus", "full body",
         "……直して？", wedgie=True, squat=True),
)
POSES_BY_KEY = {p.key: p for p in TEASE_POSES}


@dataclass
class CollectionOptions:
    kind: Kind
    title: str | None = None
    # 体つき(sheet はキャラシートのまま)。既定はコレクションが slender、からかいが curvy
    body: str | None = None
    # 画風のタグ(全コマの作画タグの最後に足す。空なら足さない)
    style_tags: str = ""
    # 全コマに explicit を足す(食い込み・肌の柔らかさが出る。描いてはいけない所はネガティブで打ち消す)
    explicit: bool = True
    # からかいで使うポーズ(キー。空なら全部)
    poses: list[str] = field(default_factory=list)
    # セリフを入れる(ポーズごとの短い一言。あとで編集画面で直せる)
    lines: bool = True
    # コレクションのテーマ(名前。空なら全部)
    themes: list[str] = field(default_factory=list)


def _join(*parts: str | None) -> str:
    seen: list[str] = []
    for part in parts:
        for tag in (part or "").split(","):
            tag = tag.strip()
            if tag and tag not in seen:
                seen.append(tag)
    return ", ".join(seen)


def _scene(
    tags: str, ids: list[int], actions: dict[int, str], outfits: dict[int, str], negative: str,
    *, narration: str | None = None, line: tuple[int, str] | None = None, size: str | None = None,
) -> dict[str, Any]:
    dialogue = [{"text": line[1], "speaker_id": line[0]}] if line else []
    return {
        "panel_size": size,
        "text": f"「{line[1]}」" if line else (narration or "…"),
        "prompt_tags": tags,
        "character_ids": ids,
        "character_actions": actions,
        "character_outfits": outfits,
        "narration": narration,
        "dialogue": dialogue,
        "negative_prompt": negative,
    }


def build_collection(characters: list[dict[str, Any]], options: CollectionOptions) -> dict[str, Any]:
    """
    キャラ(id・name の辞書。1人以上)とオプションから、台本({title, premise, scenes})を作る。
    scenes の各要素は /api/story/scripted のシーンの形。
    """
    if not characters:
        raise ValueError("キャラを1人以上選んでください。")
    kind = options.kind
    tease = kind.startswith("tease")
    body = BODIES.get(options.body or ("curvy" if tease else "slender"))
    if body is None:
        raise ValueError(f"体つきは {', '.join(BODIES)} のどれかにしてください。")
    themes = [t for t in THEMES[kind] if not options.themes or t.name in options.themes]
    if not themes:
        raise ValueError("テーマが1つもありません。")
    stage, item = STAGES[kind], ITEM[kind]
    explicit = "explicit" if options.explicit else ""
    base_negative = _join(content_guard.text("collection.content_negative"), HAND_NEGATIVE)
    ids = [int(c["id"]) for c in characters]
    names = "・".join(str(c["name"]) for c in characters)

    def look(index: int, theme: Theme) -> str:
        return _join(body, theme.looks[index % len(theme.looks)])

    def panel_tags(*parts: str) -> str:
        return _join(stage, *parts, item, explicit, options.style_tags)

    scenes: list[dict[str, Any]] = []
    first = themes[0]
    # 最初と最後: みんなで並ぶ(1人なら、その人の立ち絵)
    group = "solo" if len(ids) == 1 else "standing side by side"
    scenes.append(_scene(
        panel_tags(group, "cowboy shot"), ids,
        {cid: (TEASE if tease else "smile") + ", looking at viewer" for cid in ids},
        {cid: look(i, first) for i, cid in enumerate(ids)},
        _join(base_negative, NO_WEDGIE),
        narration=f"{names}の、{KINDS[kind]}。",
    ))
    if tease:
        poses = [POSES_BY_KEY[k] for k in options.poses if k in POSES_BY_KEY] if options.poses else list(TEASE_POSES)
        if not poses:
            raise ValueError("ポーズが1つもありません。")
        for n, pose in enumerate(poses):
            theme = themes[n % len(themes)]
            for i, cid in enumerate(ids):
                negative = _join(base_negative, "" if pose.wedgie else NO_WEDGIE,
                                 FACE_AWAY_NEGATIVE if pose.face_away else "",
                                 "" if pose.through_legs else HEAD_BETWEEN_LEGS,
                                 "kneeling, all fours, seiza" if pose.squat else "",
                                 CROPPED_NEGATIVE if pose.whole else "")
                action = pose.action if pose.face_away else _join(pose.action, TEASE)
                scenes.append(_scene(
                    panel_tags("solo", pose.framing), [cid], {cid: action}, {cid: look(i, theme)}, negative,
                    narration=f"{pose.label}　{characters[i]['name']}",
                    line=(cid, pose.line) if options.lines else None, size=pose.size,
                ))
    else:
        for n, theme in enumerate(themes):
            scenes.append(_scene(_join(stage, theme.scenery, "no humans, scenery", options.style_tags), [], {}, {},
                                 base_negative, narration=theme.name))
            for i, cid in enumerate(ids):
                # 正面と背面を1コマずつ
                front = STAND_POSES[(n + i * 2) % len(STAND_POSES)]
                back = BACK_POSES[(n + i) % len(BACK_POSES)]
                for pose, label in ((front, theme.name), (back, f"{theme.name}・後ろ")):
                    appearance = characters[i].get("appearance_tags")
                    hair_down = hair_down_appearance(appearance) if pose.bend else None
                    action = pose.action
                    if pose.bend and (hair_down or has_long_hair(appearance)):
                        action = _join(action, HANGING_HAIR)
                    scene = _scene(
                        panel_tags("solo", pose.framing), [cid], {cid: action}, {cid: look(i, theme)},
                        _join(base_negative, NO_WEDGIE, HEAD_BETWEEN_LEGS, FACE_AWAY_NEGATIVE if pose.face_away else "",
                              SIDE_NEGATIVE if pose.side_negative else "", HEAD_ABOVE_NEGATIVE if pose.bend else "",
                              HAIR_UP_NEGATIVE if hair_down else ""),
                        narration=f"{label}　{characters[i]['name']}",
                        line=(cid, pose.line) if options.lines else None, size=pose.size,
                    )
                    if hair_down:
                        scene["character_appearances"] = {cid: hair_down}
                    scenes.append(scene)
    last = themes[-1]
    scenes.append(_scene(
        panel_tags(group, "cowboy shot"), ids,
        {cid: "peace sign, smile, looking at viewer" for cid in ids},
        {cid: look(i, last) for i, cid in enumerate(ids)},
        _join(base_negative, NO_WEDGIE),
        narration=f"{KINDS[kind]}、おしまい。",
    ))
    title = options.title or f"{names}の{KINDS[kind]}"
    return {"title": title, "premise": f"{names}が、いろいろな{'下着' if 'lingerie' in kind else '水着'}で{'読者をからかう' if tease else '撮影する'}。",
            "scenes": scenes}


def catalog() -> dict[str, Any]:
    """画面・MCP に出す一覧(種類・テーマ・からかいのポーズ・体つき)。"""
    return {
        "kinds": [{"id": k, "label": v} for k, v in KINDS.items()],
        "themes": {k: [t.name for t in v] for k, v in THEMES.items()},
        "poses": [{"key": p.key, "label": p.label, "wedgie": p.wedgie} for p in TEASE_POSES],
        "bodies": [{"id": "sheet", "label": "キャラシートのまま"}, {"id": "slender", "label": "すらっと(食い込み)"},
                   {"id": "curvy", "label": "むっちり(食い込み)"}],
    }
