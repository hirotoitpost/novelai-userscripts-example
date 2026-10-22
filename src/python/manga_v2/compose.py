"""コマ画像をテンプレートに嵌め込み、吹き出しと描き文字を置いてページ画像にする。"""

from __future__ import annotations

import io
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .detect import detect_heads
from .overlay import OverlayPiece, draw_pieces
from .speakers import CastMember, identify_heads
from .layout import (
    PAGE_HEIGHT,
    PAGE_WIDTH,
    PageLayout,
    PageSpec,
    PanelShape,
    Rect,
    fill_template,
    layout_page,
    layout_size,
    panel_rects,
    rect_shape,
    template_page,
)
from .lettering import (
    SFX_MIN_SIZE,
    bubble_shape,
    draw_stamp,
    TextBlock,
    bubble_size,
    draw_bubble,
    draw_narration,
    draw_sfx,
    fit_sfx,
    fit_text,
    narration_size,
)

_BORDER = 4
_BUBBLE_MARGIN = 14
# セリフの文字サイズ(ページ幅1200px基準)。長いセリフは最小サイズまで縮める。
_TEXT_SIZE = 26
_TEXT_MIN_SIZE = 16
# 1列の最大文字数。長い列は読みにくく吹き出しも細長くなるので、超える分は次の列へ送る。
_MAX_ROWS = 11
# ナレーションの文字サイズと、コマ幅に対する枠の最大幅
_NARRATION_SIZE = 22
_NARRATION_MAX_WIDTH = 0.4
# 1コマに置く吹き出しの上限。超えた分は最後の吹き出しにまとめる。
_MAX_BUBBLES = 4
# 切り抜きの縦位置(0=上端, 0.5=中央)。人物の頭が切れにくいよう上寄りにする。
_CROP_VERTICAL_BIAS = 0.25


@dataclass
class PanelContent:
    image_path: Path | None
    dialogue: list[str] = field(default_factory=list)
    # 吹き出しを使わず描き文字にする効果音・オノマトペ
    sfx: list[str] = field(default_factory=list)
    # セリフが多いシーンを分けたときの2コマ目以降の番号。同じ絵を寄りで切り抜く(0は通常)。
    zoom_step: int = 0
    # 手動配置(ドラッグ)の保存キーの元。シーンIDを入れる。
    key: str = ""
    # ナレーション枠の文(空なら出さない)
    narration: str = ""
    # セリフごとの話し手のキャラID(dialogue と同じ並び。不明は None)。空なら話し手を使わない
    speakers: list[int | None] = field(default_factory=list)
    # シーンに登場するキャラ。絵の中の頭がどのキャラかを髪の色で見分けるのに使う
    cast: list[CastMember] = field(default_factory=list)
    # 寄りのコマで写すキャラ(そのコマで話す人。二人なら二人とも入るように寄る)
    focus: list[int] = field(default_factory=list)
    # 動くコマ(うごイラ)の、いま貼るコマの絵。頭の検出・話し手の見分け・寄りの切り抜きは image_path(元の絵)で
    # 決め、貼る絵だけこれに替える(コマごとに吹き出しの位置が揺れないように)
    picture_path: Path | None = None
    # コマの絵に重ねるもの(文字・別のコマの絵)。overlay.py
    overlays: list[OverlayPiece] = field(default_factory=list)


@dataclass
class Element:
    """ページ上に置いた吹き出し/描き文字。画面でドラッグして位置を直すために返す。"""

    key: str
    kind: str  # "bubble" | "sfx" | "narration"
    text: str
    box: Rect
    panel: Rect


@dataclass
class LetteringStyle:
    font_path: Path
    sfx_font_path: Path
    # 吹き出しの白い地の不透明度(0で輪郭線だけ)
    bubble_opacity: float = 1.0
    # 手動配置: Element.key → コマ内での左上の位置(コマの幅・高さに対する割合)
    overrides: dict[str, tuple[float, float]] = field(default_factory=dict)
    # 効果音の文字列ごとのフォント(無ければ sfx_font_path)
    sfx_font_paths: dict[str, Path] = field(default_factory=dict)
    # 効果音の文字列ごとのスタンプ画像。フォントより優先する。
    sfx_stamps: dict[str, Path] = field(default_factory=dict)
    # 文字の大きさの倍率。text_scale はセリフ・ナレーション、sfx_scale は描き文字とスタンプの全体設定、
    # scales は Element.key ごとの個別の調整(画面で選んで大きく/小さくしたもの)
    text_scale: float = 1.0
    sfx_scale: float = 1.0
    scales: dict[str, float] = field(default_factory=dict)


# 寄りのコマの拡大率(1段ごと)と上限、切り抜く中心(横は段ごとに左右へ振る)
_ZOOM_PER_STEP = 0.45
_MAX_ZOOM = 2.2
_ZOOM_FOCUS_X = (0.5, 0.5, 0.42, 0.58)
_ZOOM_FOCUS_Y = 0.35
# 二人に寄るとき、二人の頭の左右の幅がコマの幅のこの割合に収まるところまでで止める
_TWO_SHOT_FILL = 0.85


