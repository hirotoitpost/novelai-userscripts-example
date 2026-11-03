"""
素体の 3D(スキンヘッドのモデル)に付ける髪の型(scripts/blender/hair_kit.py の設定)。画面で選ぶ型と、数値ごとの範囲。
画面から来た値は clean で確かめてから Blender に渡す(省いた値は hair_kit.DEFAULTS)。まとめ髪だけ(下ろした髪は、肩・
背中に沿わせる所がまだ)。
"""

from __future__ import annotations

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
    "side_length": Field("顔の横の房の長さ", "float", 1.0, 0, 1.5, 0.05, hint="0 で耳の下・1 であご"),
    "wisps": Field("耳の前の後れ毛", "int", 2, 0, 3, 1, hint="左右それぞれ"),
    "nape_wisps": Field("うなじの後れ毛", "int", 1, 0, 3, 1, hint="左右それぞれ"),
    "buns": Field("お団子の数", "int", 1, 0, 2, 1, hint="0 でなし・1 で後ろ・2 で左右の後ろ"),
    "bun_size": Field("お団子の大きさ", "float", 0.5, 0.25, 0.8, 0.05, hint="頭の半径に対する割合"),
    "bun_height": Field("お団子の高さ", "float", 16.0, -20, 60, 1, "°", hint="頭の真ん中からの仰角"),
    "bun_wisps": Field("お団子からはねた毛", "int", 5, 0, 10, 1),
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
}


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
