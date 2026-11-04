"""
アニメ塗り(3D の形を動かして描く。scripts/blender/anime_render.py)の色: キャラの絵から、部位ごとの色(明るい所・ふつう・影の
3 段)を取る。

正解はキャラの絵: キャラシートの参照画像と、データセットの顔と表情のシート(白い背景で光がそろっている)。参照画像 1 枚
だけでは、その絵の光(海辺の日差し・ほおの赤み)に色が引っ張られた(2026-11 矢野栄子)ので、何枚もの絵の画素を集めて段を
決める。漫画のコマはほとんどがスクリーントーンの白黒なので、色には使わない(線は濃く、影は 2〜3 段で、ぼかさない塗り方を
合わせた)。

部位は体の部位分け(body_parsing。1 枚 20 秒ほど)で分ける: 肌は顔と首(目・口・鼻・髪・ほおの赤みを除く)、髪、上の服・
下の服(肌の色に近い画素を除く)。線の色は、髪のいちばん暗い画素。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

_LUM = np.array([0.299, 0.587, 0.114])
# データセットの顔と表情のシートのうち、落ち着いた表情の絵(赤み・涙・影の強い表情は色がずれる)
FACE_SHEET_VIEWS = ("front", "three_quarter", "side", "expressionless", "relaxed", "smile", "determined", "sleepy")
MAX_IMAGES = 9
# 服の部位が見つからないときの色(白に近い薄い灰色)
GARMENT = [[246, 246, 248], [226, 227, 234], [178, 176, 196]]
LINES = [30, 20, 20]


class ToonError(RuntimeError):
    """キャラの絵から色を取れなかった。"""


def sources(reference: Path | None, dataset_dirs: list[Path]) -> list[Path]:
    """色を取る絵: 参照画像と、データセットの顔と表情のシートの落ち着いた表情(無ければ素体の正面・データセットの絵)。"""
    paths = [reference] if reference is not None and reference.is_file() else []
    for folder in dataset_dirs:
        sheet = folder / "face_sheet"
        paths += [sheet / f"{view}.png" for view in FACE_SHEET_VIEWS if (sheet / f"{view}.png").is_file()]
    if len(paths) < 3:
        for folder in dataset_dirs:
            paths += sorted(folder.glob("body_base/*_front.png")) + sorted(folder.glob("generated*/*.png"))[:4]
    return list(dict.fromkeys(paths))[:MAX_IMAGES]


def tones(pixels: np.ndarray, count: int = 3) -> list[list[float]]:
    """画素を明るさの分位で count 段に分けた、それぞれの中央値の色(明るい順)。いちばん暗い 5%・明るい 3%(線・白とび)は外す。"""
    lum = pixels @ _LUM
    lo, hi = np.percentile(lum, [5, 97])
    keep = pixels[(lum >= lo) & (lum <= hi)]
    lum = keep @ _LUM
    edges = np.percentile(lum, np.linspace(0, 100, count + 1))
    result = []
    for a, b in zip(edges[:-1], edges[1:]):
        chosen = keep[(lum >= a) & (lum <= b)]
        result.append([round(float(x), 1) for x in np.median(chosen, axis=0)])
    return result[::-1]


def _between(a: list[float], b: list[float]) -> list[float]:
    return [round((x + y) / 2, 1) for x, y in zip(a, b)]


def _blush(pixels: np.ndarray) -> np.ndarray:
    """ほおの赤み(赤が緑より大きく勝つ所)。"""
    return (pixels[:, 0] - pixels[:, 1]) > 70


def extract(paths: list[Path], parse: Callable[[Image.Image], dict[str, np.ndarray]] | None = None) -> dict[str, Any]:
    """絵から部位ごとの色を取る(palette)。parse は部位分け(省くと body_parsing.parse)。"""
    if parse is None:
        from . import body_parsing

        if not body_parsing.available():
            raise ToonError("体の部位分けのモデルがありません(scripts/export_body_parsing.py で作ってください)。")
        parse = body_parsing.parse
    found: dict[str, list[np.ndarray]] = {"skin": [], "hair": [], "lines": [], "topwear": [], "bottomwear": []}
    used = []
    for path in paths:
        image = Image.open(path).convert("RGB")
        parts = parse(image)
        rgb = np.asarray(image).astype(float)
        skin = (parts["face"] | parts["neck"]) & ~(parts["eyes"] | parts["mouth"] | parts["nose"] | parts["hair"])
        if skin.sum() < 500 or parts["hair"].sum() < 500:
            continue
        used.append(path)
        pixels = rgb[skin]
        found["skin"].append(pixels[~_blush(pixels)])
        hair = rgb[parts["hair"]]
        found["hair"].append(hair)
        found["lines"].append(hair[hair @ _LUM <= np.percentile(hair @ _LUM, 2)])
        for key in ("topwear", "bottomwear"):
            if parts[key].sum() > 2000:
                found[key].append(rgb[parts[key]])
    if not used:
        raise ToonError("色を取れる絵がありません(顔と髪の見える、参照画像かデータセットの顔のシートが要ります)。")
    skin = tones(np.concatenate(found["skin"]))
    hair = tones(np.concatenate(found["hair"]))
    palette: dict[str, Any] = {
        "skin": skin,
        # 髪の影は、分位のいちばん暗い段だと線に寄るので、ふつうとの間にする
        "hair": [hair[0], hair[1], _between(hair[1], hair[2])],
        "shine": [round(min(255.0, x * 1.12 + 18), 1) for x in hair[0]],
        "lines": [round(float(x), 1) for x in np.median(np.concatenate(found["lines"]), axis=0)],
        "garments": {},
        "garment": GARMENT,
        "sources": [p.name for p in used],
    }
    for key, kind in (("topwear", "top"), ("bottomwear", "skirt")):
        if found[key]:
            pixels = np.concatenate(found[key])
            # 服の範囲に入った肌(胸元・腕。影の色も)を除く
            near_skin = np.zeros(len(pixels), bool)
            for tone in skin:
                near_skin |= np.abs(pixels - np.array(tone)).sum(axis=1) < 90
            if (~near_skin).sum() > 2000:
                hi, base, shadow = tones(pixels[~near_skin])
                # 服は明るい所を作らない(服の面の細かいでこぼこで、明るい所がまだらになった)。影は、いちばん暗い段だと
                # 黒に近くなるので、ふつうとの間にする(髪と同じ)
                palette["garments"][kind] = [base, base, _between(base, shadow)]
    return palette


def face_decal(
    image_path: Path, out_path: Path | None = None,
    parse: Callable[[Image.Image], dict[str, np.ndarray]] | None = None,
) -> dict[str, Any]:
    """
    顔に映す正面の絵: 人物の範囲(背景と違う所)と、顔の肌の色(人物の上 12% の明るい画素の中央値)。
    out_path があれば、顔の内側(体の部位分けの顔・目・口・鼻を、人物の高さの 1.2% だけ内へ縮めた所)だけを残し、外を
    肌の色で埋めた絵をそこに書いて、それを映す。3D と絵のあごの位置が少しずれるので、そのままでは絵のあごの下の影・
    あごの線が首に黒く映った(2026-11 矢野栄子)。部位分けのモデルが無ければ、元の絵をそのまま映す。
    """
    image = Image.open(image_path).convert("RGB")
    rgb = np.asarray(image).astype(float)
    edge = np.concatenate([rgb[:20].reshape(-1, 3), rgb[-20:].reshape(-1, 3)])
    person = np.abs(rgb - np.median(edge, axis=0)).sum(axis=2) > 30
    ys, xs = np.nonzero(person)
    if len(ys) == 0:
        raise ToonError("顔に映す絵に、人物が見つかりません。")
    top = person.copy()
    top[int(ys.min() + (ys.max() - ys.min()) * 0.12):] = False
    pixels = rgb[top]
    lum = pixels @ _LUM
    skin = np.median(pixels[lum >= np.percentile(lum, 40)], axis=0)
    if out_path is not None and parse is None:
        from . import body_parsing

        parse = body_parsing.parse if body_parsing.available() else None
    if out_path is not None and parse is not None:
        import cv2

        parts = parse(image)
        face = parts["face"] | parts["eyes"] | parts["mouth"] | parts["nose"]
        radius = max(2, round((ys.max() - ys.min()) * 0.012))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
        keep = cv2.erode(face.astype(np.uint8), kernel) > 0
        cleaned = np.where(keep[..., None], rgb, skin[None, None, :])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(cleaned.round().astype(np.uint8)).save(out_path)
        image_path = out_path
    return {
        "image": str(image_path.resolve()),
        "bbox": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
        "size": [rgb.shape[1], rgb.shape[0]],
        "skin": [round(float(x), 1) for x in skin],
    }
