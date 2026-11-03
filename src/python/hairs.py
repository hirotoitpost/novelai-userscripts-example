"""
素体の 3D(スキンヘッドのモデル)に付ける髪の型(scripts/blender/hair_kit.py の設定)。画面で選ぶ型と、数値ごとの範囲。
画面から来た値は clean で確かめてから Blender に渡す(省いた値は hair_kit.DEFAULTS)。下ろした髪(後ろ髪・あごより長い
顔の横の房)は、Blender で重力で垂らして、肩・背中・胸に沿わせる。
"""

from __future__ import annotations

import re
from typing import Any

from .garments import Field, GarmentError

# 髪の数値(hair_kit.DEFAULTS の名前)
FIELDS: dict[str, Field] = {
    "volume": Field("頭の髪の厚み", "float", 14.0, 4, 30, 1, "mm", hint="てっぺんの厚み。生え際へ薄くなる"),
    "hairline": Field("額の生え際の高さ", "float", 28.0, 10, 50, 1, "°", hint="頭の真ん中からの仰角"),
    "bangs": Field("前髪の房の数", "int", 9, 0, 15, 1, hint="0 で前髪なし"),
    "bangs_length": Field("前髪の長さ", "float", 1.0, 0.2, 1.4, 0.05, hint="0 で生え際・1 で目の上"),
    "bangs_spread": Field("前髪の広がり", "float", 44.0, 15, 70, 1, "°", hint="正面から左右へ"),
    "bangs_part": Field("分け目", "float", 0.3, 0, 1, 0.05, hint="真ん中の房を短くする割合"),
    "side_locks": Field("顔の横の房", "int", 1, 0, 2, 1, hint="左右の組の数"),
    "side_length": Field("顔の横の房の長さ", "float", 1.0, 0, 3, 0.05, hint="0 で耳の下・1 であご・2 で胸の上"),
    "wisps": Field("耳の前の後れ毛", "int", 2, 0, 3, 1, hint="左右それぞれ"),
    "nape_wisps": Field("うなじの後れ毛", "int", 1, 0, 3, 1, hint="左右それぞれ"),
    "buns": Field("お団子の数", "int", 1, 0, 2, 1, hint="0 でなし・1 で後ろ・2 で左右の後ろ"),
    "bun_size": Field("お団子の大きさ", "float", 0.5, 0.25, 0.8, 0.05, hint="頭の半径に対する割合"),
    "bun_height": Field("お団子の高さ", "float", 16.0, -20, 60, 1, "°", hint="頭の真ん中からの仰角"),
    "bun_wisps": Field("お団子からはねた毛", "int", 5, 0, 10, 1),
    "back_length": Field("下ろした後ろ髪の長さ", "float", 0.0, 0, 3, 0.05,
                         hint="0 でまとめ髪・1 で肩・2 で肩甲骨・3 で背中の真ん中"),
    "back_locks": Field("下ろした後ろ髪の房の数", "int", 24, 10, 40, 1),
    "tails": Field("結んで垂らす束", "int", 0, 0, 2, 1, hint="0 でなし・1 でポニーテール・2 でツインテール"),
    "tail_length": Field("束の長さ", "float", 1.5, 0.3, 3, 0.05, hint="1 で 22cm"),
    "tail_height": Field("結び目の高さ", "float", 25.0, -20, 60, 1, "°", hint="頭の真ん中からの仰角"),
    "tail_volume": Field("束の太さ", "float", 1.0, 0.5, 2, 0.05),
}

# 画面で選ぶ型(試作で確かめた組み合わせ。docs/model_3d_plan_jp.md の 6b-1 の 6)
PRESETS: dict[str, dict[str, Any]] = {
    "bun_wisps": {"label": "お団子+後れ毛", "config": {}},
    "bun_neat": {
        "label": "お団子(すっきり)",
        "config": {"side_locks": 0, "wisps": 0, "nape_wisps": 0, "bun_wisps": 0, "bangs_part": 0.0},
    },
    "bun_high": {"label": "高いお団子", "config": {"bun_height": 45.0, "bun_size": 0.45, "nape_wisps": 0}},
    "twin_buns": {"label": "お団子 2 つ", "config": {"buns": 2, "bun_size": 0.4, "bun_height": 25.0, "bun_wisps": 3}},
    "short": {"label": "ショート(まとめ髪なし)", "config": {"buns": 0, "bun_wisps": 0, "wisps": 1, "side_length": 0.6}},
    "bob": {
        "label": "ボブ",
        "config": {"buns": 0, "bun_wisps": 0, "wisps": 0, "nape_wisps": 0, "bangs_part": 0.0, "back_length": 0.3},
    },
    "semi_long": {
        "label": "セミロング",
        "config": {"buns": 0, "bun_wisps": 0, "wisps": 0, "nape_wisps": 0, "side_length": 1.6, "back_length": 1.5},
    },
    "long": {
        "label": "ロング",
        "config": {"buns": 0, "bun_wisps": 0, "wisps": 0, "nape_wisps": 0, "bangs_part": 0.1, "side_length": 2.5,
                   "back_length": 3.0},
    },
    "ponytail": {
        "label": "ポニーテール",
        "config": {"buns": 0, "bun_wisps": 0, "wisps": 0, "nape_wisps": 0, "tails": 1, "tail_height": 30.0},
    },
    "low_ponytail": {
        "label": "低いポニーテール",
        "config": {"buns": 0, "bun_wisps": 0, "wisps": 1, "nape_wisps": 0, "tails": 1, "tail_height": -10.0,
                   "tail_length": 1.8},
    },
    "twintails": {
        "label": "ツインテール",
        "config": {"buns": 0, "bun_wisps": 0, "wisps": 0, "nape_wisps": 0, "bangs_part": 0.0, "tails": 2,
                   "tail_length": 2.0},
    },
}

