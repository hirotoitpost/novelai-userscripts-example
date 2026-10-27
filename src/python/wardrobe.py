"""
衣装(着せ替え): キャラごとに服を何着も持たせ、1着を普段の服装(キャラシートの outfit_tags)にする。

服は部位(上着・トップス・ボトムス・一式・水着・靴下・靴・頭・小物)ごとに、部品(DB の wardrobe_parts)から
種類を選び、色・柄を付け、自由入力のタグを足して組み立てる。部品・色・柄の選択肢は衣装の設定のページで編集する。
組み立てたタグは、普段の服装・漫画の場面の服装・素体の Aポーズに服を着せる絵(body-outfit)に使う。
"""

from __future__ import annotations

from typing import Any

from .character_sheet import join_tags, split_tags

# 部位(画面の並び)。color / pattern は服でなく、ほかの部位に付ける選択肢
SLOTS: dict[str, str] = {
    "dress": "一式(制服・ワンピース など)",
    "outer": "上着",
    "top": "トップス",
    "bottom": "ボトムス",
    "swimwear": "水着",
    "legwear": "靴下・タイツ",
    "footwear": "靴",
    "headwear": "帽子",
    "accessory": "小物",
}
MODIFIERS: dict[str, str] = {"color": "色", "pattern": "柄"}
PART_KINDS = {**SLOTS, **MODIFIERS}

# はじめの部品(部位 → (値, 画面の名前, タグ, 色を付けられるか))。初めて表を作るときだけ入れる
DEFAULT_PARTS: dict[str, list[tuple[str, str, str, bool]]] = {
    "dress": [
        ("sailor_uniform", "セーラー服", "serafuku, sailor collar, pleated skirt", True),
        ("blazer_uniform", "ブレザーの制服", "school uniform, blazer, pleated skirt", True),
        ("dress", "ワンピース", "dress", True),
        ("sundress", "サンドレス", "sundress", True),
        ("gym_uniform", "体操服", "gym uniform, gym shirt, shorts", False),
        ("yukata", "浴衣", "yukata", True),
        ("kimono", "着物", "kimono", True),
        ("pajamas", "パジャマ", "pajamas", True),
        ("overalls", "オーバーオール", "overalls", True),
        ("maid", "メイド服", "maid, maid apron, maid headdress", False),
    ],
    "outer": [
        ("blazer", "ブレザー", "blazer", True),
        ("cardigan", "カーディガン", "cardigan", True),
        ("hoodie", "パーカー", "hoodie", True),
        ("jacket", "ジャケット", "jacket", True),
        ("track_jacket", "ジャージの上", "track jacket", True),
        ("denim_jacket", "デニムジャケット", "denim jacket", False),
        ("coat", "コート", "coat", True),
    ],
    "top": [
        ("shirt", "シャツ", "collared shirt", True),
        ("blouse", "ブラウス", "blouse", True),
        ("tshirt", "Tシャツ", "t-shirt", True),
        ("sweater", "セーター", "sweater", True),
        ("turtleneck", "タートルネック", "turtleneck sweater", True),
        ("sweater_vest", "ベスト", "sweater vest", True),
        ("polo", "ポロシャツ", "polo shirt", True),
        ("tank_top", "タンクトップ", "tank top", True),
    ],
    "bottom": [
        ("pleated_skirt", "プリーツスカート", "pleated skirt", True),
        ("long_skirt", "ロングスカート", "long skirt", True),
        ("miniskirt", "ミニスカート", "miniskirt", True),
        ("jeans", "ジーンズ", "jeans", False),
        ("pants", "パンツ", "pants", True),
        ("shorts", "ショートパンツ", "shorts", True),
        ("track_pants", "ジャージの下", "track pants", True),
        ("hakama", "袴", "hakama skirt", True),
    ],
    "swimwear": [
        ("bikini", "ビキニ", "bikini", True),
        ("frilled_bikini", "フリルのビキニ", "frilled bikini", True),
        ("one_piece_swimsuit", "ワンピースの水着", "one-piece swimsuit", True),
        ("tankini", "タンキニ+水着の短パン", "tankini, swim shorts", True),
        ("competition", "競泳水着", "competition swimsuit", True),
        ("rash_guard", "ラッシュガード+水着の短パン", "rash guard, swim shorts", True),
    ],
    "legwear": [
        ("socks", "靴下", "socks", True),
        ("kneehighs", "ハイソックス", "kneehighs", True),
        ("thighhighs", "ニーハイ", "thighhighs", True),
        ("pantyhose", "タイツ", "pantyhose", True),
        ("loose_socks", "ルーズソックス", "loose socks", False),
    ],
    "footwear": [
        ("loafers", "ローファー", "loafers", True),
        ("sneakers", "スニーカー", "sneakers", True),
        ("boots", "ブーツ", "boots", True),
        ("sandals", "サンダル", "sandals", True),
        ("high_heels", "ハイヒール", "high heels", True),
        ("uwabaki", "上履き", "uwabaki", False),
        ("barefoot", "はだし", "barefoot", False),
    ],
    "headwear": [
        ("sun_hat", "麦わら帽子", "straw hat", False),
        ("baseball_cap", "キャップ", "baseball cap", True),
        ("beret", "ベレー帽", "beret", True),
        ("beanie", "ニット帽", "beanie", True),
    ],
    "accessory": [
        ("bowtie", "蝶ネクタイ", "bowtie", True),
        ("necktie", "ネクタイ", "necktie", True),
        ("neck_ribbon", "リボン(首)", "neck ribbon", True),
        ("scarf", "マフラー", "scarf", True),
        ("apron", "エプロン", "apron", True),
        ("bag", "かばん", "shoulder bag", True),
        ("gloves", "手袋", "gloves", True),
    ],
    "color": [
        ("white", "白", "white", False), ("black", "黒", "black", False), ("grey", "グレー", "grey", False),
        ("navy", "紺", "navy blue", False), ("blue", "青", "blue", False), ("light_blue", "水色", "light blue", False),
        ("red", "赤", "red", False), ("pink", "ピンク", "pink", False), ("purple", "紫", "purple", False),
        ("yellow", "黄", "yellow", False), ("green", "緑", "green", False), ("brown", "茶", "brown", False),
        ("beige", "ベージュ", "beige", False), ("orange", "オレンジ", "orange", False),
    ],
    "pattern": [
        ("striped", "しま", "striped", False), ("plaid", "チェック", "plaid", False),
        ("polka_dot", "水玉", "polka dot", False), ("floral", "花柄", "floral print", False),
        ("checkered", "市松", "checkered", False), ("frilled", "フリル", "frilled", False),
    ],
}

