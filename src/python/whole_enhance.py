"""
ギャラリーの画像全体の画質を上げる(拡大表示の範囲だけを対象にする region_redraw.py の、画像全体版)。

- upscale: この PC で AI 拡大(upscaler.py。アニメ向けの超解像)を画像全体にかける。絵の中身は変えない。2倍か4倍
- redraw: 1MP 以内に収めて NovelAI の img2img で弱めに描き直し(線の崩れ・ノイズを直す)、続けて AI 拡大する

結果の大きさは長い辺 MAX_SIDE まで。AI 拡大はタイルに分けて CPU で回すので、1MP あたり2分ほどかかる。
入力が AI_MAX_INPUT より大きい画像は、先に縮めてから拡大する(もう十分大きいので、時間に見合わない)。
"""

from __future__ import annotations

import math
from typing import Any

from .region_redraw import MAX_PIXELS, MIN_GAIN, RegionError, _snap

MAX_SIDE = 4096
AI_MAX_INPUT = 2 * 1024 * 1024
SCALES = (2, 4)


def upscale_target(width: int, height: int, scale: int) -> tuple[int, int]:
    """拡大した大きさ(縦横 scale 倍、長い辺は MAX_SIDE まで)。ほとんど大きくならなければ RegionError。"""
    zoom = min(float(scale), MAX_SIDE / max(width, height))
    if zoom < MIN_GAIN:
        raise RegionError(f"この画像は十分大きいので、これ以上は大きくしません(長い辺 {MAX_SIDE}px まで)")
    return max(1, round(width * zoom)), max(1, round(height * zoom))


def redraw_size(width: int, height: int) -> tuple[int, int]:
    """描き直す大きさ(縦横比を保って 1MP 以内・64 の倍数。小さい画像は大きく、大きい画像は小さくする)。"""
    zoom = math.sqrt(MAX_PIXELS / (width * height))
    return max(64, int(width * zoom) // 64 * 64), max(64, int(height * zoom) // 64 * 64)


def whole_characters(settings: dict[str, Any] | None) -> list[dict[str, Any]]:
    """元の画像のキャラごとのプロンプト(位置を指定して描いた画像なら、その位置も)。"""
    result = []
    for c in (settings or {}).get("characters") or []:
        if not str(c.get("prompt") or "").strip():
            continue
        character: dict[str, Any] = {"prompt": c["prompt"], "negative_prompt": c.get("negative") or ""}
        if (settings or {}).get("use_coords") and c.get("x") is not None and c.get("y") is not None:
            character["position"] = (_snap(float(c["x"])), _snap(float(c["y"])))
        result.append(character)
    return result


def suggestion(width: int, height: int) -> str:
    """画像の大きさから、どちらの方法が合うかの目安。"""
    if max(width, height) * MIN_GAIN > MAX_SIDE:
        return "十分大きい画像です。これ以上は大きくできません"
    if width * height < 512 * 512:
        return "小さい画像です。まず「AI拡大」を試してください(絵は変わりません)"
    return "線の崩れやノイズが気になるときは「描き直し」、ぼやけているだけなら「AI拡大」が向いています"
