"""
素体の 3D に着せる服の型紙(scripts/blender/garment_skirt.py・garment_top.py の設定)。画面で選ぶ型(よく使う
組み合わせ)と、数値ごとの範囲。画面から来た値は clean で確かめてから Blender に渡す(省いた値は garment_*.DEFAULTS)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class GarmentError(ValueError):
    """服の設定が違う。"""


@dataclass(frozen=True)
class Field:
    label: str
    kind: str  # "float"・"int"・"bool"
    # 省いたときの値(garment_<種類>.py の DEFAULTS と同じ)
    default: float | bool | None
    low: float = 0
    high: float = 1
    step: float = 1
    unit: str = ""
    # None を許す(省くと別の決め方になる値)
    optional: bool = False
    hint: str = ""


# スカートの数値(garment_skirt.DEFAULTS の名前)。gores(型紙の継ぎ目の数)は形に効かないので出さない
SKIRT_FIELDS: dict[str, Field] = {
    "length": Field("丈", "float", 0.25, 0.1, 1.0, 0.01, hint="0 で腰・1 で足首"),
    "flare": Field("広がり", "float", 1.0, 1.0, 3.0, 0.05, hint="裾の周り ÷ 腰の周り。1 は肌にぴったり"),
    "ease": Field("ゆとり", "float", 3, 0, 30, 1, "mm"),
    "pleats": Field("ひだの数", "int", 0, 0, 40, 1),
    "pleat_depth": Field("ひだの深さ", "float", 14, 5, 40, 1, "mm"),
    "pleat_stitch": Field("ひだを縫い留める長さ", "float", None, 0, 30, 1, "cm", optional=True, hint="空にすると腰まで"),
    "knife": Field("車ひだ", "bool", False, hint="折り目を付けて重ねる(制服のスカート)。ひだの数が要ります"),
    "tucks": Field("タックの数", "int", 0, 0, 8, 1, hint="前に左右対称"),
    "tuck_depth": Field("タックの幅", "float", 25, 5, 50, 1, "mm"),
    "tuck_stitch": Field("タックを縫い留める長さ", "float", 6, 0, 20, 1, "cm"),
    "gussets": Field("マチの数", "int", 0, 0, 3, 1, hint="後ろ中心・左脇・右脇の順"),
    "gusset_width": Field("マチの幅(裾)", "float", 12, 2, 30, 1, "cm"),
    "gusset_height": Field("マチの高さ", "float", 15, 5, 40, 1, "cm"),
}

# 画面で選ぶ型(試作で確かめた組み合わせ。docs/model_3d_plan_jp.md の 6b)
SKIRT_PRESETS: dict[str, dict[str, Any]] = {
    "pencil_mini": {"label": "タイトなミニ", "config": {"length": 0.25, "flare": 1.0, "ease": 3}},
    "tuck_mini": {
        "label": "タイトなミニ(タック・マチ入り)",
        "config": {"length": 0.25, "flare": 1.0, "ease": 3, "tucks": 4, "gussets": 3, "gusset_height": 12},
    },
    "pleats_mini": {
        "label": "プリーツのミニ",
        "config": {"length": 0.25, "flare": 1.6, "ease": 6, "pleats": 20, "pleat_depth": 22, "pleat_stitch": 5},
    },
    "knife_mini": {
        "label": "車ひだのミニ(制服)",
        "config": {"length": 0.25, "flare": 1.35, "ease": 6, "pleats": 20, "pleat_depth": 25, "knife": True},
    },
    "flare": {"label": "フレア", "config": {"length": 0.55, "flare": 2.3, "ease": 8}},
    "long": {"label": "ロング", "config": {"length": 0.92, "flare": 1.35, "ease": 8}},
}

# 上の服の数値(garment_top.DEFAULTS の名前)
TOP_FIELDS: dict[str, Field] = {
    "length": Field("丈", "float", 1.0, 0.3, 1.3, 0.05, hint="0 でウエスト・1 で腰"),
    "ease": Field("ゆとり", "float", 8, 0, 30, 1, "mm"),
    "bridge": Field("胸の下を渡す広さ", "int", None, 10, 100, 5, optional=True,
                    hint="空にすると胸から真っすぐ落ちる。小さいほど体に沿う"),
    "bending": Field("布の硬さ", "float", 2.0, 0.5, 10, 0.5, hint="曲がりにくさ"),
    "sleeve": Field("袖の長さ", "float", 0.5, 0, 2, 0.05, hint="0 で袖なし・1 で肘・2 で手首"),
    "sleeve_ease": Field("袖のゆとり", "float", 12, 0, 30, 1, "mm"),
    "sleeve_flare": Field("袖口の広がり", "float", 1.15, 1.0, 2.0, 0.05, hint="袖口の周り ÷ 付け根の周り"),
    "neck_width": Field("襟ぐりの幅", "float", 1.5, 0, 6, 0.5, "cm", hint="首からの離れ"),
    "neck_front": Field("襟ぐりの前の深さ", "float", 6, 0, 15, 0.5, "cm"),
    "neck_back": Field("襟ぐりの後ろの深さ", "float", 2, 0, 10, 0.5, "cm"),
    "puff": Field("袖のふくらみ", "float", 0.0, 0, 1, 0.05, hint="0 でふくらまない。袖口は腕に沿ってすぼまる"),
    "collar": Field("丸えりの幅", "float", 0, 0, 12, 0.5, "cm", hint="0 でえりなし"),
    "collar_gap": Field("えりの前の開き", "float", 30, 0, 90, 5, "°"),
}

# 上の服の型: 形(試作で確かめた 3 つ)× 袖
_TOP_FITS = {
    "loose": ("ゆったり", {"ease": 8}),
    "fitted": ("すっきり", {"bridge": 60, "ease": 5}),
    "tight": ("タイト", {"bridge": 25, "ease": 3, "sleeve_ease": 6, "sleeve_flare": 1.0, "bending": 4.0}),
}
_TOP_SLEEVES = {
    "short": ("半袖", {"sleeve": 0.5}), "puff": ("パフ袖", {"sleeve": 0.5, "puff": 0.5}),
    "none": ("袖なし", {"sleeve": 0.0}), "long": ("長袖", {"sleeve": 2.0}),
}
TOP_PRESETS: dict[str, dict[str, Any]] = {
    f"{fit}_{sleeve}": {"label": f"{fit_label}・{sleeve_label}", "config": {**fit_config, **sleeve_config}}
    for fit, (fit_label, fit_config) in _TOP_FITS.items()
    for sleeve, (sleeve_label, sleeve_config) in _TOP_SLEEVES.items()
}
# えりの付いたブラウス(襟ぐりを浅く丸くして、丸えりを置く)
TOP_PRESETS["blouse_puff"] = {
    "label": "ブラウス(すっきり・パフ袖・丸えり)",
    "config": {"bridge": 60, "ease": 5, "sleeve": 0.5, "puff": 0.5, "collar": 6, "neck_front": 4},
}
TOP_PRESETS["blouse_long"] = {
    "label": "ブラウス(ゆったり・長袖・丸えり)",
    "config": {"ease": 8, "sleeve": 2.0, "puff": 0.6, "collar": 7, "collar_gap": 20, "neck_front": 2, "neck_width": 1.0},
}

KINDS: dict[str, tuple[str, dict[str, Field], dict[str, dict[str, Any]]]] = {
    "skirt": ("スカート", SKIRT_FIELDS, SKIRT_PRESETS),
    "top": ("上の服", TOP_FIELDS, TOP_PRESETS),
}


def options() -> dict[str, Any]:
    """画面に出す服の種類・型・数値の範囲。"""
    return {
        kind: {
            "label": label,
            "presets": presets,
            "fields": {
                key: {
                    "label": f.label, "kind": f.kind, "default": f.default, "min": f.low, "max": f.high, "step": f.step, "unit": f.unit,
                    "optional": f.optional, "hint": f.hint,
                }
                for key, f in fields.items()
            },
        }
        for kind, (label, fields, presets) in KINDS.items()
    }


def clean(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    """服の数値を確かめて、Blender に渡す形にする(知らない名前・範囲の外は GarmentError)。"""
    if kind not in KINDS:
        raise GarmentError(f"作れない服です: {kind}")
    fields = KINDS[kind][1]
    unknown = sorted(set(config) - set(fields))
    if unknown:
        raise GarmentError(f"知らない値です: {', '.join(unknown)}")
    result: dict[str, Any] = {}
    for key, value in config.items():
        f = fields[key]
        if value is None:
            if not f.optional:
                raise GarmentError(f"{f.label}を入れてください")
            result[key] = None
        elif f.kind == "bool":
            if not isinstance(value, bool):
                raise GarmentError(f"{f.label}は、する・しないで選びます")
            result[key] = value
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
                raise GarmentError(f"{f.label}は数で入れてください")
            if f.kind == "int" and value != int(value):
                raise GarmentError(f"{f.label}は整数で入れてください")
            if not f.low <= value <= f.high:
                raise GarmentError(f"{f.label}は {f.low:g}〜{f.high:g}{f.unit} です")
            result[key] = int(value) if f.kind == "int" else float(value)
    if result.get("knife") and not result.get("pleats"):
        raise GarmentError("車ひだには、ひだの数が要ります")
    return result