Catalog = dict[str, dict[str, dict[str, Any]]]


def catalog_map(rows: list[dict[str, Any]]) -> Catalog:
    """部品の表: 部位 → 値 → 部品(label・tags・colorable)。"""
    result: Catalog = {}
    for row in rows:
        result.setdefault(row["slot"], {})[row["value"]] = row
    return result


def part_tags(part: dict[str, Any], catalog: Catalog) -> str:
    """
    1つの部品のタグ。色・柄は最初のタグの前に付ける(navy + plaid + pleated skirt → navy plaid pleated skirt)。
    色を付けられない部品(体操服・はだし など)と、表に無い値は色・柄を付けない(表に無い部品は何も入れない)。
    """
    item = catalog.get(part.get("slot") or "", {}).get(part.get("value") or "")
    if item is None:
        return ""
    tags = split_tags(item["tags"])
    if not tags:
        return ""
    if item.get("colorable"):
        words = [
            catalog.get(kind, {}).get(part.get(kind) or "", {}).get("tags", "")
            for kind in ("color", "pattern")
        ]
        prefix = " ".join(w.strip() for w in words if w and w.strip())
        if prefix:
            tags[0] = f"{prefix} {tags[0]}"
    return ", ".join(tags)


def compose(parts: list[dict[str, Any]], extra_tags: str, catalog: Catalog) -> str:
    """衣装のタグ(部品を部位の並び順に、自由入力のタグを最後に)。"""
    order = list(SLOTS)
    ordered = sorted(
        (p for p in parts if p.get("slot") in SLOTS),
        key=lambda p: order.index(p["slot"]),
    )
    return join_tags(*(part_tags(p, catalog) for p in ordered), extra_tags)