def _cover(
    img: Image.Image,
    width: int,
    height: int,
    zoom_step: int = 0,
    heads: list[Rect] | None = None,
    focus_indexes: list[int] | None = None,
) -> tuple[Image.Image, float, int, int]:
    """
    縦横比を保ったまま width×height を覆うよう拡大し、はみ出しを切り落とす。
    zoom_step > 0 なら更に拡大して寄りにする。頭が見つかっていればその辺りを、無ければ
    上寄りの中央付近(人物の顔がありやすい)を切り抜く。寄りの段ごとに別の頭を中心にする。
    focus_indexes を渡すと、その頭(そのコマで話す人)を中心にする。二つ以上なら、全員が
    コマに収まるところまでで寄るのを止める(片方だけに寄ると、もう一人のセリフが宙に浮く)。

    戻り値は (切り抜いた画像, 拡大率, 切り抜きの左端, 上端)。元画像の座標をコマの座標へ
    直すのに使う(コマ座標 = 元座標 × 拡大率 − 左端/上端)。
    """
    zoom = min(1 + _ZOOM_PER_STEP * zoom_step, _MAX_ZOOM)
    base = max(width / img.width, height / img.height)
    focused = [heads[i] for i in focus_indexes or []] if heads else []
    if len(focused) > 1:
        span = max(h[2] for h in focused) - min(h[0] for h in focused)
        zoom = max(1.0, min(zoom, width * _TWO_SHOT_FILL / (span * base)))
    scale = base * zoom
    resized = img.resize((max(round(img.width * scale), width), max(round(img.height * scale), height)))
    if zoom_step == 0:
        left = (resized.width - width) // 2
        top = round((resized.height - height) * _CROP_VERTICAL_BIAS)
    else:
        if heads:
            if focused:
                hx0, hy0 = min(h[0] for h in focused), min(h[1] for h in focused)
                hx1, hy1 = max(h[2] for h in focused), max(h[3] for h in focused)
            else:
                hx0, hy0, hx1, hy1 = heads[(zoom_step - 1) % len(heads)]
            fx = (hx0 + hx1) / 2 * scale
            fy = (hy0 + hy1) / 2 * scale + height * 0.1  # 顔の少し下を中心にして頭上を空ける
        else:
            fx = resized.width * _ZOOM_FOCUS_X[zoom_step % len(_ZOOM_FOCUS_X)]
            fy = resized.height * _ZOOM_FOCUS_Y
        left = min(max(round(fx - width / 2), 0), resized.width - width)
        top = min(max(round(fy - height / 2), 0), resized.height - height)
    return resized.crop((left, top, left + width, top + height)), scale, left, top


def split_dense_panels(panels: list[PanelContent], max_lines: int) -> list[PanelContent]:
    """
    セリフが max_lines を超えるシーンを複数のコマに分ける(0なら分けない)。
    分けたコマには同じ絵の寄りを使うので、追加の生成は要らない。効果音は最初のコマに置く。
    """
    if max_lines <= 0:
        return panels
    result: list[PanelContent] = []
    for panel in panels:
        pairs = [(line, speaker) for line, speaker in zip(panel.dialogue, _padded(panel)) if line.strip()]
        if len(pairs) <= max_lines:
            result.append(panel)
            continue
        for step, start in enumerate(range(0, len(pairs), max_lines)):
            chunk = pairs[start : start + max_lines]
            speakers = [speaker for _, speaker in chunk]
            known = [s for s in speakers if s is not None]
            result.append(
                PanelContent(
                    panel.image_path,
                    [line for line, _ in chunk],
                    panel.sfx if step == 0 else [],
                    zoom_step=step,
                    key=panel.key,
                    narration=panel.narration if step == 0 else "",
                    speakers=speakers if panel.speakers else [],
                    cast=panel.cast,
                    # 寄りのコマは、そのコマで話す人(二人なら二人とも入るよう)に寄せる
                    focus=list(dict.fromkeys(known)) if step > 0 else [],
                )
            )
    return result


def _padded(panel: PanelContent) -> list[int | None]:
    """dialogue と同じ長さの話し手の並び(話し手が無ければ全部 None)。"""
    return (panel.speakers + [None] * len(panel.dialogue))[: len(panel.dialogue)]


def _overlaps(a: Rect, b: Rect) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _overlap_area(a: Rect, b: Rect) -> int:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(w, 0) * max(h, 0)


# 話し手に寄せる置き方の重み: 別の人の吹き出しから離れる距離の重み(話し手への近さを1として)と、
# 下の段を使うときの不利(コマの幅に対する割合。吹き出しはなるべく上に置く)
_RIVAL_GAP_WEIGHT = 0.6
_LOWER_ROW_COST = 0.3


def _reads_after(candidate: Rect, earlier: list[Rect]) -> bool:
    """
    先に置いた吹き出しより後に読まれる位置か。下の段にある吹き出しより上には置かず、
    同じ高さに並ぶなら左に置く(日本の漫画は上から、右から読む)。
    """
    for other in earlier:
        if other[1] >= candidate[3]:
            return False
        if candidate[1] < other[3] and other[1] < candidate[3] and candidate[2] > other[0] + _BUBBLE_MARGIN:
            return False
    return True


