"""
素体の 3D に着せる服の型紙(scripts/blender/garment_skirt.py の設定)。画面で選ぶ型(よく使う組み合わせ)と、
数値ごとの範囲。画面から来た値は clean で確かめてから Blender に渡す(省いた値は garment_skirt.DEFAULTS)。
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

KINDS: dict[str, tuple[str, dict[str, Field], dict[str, dict[str, Any]]]] = {
    "skirt": ("スカート", SKIRT_FIELDS, SKIRT_PRESETS),
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