# キャラの見た目のタグ → 型(上のものほど先に見る。まとめ方 → 長さの順)。タグは小文字にして、語の区切りで探す。
# 長さは色が間に入ったタグ(short black hair)も拾う。ハーフアップは後ろを下ろしているので、お団子でなくセミロング
_TAG_PRESETS: list[tuple[str, str]] = [
    (r"twin ?tails?|pigtails?", "twintails"),
    (r"low ponytail|side ponytail", "low_ponytail"),
    (r"ponytail", "ponytail"),
    (r"double bun|twin buns?|cone hair bun", "twin_buns"),
    (r"half[- ]?up", "semi_long"),
    (r"\bbuns?\b|updo|chignon", "bun_wisps"),
    (r"bob cut|\bbob\b", "bob"),
    (r"\blong( [a-z]+)? hair", "long"),
    (r"\bmedium( [a-z]+)? hair|shoulders?[- ]length", "semi_long"),
    (r"\bshort( [a-z]+)? hair|pixie cut", "short"),
]
# 前髪のタグ → 数値
_TAG_BANGS: list[tuple[str, dict[str, Any]]] = [
    (r"blunt bangs|straight bangs", {"bangs_part": 0.0}),
    (r"parted bangs|swept bangs|side-swept bangs|asymmetrical bangs", {"bangs_part": 0.6}),
    (r"no bangs|forehead|hair pulled back|slicked back", {"bangs": 0}),
    (r"long bangs|hair over eyes", {"bangs_length": 1.25}),
    (r"short bangs", {"bangs_length": 0.6}),
]
_HAIR_TAG = re.compile(r"hair|\bbuns?\b|twin ?tails?|pigtails?|ponytail|bangs|\bbob\b|updo|chignon|bald|pixie", re.I)


def suggest(tags: str) -> dict[str, Any] | None:
    """
    キャラの見た目のタグから、髪の型と数値を選ぶ(画面の最初の選び・スキンヘッドのモデルを作ったときの最初の髪)。
    {"preset", "config", "tags"(決め手にしたタグ)}。まとめ方・長さのタグが無ければ None(禿げ頭のタグも None)。
    """
    words = [t.strip().lower() for t in (tags or "").split(",") if t.strip()]
    hair = [w for w in words if _HAIR_TAG.search(w) and "pubic" not in w and "facial" not in w]
    if not hair or any(re.search(r"\bbald", w) for w in hair):
        return None
    preset, used = None, []
    for pattern, key in _TAG_PRESETS:
        found = [w for w in hair if re.search(pattern, w)]
        if found:
            preset, used = key, found[:1]
            break
    if preset is None:
        # 髪の色だけなど、まとめ方・長さが分からなければ選ばない
        return None
    config = dict(PRESETS[preset]["config"])
    for pattern, values in _TAG_BANGS:
        found = [w for w in hair if re.search(pattern, w)]
        if found:
            config.update(values)
            used += found[:1]
    return {"preset": preset, "config": config, "tags": used}


def options() -> dict[str, Any]:
    """画面に出す髪の型・数値の範囲。"""
    return {
        "presets": PRESETS,
        "fields": {
            key: {
                "label": f.label, "kind": f.kind, "default": f.default, "min": f.low, "max": f.high, "step": f.step, "unit": f.unit,
                "optional": f.optional, "hint": f.hint,
            }
            for key, f in FIELDS.items()
        },
    }


def clean(config: dict[str, Any]) -> dict[str, Any]:
    """髪の数値を確かめて、Blender に渡す形にする(知らない名前・範囲の外は GarmentError)。"""
    unknown = sorted(set(config) - set(FIELDS))
    if unknown:
        raise GarmentError(f"知らない値です: {', '.join(unknown)}")
    result: dict[str, Any] = {}
    for key, value in config.items():
        f = FIELDS[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
            raise GarmentError(f"{f.label}は数で入れてください")
        if f.kind == "int" and value != int(value):
            raise GarmentError(f"{f.label}は整数で入れてください")
        if not f.low <= value <= f.high:
            raise GarmentError(f"{f.label}は {f.low:g}〜{f.high:g}{f.unit} です")
        result[key] = int(value) if f.kind == "int" else float(value)
    return result