def _place(
    size: tuple[int, int],
    panel: Rect,
    placed: list[Rect],
    *,
    sfx: bool = False,
    narration: bool = False,
    heads: list[Rect] | None = None,
    prefer_x: float | None = None,
    earlier: list[Rect] | None = None,
    rivals: list[Rect] | None = None,
) -> tuple[Rect, bool]:
    """
    吹き出しの置き場所。日本の漫画は右から読むので、コマの上辺に沿って右から左へ、
    次に下辺に沿って右から左へ探し、既存の吹き出しと重ならない最初の位置に置く。
    どこにも空きが無ければ重なりが最小の位置にする。2つ目の戻り値は重ならずに置けたか。
    heads(絵の中の頭の位置)を渡すと、頭に被らない位置を優先し、被るしかなければ
    被る面積が最小の位置にする(この場合も「重ならずに置けた」には数えない)。

    描き文字(sfx=True)は吹き出しと取り合わないよう、コマの中ほど・左側から探す。
    ナレーション(narration=True)はコマの左上の角に置く(右上は吹き出しが使う)。空きが無ければ左下。

    prefer_x(話し手の頭の横位置)を渡すと、読む順(上の段から、同じ段は右から)を崩さない
    範囲で、話し手に近く、別の人の吹き出し(rivals)からは遠い位置を選ぶ。二人の吹き出しが
    並ぶと、どちらがどちらのセリフか分かりにくいため。earlier はこのコマで先に置いた吹き出し。
    """
    bw, bh = size
    px0, py0, px1, py1 = panel
    left_limit = px0 + _BUBBLE_MARGIN
    right_limit = px1 - _BUBBLE_MARGIN
    top_row, bottom_row = py0 + _BUBBLE_MARGIN, py1 - _BUBBLE_MARGIN - bh
    step = max(bw // 4, 8)
    candidates: list[Rect] = []
    if narration:
        # コマの枠にぴったり付ける(余白なし)のがナレーションらしい
        candidates = [(px0, py0, px0 + bw, py0 + bh), (px0, py1 - bh, px0 + bw, py1)]
    elif sfx:
        middle_row = (py0 + py1 - bh) // 2
        for top in (middle_row, bottom_row, top_row):
            x0 = left_limit
            while x0 + bw <= right_limit:
                candidates.append((x0, top, x0 + bw, top + bh))
                x0 += step
        if not candidates:
            candidates.append((left_limit, top_row, left_limit + bw, top_row + bh))
    else:
        for top in (top_row, bottom_row):
            x1 = right_limit
            while x1 - bw >= left_limit:
                candidates.append((x1 - bw, top, x1, top + bh))
                x1 -= step
            candidates.append((left_limit, top, left_limit + bw, top + bh))
    heads = heads or []
    free = [c for c in candidates if not any(_overlaps(c, other) for other in placed)]
    if prefer_x is not None and not sfx and not narration:
        # 話し手に寄せるときは上下の段の間の高さも候補にする(後に話す人の吹き出しを一段下げて、
        # 話し手の側に置けるように)
        between_rows = [
            (x, top, x + bw, top + bh)
            for top in range(top_row + max(bh // 3, 20), bottom_row, max(bh // 3, 20))
            for x in range(right_limit - bw, left_limit - 1, -step)
        ]
        spread = free + [c for c in between_rows if not any(_overlaps(c, other) for other in placed)]
        clear = [c for c in spread if _reads_after(c, earlier or []) and not any(_overlaps(c, head) for head in heads)]
        if clear:
            width = px1 - px0

            def cost(c: Rect) -> float:
                cx, cy = (c[0] + c[2]) / 2, (c[1] + c[3]) / 2
                gap = min(
                    (((cx - (r[0] + r[2]) / 2) ** 2 + (cy - (r[1] + r[3]) / 2) ** 2) ** 0.5 for r in rivals or []),
                    default=width,
                )
                lower = _LOWER_ROW_COST * width * (c[1] - top_row) / max(bottom_row - top_row, 1)
                return abs(cx - prefer_x) - _RIVAL_GAP_WEIGHT * min(gap, width) + lower

            return min(clear, key=cost), True
    for candidate in free:
        if not any(_overlaps(candidate, head) for head in heads):
            return candidate, True
    if free:
        return min(free, key=lambda c: sum(_overlap_area(c, head) for head in heads)), False
    return min(
        candidates,
        key=lambda c: (sum(_overlap_area(c, o) for o in placed), sum(_overlap_area(c, h) for h in heads)),
    ), False


def _covers_head(box: Rect, heads: list[Rect]) -> bool:
    """頭の面積の1割以上を覆っているか(縁を少しかすめる程度は気にしない)。"""
    return any(_overlap_area(box, h) > 0.1 * (h[2] - h[0]) * (h[3] - h[1]) for h in heads)


def _speaker_point(box: Rect, heads: list[Rect], default: tuple[float, float]) -> tuple[float, float]:
    """しっぽを向ける先: 吹き出しに一番近い頭の口元あたり。頭が無ければ既定の位置。"""
    if not heads:
        return default
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    hx0, hy0, hx1, hy1 = min(heads, key=lambda h: ((h[0] + h[2]) / 2 - cx) ** 2 + ((h[1] + h[3]) / 2 - cy) ** 2)
    return (hx0 + hx1) / 2, hy0 + (hy1 - hy0) * 0.7


def _offpanel_side(
    box: Rect,
    panel: Rect,
    heads: list[Rect],
    head_ids: list[int | None],
    offscreen: dict[int, tuple[float, float]],
    speaker: int | None,
) -> str | None:
    """
    話し手がコマに写っていないなら、声がしてくる側("left"/"right")。写っている(または
    写っているかもしれない)なら None。寄りで切れた人はその人の頭がある側、絵にいない人は
    吹き出しに近い側の縁にする。
    """
    if speaker is None or speaker in head_ids:
        return None
    if speaker not in offscreen and (not any(i is not None for i in head_ids) or any(i is None for i in head_ids)):
        # 誰とも見分けられていない頭があれば、それが話し手かもしれない
        return None
    middle = (panel[0] + panel[2]) / 2
    x = offscreen[speaker][0] if speaker in offscreen else (box[0] + box[2]) / 2
    return "left" if x < middle else "right"


def _tail_target(
    box: Rect,
    panel: Rect,
    heads: list[Rect],
    head_ids: list[int | None],
    speaker: int | None,
    side: str | None,
    default: tuple[float, float],
) -> tuple[float, float] | None:
    """
    しっぽを向ける先。写っていない人のセリフ(side あり)はしっぽを付けない(None)。どこへ向けても
    写っている人を指しているように見えてしまうため。話し手が写っていればその人の口元へ。
    話し手の頭が見分けられなければ、まだ誰とも見分けていない頭のうち近いものへ。話し手が
    分からないとき・頭を見分けられないときは従来どおり。
    """
    if side is not None:
        return None
    if speaker is None or not any(i is not None for i in head_ids):
        return _speaker_point(box, heads, default)
    own = [h for h, i in zip(heads, head_ids) if i == speaker]
    if own:
        return _speaker_point(box, own, default)
    unknown = [h for h, i in zip(heads, head_ids) if i is None]
    return _speaker_point(box, unknown or heads, default)


def _speaker_x(
    heads: list[Rect], head_ids: list[int | None], offscreen: dict[int, tuple[float, float]], speaker: int | None
) -> float | None:
    """吹き出しを寄せる横位置: 話し手の頭(寄りで切れていれば、切れた先)。分からなければ None。"""
    if speaker is None:
        return None
    for head, head_id in zip(heads, head_ids):
        if head_id == speaker:
            return (head[0] + head[2]) / 2
    return offscreen[speaker][0] if speaker in offscreen else None


def _to_side(box: Rect, panel: Rect, side: str, placed: list[Rect]) -> Rect:
    """写っていない人の吹き出しを、声がしてくる側の縁に寄せる(他の吹き出しと重なるなら動かさない)。"""
    width = box[2] - box[0]
    x0 = panel[0] + _BUBBLE_MARGIN if side == "left" else panel[2] - _BUBBLE_MARGIN - width
    moved = (x0, box[1], x0 + width, box[3])
    return box if any(_overlaps(moved, other) for other in placed) else moved


def _fit_bubble(
    text: str, panel: Rect, text_size: int = _TEXT_SIZE, shape: str = "ellipse", min_size: int = _TEXT_MIN_SIZE
) -> tuple[TextBlock, tuple[int, int]]:
    pw, ph = panel[2] - panel[0], panel[3] - panel[1]
    # 楕円の倍率と余白を見込んで、文字ブロックの上限をコマの大きさから決める。
    # 文字を大きくした分は横幅の上限も広げる(広げないと列が増えられず、結局縮んでしまう)
    widen = min(max(text_size / _TEXT_SIZE, 1.0), 1.6)
    max_text_w = int((pw * 0.45 * widen) / 1.45)
    max_text_h = min(int((ph - _BUBBLE_MARGIN * 2 - 20) / 1.3), text_size * _MAX_ROWS)
    block = fit_text(text, max_text_w, max_text_h, text_size, min_size)
    return block, bubble_size(block, shape)


# 文字の大きさの自動調整。基準は 2×2 テンプレートのコマ(約 540×790)で、大ゴマほど大きく、
# 小さいコマほど小さくする(面積の平方根に比例、範囲は 0.85〜1.3 倍)。
_REFERENCE_PANEL_AREA = 540 * 790
_PANEL_FACTOR_RANGE = (0.85, 1.3)
# 吹き出しの形ごとの補正: 叫びは大きく、小声は小さく
_SHAPE_TEXT_FACTOR = {"burst": 1.15, "whisper": 0.85}


def _panel_factor(panel: Rect) -> float:
    area = (panel[2] - panel[0]) * (panel[3] - panel[1])
    low, high = _PANEL_FACTOR_RANGE
    return min(max((area / _REFERENCE_PANEL_AREA) ** 0.5, low), high)


# 頭に被らざるを得ない吹き出しの、白い地の不透明度の上限(絵が透けて見えるようにする)
_OVER_HEAD_OPACITY = 0.55


# 空きが無いときに文字を小さくして置き直す刻み
_SHRINK_STEP_TEXT = 2
_SHRINK_STEP_SFX = 8


def _overridden_box(size: tuple[int, int], panel: Rect, position: tuple[float, float]) -> Rect:
    """手動配置の位置(コマに対する割合)を、コマからはみ出さないページ座標にする。"""
    x0, y0, x1, y1 = panel
    bw, bh = size
    left = min(max(round(x0 + position[0] * (x1 - x0)), x0), max(x1 - bw, x0))
    top = min(max(round(y0 + position[1] * (y1 - y0)), y0), max(y1 - bh, y0))
    return left, top, left + bw, top + bh


# スタンプの大きさ(コマの高さ・幅に対する上限)。空きが無ければこの刻みで縮める。
_STAMP_MAX_H = 0.55
_STAMP_MAX_W = 0.4
_STAMP_SHRINK = 0.85
_STAMP_MIN_H = 0.2


def _place_stamp(
    natural: tuple[int, int],
    panel: Rect,
    placed: list[Rect],
    override: tuple[float, float] | None,
    heads: list[Rect] | None = None,
    scale: float = 1.0,
    fixed: bool = False,
    anchor: Rect | None = None,
) -> Rect:
    """
    スタンプの縦横比のまま、コマに収まる大きさで描き文字と同じ探し方で置き場所を決める。
    scale は大きさの倍率。fixed(個別に大きさを決めた)なら空きが無くても縮めない。
    anchor は手動配置の基準(コマの外接矩形。無ければ panel)。panel は置ける範囲(形のあるコマでは形の内側)。
    """
    pw, ph = panel[2] - panel[0], panel[3] - panel[1]
    sw, sh = natural
    ratio = min(ph * _STAMP_MAX_H / sh, pw * _STAMP_MAX_W / sw) * scale
    # コマからはみ出す大きさにはしない
    ratio = min(ratio, pw * 0.95 / sw, ph * 0.95 / sh)
    while True:
        size = (max(1, round(sw * ratio)), max(1, round(sh * ratio)))
        if override is not None:
            return _overridden_box(size, anchor or panel, override)
        box, free = _place(size, panel, placed, sfx=True, heads=heads)
        if free or fixed or size[1] <= ph * _STAMP_MIN_H * min(scale, 1.0):
            return box
        ratio *= _STAMP_SHRINK


@dataclass
class _Picture:
    """コマに描いた絵の中の頭(ページ座標)と、そのキャラID、コマの外に切れたキャラの頭の中心。"""

    heads: list[Rect]
    head_ids: list[int | None]
    offscreen: dict[int, tuple[float, float]]


def _draw_panel(
    page: Image.Image, rect: Rect, content: PanelContent, style: LetteringStyle, *, border: bool = True
) -> list[Element]:
    """1コマを描く(絵・文字・枠線)。コマ単位のダウンロードで使う。"""
    picture = _draw_picture(page, rect, content)
    elements = _draw_lettering(page, rect, content, style, picture)
    if border:
        ImageDraw.Draw(page).rectangle(rect, outline=(0, 0, 0), width=_BORDER)
    return elements


def _draw_picture(
    page: Image.Image, rect: Rect, content: PanelContent, cutout: Mapping[str, Any] | None = None
) -> _Picture:
    """
    コマの絵を rect に嵌め込む(まだ無ければ灰色の仮の絵)。吹き出しの置き場所を決めるための頭の位置を返す。
    cutout(背景を消す)なら、人物だけを置き、縁取りを決めていれば人物のまわりに色の縁を付ける。
    """
    x0, y0, x1, y1 = rect
    width, height = x1 - x0, y1 - y0
    draw = ImageDraw.Draw(page)
    # 絵の中の頭の位置(ページ座標)。吹き出し・描き文字はここを避ける
    heads: list[Rect] = []
    # 頭ごとのキャラID(heads と同じ並び)と、コマの外に切れたキャラの頭の中心(ページ座標)
    head_ids: list[int | None] = []
    offscreen: dict[int, tuple[float, float]] = {}
    if content.image_path is not None and content.image_path.is_file():
        source_heads = detect_heads(content.image_path)
        with Image.open(content.image_path) as src:
            image = src.convert("RGB")
        source_ids = identify_heads(image, source_heads, content.cast) if content.speakers else []
        focus_indexes = [source_ids.index(f) for f in content.focus if f in source_ids]
        if content.picture_path is not None and content.picture_path.is_file():
            with Image.open(content.picture_path) as frame:
                image = frame.convert("RGB").resize(image.size, Image.Resampling.LANCZOS)
        if cutout is not None and content.picture_path is None:
            # AI で拡大した絵があれば、そちらを使う(頭の位置も同じ倍率にする)
            image, source_heads = _upscaled(image, content.image_path, source_heads)
        if content.overlays:
            # 重ね(文字・写真)は元の絵に貼ってから切り抜く(どう切り抜いても、絵の中の同じ所に付いてくる)
            size = image.size
            draw_pieces(image, content.overlays, lambda x, y: (x * size[0], y * size[1]))
        if cutout is not None:
            image = _with_cutout(image, content.image_path)
        if cutout is not None and image.mode == "RGBA" and cutout.get("fit") == "contain":
            picture, scale, left, top = _contain(image, width, height)
        else:
            picture, scale, left, top = _cover(image, width, height, content.zoom_step, source_heads, focus_indexes)
        if picture.mode == "RGBA":
            if cutout and cutout.get("fade"):
                picture = _fade_bottom(picture)
            _paste_cutout(page, picture, (x0, y0), cutout or {})
        else:
            page.paste(picture, (x0, y0))
        for index, (hx0, hy0, hx1, hy1) in enumerate(source_heads):
            head_id = source_ids[index] if source_ids else None
            box = (
                max(round(hx0 * scale - left) + x0, x0),
                max(round(hy0 * scale - top) + y0, y0),
                min(round(hx1 * scale - left) + x0, x1),
                min(round(hy1 * scale - top) + y0, y1),
            )
            if box[2] > box[0] and box[3] > box[1]:
                heads.append(box)
                head_ids.append(head_id)
            elif head_id is not None:
                offscreen[head_id] = ((hx0 + hx1) / 2 * scale - left + x0, (hy0 + hy1) / 2 * scale - top + y0)
    else:
        draw.rectangle(rect, fill=(225, 225, 225))
        draw.text((x0 + 12, y0 + 10), "(未生成)", fill=(120, 120, 120))
    return _Picture(heads, head_ids, offscreen)


def _draw_lettering(
    page: Image.Image,
    rect: Rect,
    content: PanelContent,
    style: LetteringStyle,
    picture: _Picture,
    area: Rect | None = None,
    covered: list[Rect] | None = None,
) -> list[Element]:
    """
    吹き出し・描き文字・ナレーションを置いて描く。area は置ける範囲(形のあるコマでは形の内側の長方形。無ければ rect)。
    covered は前面のコマに覆われた所(置き済みとして避ける)。
    手動配置・文字の大きさの基準・画面に返すコマの範囲は、コマの外接矩形 rect のまま。
    """
    heads, head_ids, offscreen = picture.heads, picture.head_ids, picture.offscreen
    frame = rect
    rect = area or rect
    x0, y0, x1, y1 = rect
    width, height = x1 - x0, y1 - y0

    pairs = [(line, speaker) for line, speaker in zip(content.dialogue, _padded(content)) if line.strip()]
    if len(pairs) > _MAX_BUBBLES:
        pairs = pairs[: _MAX_BUBBLES - 1] + [("　".join(line for line, _ in pairs[_MAX_BUBBLES - 1 :]), None)]
    lines = [line for line, _ in pairs]
    # 前面のコマに覆われた所は、はじめから置き済みとして避ける
    placed: list[Rect] = list(covered or [])
    speaker = ((x0 + x1) / 2, y0 + height * 0.6)
    elements: list[Element] = []
    key_base = f"{content.key}:{content.zoom_step}"
    narration_box: Rect | None = None
    narration_block: TextBlock | None = None
    if content.narration.strip():
        # ナレーションは先に場所を取り、吹き出しはそれを避けて置く。描くのは最後(枠線が上に来るように)。
        key = f"{key_base}:narration:0"
        narration_scale = _panel_factor(frame) * style.text_scale * style.scales.get(key, 1.0)
        narration_text_size = max(12, round(_NARRATION_SIZE * narration_scale))
        narration_block = fit_text(
            content.narration.strip(),
            int(width * _NARRATION_MAX_WIDTH * min(max(narration_scale, 1.0), 1.6)),
            min(int(height * 0.7), narration_text_size * _MAX_ROWS),
            narration_text_size,
            max(10, round(_TEXT_MIN_SIZE * min(narration_scale, 1.0))),
        )
        size = narration_size(narration_block)
        override = style.overrides.get(key)
        narration_box = (
            _overridden_box(size, frame, override)
            if override is not None
            else _place(size, rect, placed, narration=True, heads=heads)[0]
        )
        placed.append(narration_box)
        elements.append(Element(key, "narration", content.narration.strip(), narration_box, frame))
    # 吹き出し・描き文字とも、既に置いたものと重なるなら文字を小さくして空きを探し直す。
    # 最小サイズでも空きが無ければ、重なりが最小の位置に置く。手動で動かしたものはその位置に置く。
    bubble_boxes: list[Rect] = []
    bubble_speakers: list[int | None] = []
    for index, line in enumerate(lines):
        key = f"{key_base}:bubble:{index}"
        override = style.overrides.get(key)
        shape, text = bubble_shape(line)
        # 文字の大きさ = 基準 × コマの大きさ × 形(叫び/小声) × 全体の設定 × 個別の調整
        element_scale = style.scales.get(key)
        scale = _panel_factor(frame) * _SHAPE_TEXT_FACTOR.get(shape, 1.0) * style.text_scale * (element_scale or 1.0)
        start_size = max(12, round(_TEXT_SIZE * scale))
        # 個別に大きさを決めたものは、空きが無くても縮めない(置き場所の方を譲る)
        min_size = start_size if element_scale is not None else max(10, round(_TEXT_MIN_SIZE * min(scale, 1.0)))
        # 空きが無ければ、まず同じ大きさの角丸の四角(場所を取らない)を試し、それでも駄目なら
        # 文字を小さくして探し直す。最後まで空きが無ければ、頭への被りが最小の位置にする。
        shapes = [shape] if shape in ("box", "cloud") else [shape, "box"]
        best: tuple[Rect, TextBlock, str] | None = None
        speaker_id = pairs[index][1]
        prefer_x = _speaker_x(heads, head_ids, offscreen, speaker_id)
        rivals = [b for b, who in zip(bubble_boxes, bubble_speakers) if speaker_id is not None and who != speaker_id]
        text_size = start_size
        while best is None:
            for candidate_shape in shapes:
                block, size = _fit_bubble(text, rect, text_size, candidate_shape, min_size)
                if override is not None:
                    best = (_overridden_box(size, frame, override), block, candidate_shape)
                    break
                box, free = _place(
                    size, rect, placed, heads=heads, prefer_x=prefer_x, earlier=bubble_boxes, rivals=rivals
                )
                if free:
                    best = (box, block, candidate_shape)
                    break
            if best is None and text_size <= min_size:
                block, size = _fit_bubble(text, rect, text_size, shape, min_size)
                best = (_place(size, rect, placed, heads=heads)[0], block, shape)
            text_size -= _SHRINK_STEP_TEXT
        box, block, final_shape = best
        side = _offpanel_side(box, rect, heads, head_ids, offscreen, speaker_id)
        if side is not None and override is None:
            box = _to_side(box, rect, side, placed)
        placed.append(box)
        bubble_boxes.append(box)
        bubble_speakers.append(speaker_id)
        elements.append(Element(key, "bubble", line, box, frame))
        # 頭に被ってしまう吹き出しだけ地を透かし、絵が見えるようにする
        opacity = min(style.bubble_opacity, _OVER_HEAD_OPACITY) if _covers_head(box, heads) else style.bubble_opacity
        draw_bubble(
            page,
            box,
            block,
            style.font_path,
            tail_toward=_tail_target(box, rect, heads, head_ids, speaker_id, side, speaker),
            opacity=opacity,
            shape=final_shape,
        )

    for index, text in enumerate(t for t in content.sfx if t.strip()):
        key = f"{key_base}:sfx:{index}"
        override = style.overrides.get(key)
        stamp_path = style.sfx_stamps.get(text)
        element_scale = style.scales.get(key)
        sfx_scale = style.sfx_scale * (element_scale or 1.0)
        if stamp_path is not None and stamp_path.is_file():
            with Image.open(stamp_path) as stamp:
                stamp.load()
                box = _place_stamp(
                    stamp.size, rect, placed, override, heads, sfx_scale, element_scale is not None, anchor=frame
                )
                placed.append(box)
                elements.append(Element(key, "sfx", text, box, frame))
                draw_stamp(page, box, stamp)
            continue
        layout = fit_sfx(text, width, height, scale=sfx_scale)
        while True:
            if override is not None:
                box = _overridden_box((layout.width, layout.height), frame, override)
                break
            box, free = _place((layout.width, layout.height), rect, placed, sfx=True, heads=heads)
            if free or element_scale is not None or layout.size <= SFX_MIN_SIZE * min(sfx_scale, 1.0):
                break
            layout = fit_sfx(text, width, height, max_size=layout.size - _SHRINK_STEP_SFX, scale=sfx_scale)
        placed.append(box)
        elements.append(Element(key, "sfx", text, box, frame))
        draw_sfx(page, box, layout, style.sfx_font_paths.get(text, style.sfx_font_path))

    if narration_box is not None and narration_block is not None:
        draw_narration(page, narration_box, narration_block, style.font_path)
    return elements


def _upscaled(image: Image.Image, image_path: Path, heads: list[Rect]) -> tuple[Image.Image, list[Rect]]:
    """AI で拡大した絵(cutout.make_upscaled)があれば、それと、同じ倍率にした頭の位置。無ければそのまま。"""
    from . import cutout

    path = cutout.upscaled_path(image_path)
    if not path.is_file():
        return image, heads
    with Image.open(path) as larger:
        upscaled = larger.convert("RGB")
    ratio = upscaled.width / image.width
    return upscaled, [tuple(round(v * ratio) for v in head) for head in heads]  # type: ignore[misc]


def _with_cutout(image: Image.Image, image_path: Path) -> Image.Image:
    """
    背景を消した絵(人物の外は透明)。境目に背景の色がにじまないよう、切り抜きの縁を少し内側に削ってから軽くぼかす。
    切り分けに失敗したら、元の絵のまま(合成は止めない)。
    """
    from . import cutout

    try:
        mask = cutout.best_mask(image_path)
    except Exception:  # noqa: BLE001 モデルを取れない等。背景を残して描く
        logging.getLogger(__name__).warning("背景を消せませんでした: %s", image_path, exc_info=True)
        return image
    edge = max(1, round(image.width / 832))
    mask = mask.resize(image.size, Image.Resampling.LANCZOS)
    mask = mask.filter(ImageFilter.MinFilter(edge * 2 + 1)).filter(ImageFilter.GaussianBlur(edge * 0.8))
    rgba = image.convert("RGBA")
    rgba.putalpha(mask)
    return rgba


# 人物全体を収めるときに、人物のまわりに残す余白(人物の大きさに対する割合)
_CONTAIN_PAD = 0.03


def _contain(img: Image.Image, width: int, height: int) -> tuple[Image.Image, float, int, int]:
    """
    背景を消した絵の、人物の写っている範囲を width×height に収める(切らない)。左右は真ん中、足元は下にそろえる。
    戻り値は _cover と同じ (絵, 拡大率, 左端, 上端)。コマ座標 = 元座標 × 拡大率 − 左端/上端。
    """
    box = img.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox() or (0, 0, img.width, img.height)
    pad_x = round((box[2] - box[0]) * _CONTAIN_PAD)
    pad_y = round((box[3] - box[1]) * _CONTAIN_PAD)
    x0, y0 = max(box[0] - pad_x, 0), max(box[1] - pad_y, 0)
    # 足元(下)には余白を付けない(枠の下端にそろえる)
    x1, y1 = min(box[2] + pad_x, img.width), box[3]
    figure = img.crop((x0, y0, x1, y1))
    scale = min(width / figure.width, height / figure.height)
    fw, fh = max(1, round(figure.width * scale)), max(1, round(figure.height * scale))
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ox, oy = (width - fw) // 2, height - fh
    canvas.alpha_composite(figure.resize((fw, fh), Image.Resampling.LANCZOS), (ox, oy))
    return canvas, scale, round(x0 * scale) - ox, round(y0 * scale) - oy


# 下端をぼかすときの、ぼかす高さ(見えている人物の高さに対する割合)
_FADE_SHARE = 0.22


def _fade_bottom(picture: Image.Image) -> Image.Image:
    """人物の下の方を、下に向かってだんだん透明にする(枠で切れた所を目立たなくする)。"""
    alpha = picture.getchannel("A")
    box = alpha.point(lambda v: 255 if v > 24 else 0).getbbox()
    if box is None:
        return picture
    bottom = box[3]
    start = bottom - max(1, round((box[3] - box[1]) * _FADE_SHARE))
    ramp = np.ones((picture.height, 1), np.float32)
    ramp[start:bottom, 0] = np.linspace(1.0, 0.0, bottom - start, dtype=np.float32)
    ramp[bottom:, 0] = 0.0
    faded = (np.asarray(alpha, np.float32) * ramp).clip(0, 255).astype(np.uint8)
    result = picture.copy()
    result.putalpha(Image.fromarray(faded, "L"))
    return result


def _paste_cutout(page: Image.Image, picture: Image.Image, at: tuple[int, int], options: Mapping[str, Any]) -> None:
    """人物だけを貼る。縁取りがあれば、人物の形を太らせた所を先に縁の色で塗る。"""
    alpha = picture.getchannel("A")
    if options.get("outline"):
        width = int(options.get("outline_width", 8))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (width * 2 + 1, width * 2 + 1))
        grown = Image.fromarray(cv2.dilate(np.asarray(alpha), kernel), "L")
        color = Image.new("RGB", picture.size, options.get("outline_color", "#ffffff"))
        page.paste(color, at, grown)
    page.paste(picture.convert("RGB"), at, alpha)


def inscribed_rect(shape: PanelShape) -> Rect:
    """
    コマの形の内側に収まる、なるべく大きな長方形(吹き出し・描き文字を置く範囲)。輪郭を縮小して塗り、
    塗った升目だけでできる一番大きな長方形を探す(ヒストグラムの最大長方形)。見つからなければ外接矩形。
    """
    x0, y0, x1, y1 = shape.rect
    step = max(4, max(x1 - x0, y1 - y0) // 120)
    cols, rows = max(1, (x1 - x0) // step), max(1, (y1 - y0) // step)
    mask = Image.new("L", (cols, rows), 0)
    ImageDraw.Draw(mask).polygon([((x - x0) / step, (y - y0) / step) for x, y in shape.points], fill=255)
    filled = [[mask.getpixel((c, r)) > 0 for c in range(cols)] for r in range(rows)]
    heights = [0] * cols
    best = (0, 0, 0, 0, 0)  # 面積, 左, 上, 右, 下(升目)
    for r in range(rows):
        heights = [h + 1 if filled[r][c] else 0 for c, h in enumerate(heights)]
        stack: list[int] = []
        for c in range(cols + 1):
            h = heights[c] if c < cols else 0
            while stack and heights[stack[-1]] >= h:
                top = heights[stack.pop()]
                left = stack[-1] + 1 if stack else 0
                area = top * (c - left)
                if area > best[0]:
                    best = (area, left, r - top + 1, c, r + 1)
            stack.append(c)
    if best[0] == 0:
        return shape.rect
    _, left, top, right, bottom = best
    # 升目の境で縮めた分、内側に1升ずつ余裕を見る(輪郭の線に文字が掛からないように)
    return (
        x0 + (left + 1) * step if right - left > 2 else x0 + left * step,
        y0 + (top + 1) * step if bottom - top > 2 else y0 + top * step,
        x0 + (right - 1) * step if right - left > 2 else x0 + right * step,
        y0 + (bottom - 1) * step if bottom - top > 2 else y0 + bottom * step,
    )


def _draw_border(page: Image.Image, shape: PanelShape) -> None:
    """コマの枠線。ページの端に接する辺(裁ち落とし)には描かない。"""
    if shape.border_width <= 0:
        return
    draw = ImageDraw.Draw(page)
    width, height = page.size
    x0, y0, x1, y1 = shape.rect
    if shape.is_rect and x0 > 0 and y0 > 0 and x1 < width and y1 < height:
        draw.rectangle(shape.rect, outline=shape.border_color, width=shape.border_width)
        return
    points = shape.points
    for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1]):
        on_edge = (ax == bx and ax in (0, width)) or (ay == by and ay in (0, height))
        if not on_edge:
            draw.line([(ax, ay), (bx, by)], fill=shape.border_color, width=shape.border_width, joint="curve")


def compose_page(
    spec: PageSpec | list[Rect], panels: list[PanelContent], style: LetteringStyle
) -> tuple[Image.Image, list[Element]]:
    """
    1ページを合成する。斜めの枠のコマは、外接矩形に描いてから輪郭で切り抜いて重ねる
    (隣のコマと外接矩形が重なるので、ページに直接描くと隣を上書きしてしまう)。
    """
    if not isinstance(spec, PageSpec):
        spec = PageSpec(PAGE_WIDTH, PAGE_HEIGHT, tuple(rect_shape(r) for r in spec))
    page = Image.new("RGB", (spec.width, spec.height), spec.background)
    count = min(len(spec.panels), len(panels))
    # 重なり順(z が小さい方から。同じなら読む順)に、絵と枠線を描く。前面のコマが後ろのコマの上に来る
    order = sorted(range(count), key=lambda i: (spec.panels[i].z, i))
    pictures: dict[int, _Picture] = {}
    for i in order:
        shape, content = spec.panels[i], panels[i]
        if shape.is_rect:
            pictures[i] = _draw_picture(page, shape.rect, content, shape.cutout)
        else:
            layer = page.copy()
            pictures[i] = _draw_picture(layer, shape.rect, content, shape.cutout)
            mask = Image.new("L", page.size, 0)
            ImageDraw.Draw(mask).polygon(list(shape.points), fill=255)
            page.paste(layer, (0, 0), mask)
        if shape.border:
            _draw_border(page, shape)
    # 文字は全部の絵と枠線の上に描く(形のあるコマで吹き出しが形に切られない・隣のコマに隠れないように)。
    # 前面のコマに覆われた所には、後ろのコマの吹き出しを置かない
    rank = {i: r for r, i in enumerate(order)}
    elements: list[Element] = []
    for i in range(count):
        shape = spec.panels[i]
        area = None if shape.is_rect else inscribed_rect(shape)
        covered = [
            overlap
            for j in range(count)
            if rank[j] > rank[i] and (overlap := _intersection(shape.rect, spec.panels[j].rect)) is not None
        ]
        elements.extend(_draw_lettering(page, shape.rect, panels[i], style, pictures[i], area, covered))
    return page, elements


def _intersection(a: Rect, b: Rect) -> Rect | None:
    box = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return box if box[2] > box[0] and box[3] > box[1] else None


@dataclass
class PagePlan:
    """1ページの割り付け。コマの枠(spec)と、そのページに入るコマの範囲(panels[start:end])。"""

    spec: PageSpec
    start: int
    end: int
    # どこから来た割り付けか: ページごとのコマ割り(manga_v2_page_layouts)の番号か、テンプレートのID
    layout: int | None = None
    template: str | None = None


def plan_pages(
    template_id: str,
    count: int,
    layouts: Sequence[PageLayout] | None = None,
    groups: Sequence[int] | None = None,
) -> list[PagePlan]:
    """
    シーン順のコマ count 個をテンプレートのコマ数ずつページに割り付ける。最後のページのコマが
    足りないときは、空きゴマを残さないよう少ないコマ数のテンプレートに切り替える。
    layouts(取り込んだ作品から写したページごとのコマ割り)があれば、先頭のページから順にそれを使い、
    使い切った後のコマはテンプレートで続ける。
    groups(コマごとのページ番号。編集画面でページの区切りを決めたとき)があれば、同じ番号が続くコマを
    1ページにまとめ、そのコマ数に合うテンプレートにする。テンプレートより多いときは、そのページの中だけで分ける。
    写したコマ割りがあるときは、そちらを優先する。
    """
    plans = []
    start = 0
    for index, layout in enumerate(layouts or []):
        if start >= count:
            break
        end = min(start + layout_size(layout), count)
        plans.append(PagePlan(layout_page(layout), start, end, layout=index))
        start = end
    per_page = len(panel_rects(template_id))
    runs = [(start, count)]
    if groups is not None and not layouts:
        runs = []
        for i in range(count):
            if i == 0 or groups[i] != groups[i - 1]:
                runs.append((i, i + 1))
            else:
                runs[-1] = (runs[-1][0], i + 1)
    for run_start, run_end in runs:
        for i in range(run_start, run_end, per_page):
            end = min(i + per_page, run_end)
            filled = fill_template(template_id, end - i)
            plans.append(PagePlan(template_page(filled), i, end, template=filled))
    return plans


def slot_rects(
    template_id: str,
    count: int,
    layouts: Sequence[PageLayout] | None = None,
    groups: Sequence[int] | None = None,
) -> list[Rect]:
    """
    合成と同じ割り付け(plan_pages)で、シーン順のコマが入る枠の外接矩形(ピクセル座標)。コマの生成サイズを決める。
    セリフの多いシーンを寄りのコマに分けると(合成の設定)、その後ろのコマは枠が1つずつずれる。
    """
    rects: list[Rect] = []
    for plan in plan_pages(template_id, count, layouts, groups):
        rects.extend(shape.rect for shape in plan.spec.panels[: plan.end - plan.start])
    return rects


def compose_pages(
    template_id: str,
    panels: list[PanelContent],
    style: LetteringStyle,
    layouts: Sequence[PageLayout] | None = None,
    groups: Sequence[int] | None = None,
) -> list[tuple[Image.Image, list[Element]]]:
    """コマをページに割り付けて(plan_pages)、ページごとに合成する。"""
    return [
        compose_page(plan.spec, panels[plan.start : plan.end], style)
        for plan in plan_pages(template_id, len(panels), layouts, groups)
    ]


def compose_panel(content: PanelContent, style: LetteringStyle) -> Image.Image:
    """
    1コマを、ページに嵌める前の絵の大きさのまま(切り抜かず・枠線なしで)吹き出しと描き文字を
    置いて返す。ダウンロード用。手動配置はコマに対する割合なので、そのまま同じ辺りに置かれる。
    """
    if content.image_path is None or not content.image_path.is_file():
        raise ValueError("panel image is missing")
    with Image.open(content.image_path) as src:
        size = src.size
    canvas = Image.new("RGB", size, (255, 255, 255))
    _draw_panel(canvas, (0, 0, size[0], size[1]), content, style, border=False)
    return canvas


_PAGE_GAP = 40


def concat_pages(pages: list[Image.Image]) -> bytes:
    """全ページを縦に並べた1枚のPNG(スマホで縦スクロールして読む用)。"""
    if not pages:
        raise ValueError("pages is empty")
    total = sum(p.height for p in pages) + _PAGE_GAP * (len(pages) - 1)
    # 見開きのページは横2ページ分あるので、一番広いページに合わせる
    canvas = Image.new("RGB", (max(p.width for p in pages), total), (255, 255, 255))
    y = 0
    for page in pages:
        canvas.paste(page, ((canvas.width - page.width) // 2, y))
        y += page.height + _PAGE_GAP
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def page_png(page: Image.Image) -> bytes:
    buf = io.BytesIO()
    page.save(buf, format="PNG")
    return buf.getvalue()
