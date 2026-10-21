"""
コマの絵の背景を消す(前面に重ねたコマで、人物だけを見せるため)。

切り抜きの形(マスク。白が人物)は、コマの絵と同じ場所に `<絵の名前>.cutout-<方法>.png` で残す。
コマを描き直すと絵のファイル名が変わるので、古いマスクは使われない。
- local: この PC でアニメ絵の人物を切り分ける(anime_seg、無料)。合成のときに無ければ作る
- novelai: NovelAI のディレクターツール(背景の削除)。1枚 65 Anlas 前後で Opus でも無料にならないので、
  画面で選んだときだけ作る。あれば local より優先する
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

METHODS = ("novelai", "local")
# 人物らしさ(0〜1)を不透明度にするときの範囲。境目を少しだけぼかす
_LOW, _HIGH = 0.3, 0.7


def mask_path(image_path: Path, method: str) -> Path:
    return image_path.with_name(f"{image_path.stem}.cutout-{method}.png")


def local_mask(image_path: Path) -> Image.Image:
    """この PC で人物を切り分けたマスクを作って残す(既にあればそれ)。"""
    path = mask_path(image_path, "local")
    if path.is_file():
        with Image.open(path) as cached:
            return cached.convert("L")
    from .. import anime_seg

    with Image.open(image_path) as source:
        person = anime_seg.person_mask(source.convert("RGB"))
    alpha = np.clip((person - _LOW) / (_HIGH - _LOW), 0.0, 1.0)
    mask = Image.fromarray((alpha * 255).astype(np.uint8), "L")
    mask.save(path)
    return mask


def mask_from_novelai(images: list[Image.Image], size: tuple[int, int]) -> Image.Image:
    """
    NovelAI の背景の削除の結果から、マスクを作る。透明な所のある画像があればその不透明度、無ければ白黒の画像
    (人物が白)をマスクとして使う。どちらでもなければ ValueError。
    """
    for image in images:
        if image.mode in ("RGBA", "LA") or "transparency" in image.info:
            alpha = image.convert("RGBA").getchannel("A")
            if alpha.getextrema()[0] < 250:
                return alpha.resize(size, Image.Resampling.LANCZOS)
    for image in images:
        gray = np.asarray(image.convert("L"), dtype=np.uint8)
        # ほとんどの画素が黒か白なら、白黒のマスク
        if np.mean((gray < 30) | (gray > 225)) > 0.9 and 0.02 < np.mean(gray > 127) < 0.98:
            return Image.fromarray(gray, "L").resize(size, Image.Resampling.LANCZOS)
    raise ValueError("NovelAI の結果から切り抜きの形を作れませんでした。")


def save_mask(image_path: Path, method: str, mask: Image.Image) -> Path:
    path = mask_path(image_path, method)
    mask.convert("L").save(path)
    return path


def best_mask(image_path: Path) -> Image.Image:
    """合成で使うマスク: NovelAI で作ったものがあればそれ、無ければこの PC で作る。"""
    novelai = mask_path(image_path, "novelai")
    if novelai.is_file():
        with Image.open(novelai) as mask:
            return mask.convert("L")
    return local_mask(image_path)
