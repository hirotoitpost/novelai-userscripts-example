"""
素体の 3D モデルの形状パーツ(docs/model_3d_shapes_plan_jp.md)。Hunyuan3D-2mv の形は、部分的に崩れる(素体の絵の陰影を
へこみと読む: 矢野栄子の裸の素体で、胸の先が 8.8mm えぐれた。2026-11 実機)。体の面を部位ごとに変形するシェイプキー
(形の差分)を Blender で作り(scripts/blender/rig_model.py)、強さ 0〜1 で入れたり切ったりする。

- 部位(PARTS): 場所は骨と体の面から自動で決める。nude_only の部位(乳首・乳輪)は裸の素体のモデルだけ、adult_only の
  部位(胸の大きさ)は控えめに描かないキャラ(未成年の手がかりの無いキャラ)だけ。性器の部位は作らない(素体の決まり)。
- 形(PRESETS): 部位ごとの形の値と、その形を表すタグ。素体・キャラシートのタグから、部位ごとに既定の形を選ぶ(choose)。
  形の値は、それぞれの部位の大きさ(胸の幅など)に対する割合なので、体格の違うモデルにも同じ形を当てられる。
- 自動へこみ補正(repair): 部位を決めず、AI の形にできた小さなへこみを、周りのなめらかな面に寄せる。顔・股・へそは除く。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Part:
    label: str
    # 左右にあるか(乳首・乳輪・胸)。ある部位は、左右とも同じ形を 1 つのシェイプキーにする
    sides: bool
    nude_only: bool = False
    adult_only: bool = False


PARTS: dict[str, Part] = {
    "repair": Part("全身の自動へこみ補正", sides=False),
    "nipple": Part("乳首", sides=True, nude_only=True),
    "areola": Part("乳輪", sides=True, nude_only=True),
    "breast": Part("胸の大きさ・形", sides=True, adult_only=True),
    "navel": Part("へそ", sides=False),
    "belly": Part("お腹", sides=False),
}


@dataclass(frozen=True)
class Preset:
    part: str
    label: str
    # この形を表すタグ(素体・キャラシートにあれば、部位の既定の形にする)
    tags: tuple[str, ...]
    # 形の値。op は Blender での変形のしかた:
    # - bump: 部位の点のまわり(radius。部位の大きさに対する割合)を、面の向きへ height(同じく割合。負でへこむ)だけ動かす。
    #   edge は形のふち(0 でなだらかな丘、1 に近いほど筒のように切り立つ)
    # - lift: 部位のまわりを上へ height だけ動かす(胸の垂れ・持ち上がり)
    # - repair: 小さなへこみを周りの面に寄せる(depth はへこみとみなす深さ m、size はかたまりの大きさの上限 m)
    params: dict[str, float | str] = field(default_factory=dict)


PRESETS: dict[str, Preset] = {
    "repair": Preset("repair", "へこみをならす", (), {"op": "repair", "depth": 0.003, "size": 0.04}),
    # 乳首: 部位の大きさは乳輪の半径の基準(胸の幅の 0.18 倍)
    "nipple_normal": Preset("nipple", "ふつう", (), {"op": "bump", "radius": 0.28, "height": 0.22, "edge": 0.7}),
    "nipple_small": Preset("nipple", "小さめ", ("small nipples",), {"op": "bump", "radius": 0.2, "height": 0.14, "edge": 0.7}),
    "nipple_large": Preset("nipple", "大きめ", ("large nipples", "big nipples"), {"op": "bump", "radius": 0.38, "height": 0.36, "edge": 0.75}),
    "nipple_puffy": Preset("nipple", "ぷっくり", ("puffy nipples",), {"op": "bump", "radius": 0.5, "height": 0.3, "edge": 0.3}),
    "nipple_inverted": Preset("nipple", "陥没", ("inverted nipples",), {"op": "bump", "radius": 0.22, "height": -0.15, "edge": 0.6}),
    "areola_flat": Preset("areola", "平ら", (), {"op": "bump", "radius": 1.0, "height": 0.0, "edge": 0.2}),
    "areola_plump": Preset("areola", "ふっくら", ("plump areola", "puffy areola", "puffy areolae"), {"op": "bump", "radius": 1.0, "height": 0.18, "edge": 0.35}),
    "areola_large": Preset("areola", "大きめ・ふっくら", ("large areolae", "big areola", "large areola"), {"op": "bump", "radius": 1.4, "height": 0.12, "edge": 0.3}),
    # 胸: 部位の大きさは胸の幅(左右の胸の先の間の半分)
    "breast_keep": Preset("breast", "そのまま", (), {"op": "bump", "radius": 1.0, "height": 0.0, "edge": 0.0}),
    "breast_larger": Preset("breast", "大きく", ("huge breasts", "gigantic breasts"), {"op": "bump", "radius": 1.0, "height": 0.25, "edge": 0.0}),
    "breast_smaller": Preset("breast", "小さく", ("small breasts", "flat chest"), {"op": "bump", "radius": 1.0, "height": -0.2, "edge": 0.0}),
    "breast_lifted": Preset("breast", "上向き", ("perky breasts",), {"op": "lift", "radius": 1.0, "height": 0.1}),
    "breast_sagging": Preset("breast", "垂れ", ("sagging breasts",), {"op": "lift", "radius": 1.0, "height": -0.12}),
    # へそ・お腹: 部位の大きさは胴の幅(左右の腰の間)
    "navel_normal": Preset("navel", "ふつう", (), {"op": "bump", "radius": 0.08, "height": -0.04, "edge": 0.5}),
    "navel_deep": Preset("navel", "深め", ("deep navel",), {"op": "bump", "radius": 0.09, "height": -0.07, "edge": 0.6}),
    "belly_flat": Preset("belly", "平ら", ("flat stomach", "toned"), {"op": "bump", "radius": 0.9, "height": -0.05, "edge": 0.0}),
    "belly_plump": Preset("belly", "ぽっちゃり", ("plump", "chubby"), {"op": "bump", "radius": 0.9, "height": 0.1, "edge": 0.0}),
}


def _words(text: str) -> set[str]:
    """タグの並び(カンマ区切り。重みの 1.2::tag:: や () は外す)を、小文字のタグの集まりにする。"""
    words = set()
    for tag in text.split(","):
        tag = re.sub(r"^-?[\d.]+::|::$|[()\[\]{}]", "", tag.strip()).strip().lower()
        if tag:
            words.add(tag.replace("_", " "))
    return words


def allowed(part: str, *, nude: bool, modest: bool) -> bool:
    """この部位を、このモデルに当てられるか(nude: 裸の素体のモデル、modest: 控えめに描くキャラ)。"""
    spec = PARTS[part]
    if spec.nude_only and not nude:
        return False
    return not (spec.adult_only and modest)


def presets_for(*, nude: bool, modest: bool) -> dict[str, Preset]:
    """このモデルに当てられる形の全部(シェイプキーにするもの)。"""
    return {key: p for key, p in PRESETS.items() if allowed(p.part, nude=nude, modest=modest)}


def choose(tags: str, *, nude: bool, modest: bool) -> dict[str, str]:
    """部位 → 既定の形。タグに合う形があればそれ、無ければ部位の最初の形(ふつう・そのまま)。"""
    words = _words(tags)
    result: dict[str, str] = {}
    for key, preset in presets_for(nude=nude, modest=modest).items():
        if preset.part not in result:
            result[preset.part] = key
        if any(tag in words for tag in preset.tags):
            result[preset.part] = key
    return result


def to_json(*, nude: bool, modest: bool) -> dict:
    """rig_model.py に渡す形の一覧(部位ごとの左右の有無と、形の値)。"""
    presets = presets_for(nude=nude, modest=modest)
    return {
        "parts": {k: {"sides": p.sides} for k, p in PARTS.items() if allowed(k, nude=nude, modest=modest)},
        "presets": {k: {"part": p.part, **p.params} for k, p in presets.items()},
    }


def labels(*, nude: bool, modest: bool) -> dict:
    """画面に出す名前(部位 → 名前と、選べる形)。"""
    presets = presets_for(nude=nude, modest=modest)
    return {
        part: {"label": spec.label, "presets": {k: p.label for k, p in presets.items() if p.part == part}}
        for part, spec in PARTS.items() if allowed(part, nude=nude, modest=modest)
    }
