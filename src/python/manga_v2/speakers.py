"""
セリフの話し手を本文から推定し、コマの絵の中の頭がどのキャラかを髪の色で見分ける。

吹き出しのしっぽは話し手の顔へ向けたいが、本文の「」には誰のセリフかが書かれていない。
小説の地の文はたいてい「みおが呟いた」「ゆらは叫んだ」のように話し手を書くので、
その手がかりを規則で拾う。手がかりが無いセリフは、二人の会話なら交互に話しているとみなす。
"""

from __future__ import annotations

import itertools
import os
import re
from dataclasses import dataclass, replace

import numpy as np
from PIL import Image

Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class CastMember:
    """シーンに登場するキャラ。names は本文での呼ばれ方(フルネーム・名・姓)。"""

    id: int
    names: tuple[str, ...]
    # キャラシートの容姿タグから読んだ髪の色(RGB)。分からなければ None
    hair: tuple[int, int, int] | None = None


def cast_member(character: dict) -> CastMember:
    """キャラシート(characters の行)から CastMember を作る。名前は「姓 名」の形を想定する。"""
    name = (character.get("name") or "").strip()
    parts = [p for p in re.split(r"[ 　]+", name) if p]
    names = ["".join(parts)] + parts[::-1] if len(parts) > 1 else parts
    # 長い呼び方から照合する(「佐倉みお」を「みお」より先に)
    unique = sorted(dict.fromkeys(n for n in names if n), key=len, reverse=True)
    return CastMember(
        int(character["id"]),
        tuple(unique),
        hair_color(character.get("appearance_tags") or ""),
    )


# 登場キャラの名前に共通する頭の部分を姓とみなす最小の長さ
_MIN_SHARED_FAMILY_NAME = 2


def cast_members(characters: list[dict]) -> list[CastMember]:
    """
    シーンの登場キャラ全員分の CastMember。名前が空白で区切られておらず(「矢野栄子」「矢野先生」)、
    全員の名前の頭が共通しているなら、それを姓とみなして残り(「栄子」「先生」)も呼び名に加える。
    本文では夫婦や家族を姓抜きで呼ぶことがほとんどなので、これが無いと話し手の手がかりを拾えない。
    """
    members = [cast_member(c) for c in characters]
    if len(members) < 2:
        return members
    full_names = [m.names[0] for m in members]
    family = os.path.commonprefix(full_names)
    if len(family) < _MIN_SHARED_FAMILY_NAME or any(len(n) <= len(family) for n in full_names):
        return members
    return [
        replace(m, names=tuple(sorted(dict.fromkeys((*m.names, m.names[0][len(family) :])), key=len, reverse=True)))
        for m in members
    ]


# ---- 話し手の推定 ----

# 名前の直後に来ると、その人が文の主語だとわかる助詞
_SUBJECT_PARTICLES = "はがも"
# 呼びかけ(「ゆら、…」「みおちゃん、…」)。呼ばれた側は話し手ではない
_VOCATIVE_SUFFIXES = ("ちゃん", "さん", "くん", "君")


def _subject_in(narration: str, cast: list[CastMember]) -> int | None:
    """地の文で最初に主語として出てくるキャラ(「親友の佐倉みおが、」も拾う)。"""
    best: tuple[int, int] | None = None
    for member in cast:
        for name in member.names:
            match = re.search(re.escape(name) + f"[{_SUBJECT_PARTICLES}]", narration)
            if match and (best is None or match.start() < best[0]):
                best = (match.start(), member.id)
    return best[1] if best else None


def _addressee(line: str, cast: list[CastMember]) -> int | None:
    """
    セリフの中の呼びかけで呼ばれているキャラ。文の頭か区切りの直後に名前があり、すぐ読点などが
    続くもの(「ゆら、…」「ありがとう、みお。」「…。ゆら、お風呂…」)。「みおは？」は呼びかけではない。
    """
    for member in cast:
        for name in member.names:
            suffixes = "|".join(_VOCATIVE_SUFFIXES)
            if re.search(
                rf"(?:^|[。、！？!?　 ]){re.escape(name)}(?:{suffixes})?[、。！!…]",
                line,
            ):
                return member.id
    return None


# 前のセリフとの間にこの行数以上の地の文があれば、会話が途切れたとみなす
_INTERRUPTION_LINES = 2


def _line_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    return line_start, len(text) if line_end < 0 else line_end


def attribute_speakers(text: str, spans: list[tuple[int, int, str]], cast: list[CastMember]) -> list[int | None]:
    """
    セリフ(本文中の開始・終了位置と中身)ごとに話し手のキャラIDを返す。分からなければ None。

    手がかりの優先順:
    1. 同じ行の地の文の主語(みおは微笑んで、「…」と言った)
    2. 直後の行の地の文の主語(「結構、降ってるね」/みおが呟いた)
    3. 呼びかけ(「ゆら、…」は二人の場面ならゆら以外が話している)
    4. 会話が途切れた後(前のセリフとの間に地の文が2行以上)なら、直前の地の文の主語
    5. 二人の場面で、前のセリフの話し手と交互
    6. 登場キャラが一人だけならその人
    """
    ids = [m.id for m in cast]
    result: list[int | None] = []
    for index, (start, end, line) in enumerate(spans):
        line_start, line_end = _line_bounds(text, start, end)
        same_line = text[line_start:start] + text[end:line_end]
        speaker = _subject_in(re.sub(r"「[^」]*」", "", same_line), cast)

        if speaker is None:
            # 直後の行が地の文だけなら、その主語。セリフを含む行は見ない(「みおは舌を出した。「…」」の
            # ように次のセリフがある行の主語は、その次のセリフの話し手)
            following = text[line_end:].lstrip("\n").split("\n", 1)[0]
            if following and "「" not in following:
                speaker = _subject_in(following, cast)

        if speaker is None and len(ids) == 2:
            called = _addressee(line, cast)
            if called is not None:
                speaker = ids[1] if called == ids[0] else ids[0]

        if speaker is None:
            # 前のセリフとの間に地の文が何行もあれば会話は途切れている。交互ではなく、
            # 直前の地の文の主語(「ゆらはふと鞄に目を落とした。」/「あれ？」)を話し手にする
            previous_end = spans[index - 1][1] if index > 0 else 0
            between = [s for s in text[previous_end:line_start].split("\n") if s.strip() and "「" not in s]
            if len(between) >= _INTERRUPTION_LINES or not result:
                for narration in reversed(between):
                    speaker = _subject_in(narration, cast)
                    if speaker is not None:
                        break

        if speaker is None and len(ids) == 2:
            # 交互の起点は前のセリフの話し手。ただし間の地の文に「…」と言った、のような吹き出しに
            # しない発言があれば、その行の主語が最後に話した人になる
            last = result[-1] if result else None
            previous_end = spans[index - 1][1] if index > 0 else 0
            for narration in text[previous_end:line_start].split("\n"):
                if "「" in narration:
                    last = _subject_in(re.sub(r"「[^」]*」", "", narration), cast) or last
            if last is not None:
                speaker = ids[1] if last == ids[0] else ids[0]

        if speaker is None and len(ids) == 1:
            speaker = ids[0]
        result.append(speaker)

    # 交互の推定は前から順にしか効かないので、冒頭の不明なセリフは後ろの確定から逆算する
    if len(ids) == 2:
        for index in range(len(result) - 2, -1, -1):
            if result[index] is None and result[index + 1] is not None:
                result[index] = ids[1] if result[index + 1] == ids[0] else ids[0]
    return result


# ---- 頭がどのキャラかの見分け(髪の色) ----

# 容姿タグの「◯◯ hair」の色。生成画像の典型的な色に合わせた目安
_HAIR_COLORS: dict[str, tuple[int, int, int]] = {
    "black": (40, 38, 45),
    "dark brown": (75, 50, 38),
    "brown": (120, 80, 55),
    "light brown": (170, 125, 90),
    "blonde": (225, 195, 120),
    "orange": (225, 130, 60),
    "red": (175, 55, 50),
    "pink": (235, 160, 185),
    "purple": (125, 85, 160),
    "blue": (70, 100, 180),
    "light blue": (150, 190, 230),
    "aqua": (100, 195, 205),
    "green": (80, 140, 85),
    "white": (235, 235, 235),
    "silver": (190, 190, 200),
    "grey": (150, 150, 155),
}
_HAIR_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, _HAIR_COLORS), key=len, reverse=True)) + r") hair\b")


def hair_color(appearance_tags: str) -> tuple[int, int, int] | None:
    match = _HAIR_RE.search(appearance_tags.lower())
    return _HAIR_COLORS[match.group(1)] if match else None


# 髪の色とみなす距離(RGB)。これより遠い画素(肌・背景・服)は数えない
_HAIR_TOLERANCE = 55
# 頭の枠の画素のうち、髪の色に当たる画素がこの割合に満たなければ判定しない
_MIN_HAIR_SHARE = 0.04
# 一番多いキャラの髪色の画素が、2番目のこの倍以上あるときだけそのキャラとみなす
_WIN_RATIO = 1.5


def _hair_votes(image: Image.Image, box: Box, known: list[CastMember]) -> list[float] | None:
    """
    頭の枠の画素を、一番近いキャラの髪色に振り分けた割合(known と同じ並び)。
    枠の平均や中央値は背景・肌の色に引きずられて髪色と離れてしまうので、画素ごとに数える。
    """
    x0, y0, x1, y1 = (round(v) for v in box)
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1, image.width), min(y1, image.height)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    pixels = np.asarray(image.crop((x0, y0, x1, y1)).resize((32, 32)), dtype=np.float32).reshape(-1, 1, 3)
    colors = np.array([m.hair for m in known], dtype=np.float32).reshape(1, -1, 3)
    distance = np.linalg.norm(pixels - colors, axis=2)  # (画素, キャラ)
    nearest = distance.argmin(axis=1)
    near_enough = distance.min(axis=1) <= _HAIR_TOLERANCE
    total = len(pixels)
    return [float(((nearest == k) & near_enough).sum()) / total for k in range(len(known))]


# 頭の数と登場キャラの数が同じときに、並び順も手がかりにする人数の上限(組み合わせを総当たりする)
_MAX_ORDERED_CAST = 4
# 並び順が合う頭1つあたりの加点(髪色の画素の割合と同じ尺度)
_ORDER_WEIGHT = 0.3


def _identify_in_order(image: Image.Image, heads: list[Box], cast: list[CastMember]) -> list[int | None]:
    """
    頭とキャラの割り当てを、髪色の一致と並び順の両方で総当たりして決める。コマの生成では
    キャラを割り当て順に左から並べるよう位置を指定している(characterPrompts の center)ので、
    左から順に cast の並びになっている見込みが高い。髪色だけだと、暗い場面で茶髪が黒髪に
    見えるなどして左右が入れ替わることがある(実機で確認)。
    """
    votes = [_hair_votes(image, head, cast) or [0.0] * len(cast) for head in heads]
    by_x = sorted(range(len(heads)), key=lambda i: heads[i][0] + heads[i][2])
    rank = {head: position for position, head in enumerate(by_x)}

    def score(assignment: tuple[int, ...]) -> float:
        # assignment[頭] = cast の何番目か
        hair = sum(votes[head][member] for head, member in enumerate(assignment))
        order = sum(1 for head, member in enumerate(assignment) if rank[head] == member)
        return hair + _ORDER_WEIGHT * order

    best = max(itertools.permutations(range(len(cast))), key=score)
    return [cast[member].id for member in best]


def identify_heads(image: Image.Image, heads: list[Box], cast: list[CastMember]) -> list[int | None]:
    """
    頭ごとにどのキャラかを返す(分からなければ None)。髪の色が分かるキャラが二人以上いて、
    色が見分けられるときだけ判定する(白黒のコマや同じ髪色の二人は判定しない)。
    """
    known = [m for m in cast if m.hair is not None]
    if len(known) < 2 or not heads:
        return [None] * len(heads)
    if len(heads) == len(cast) == len(known) <= _MAX_ORDERED_CAST:
        return _identify_in_order(image, heads, known)
    result: list[int | None] = [None] * len(heads)
    # 頭ごとに髪色の画素が一番多いキャラを選び、同じキャラに複数の頭が当たったら多い方だけを残す
    claims: dict[int, tuple[float, int]] = {}
    for index, head in enumerate(heads):
        votes = _hair_votes(image, head, known)
        if votes is None:
            continue
        ranked = sorted(zip(votes, (m.id for m in known)), reverse=True)
        (share, member_id), (runner_up, _) = ranked[0], ranked[1]
        if share < _MIN_HAIR_SHARE or share < runner_up * _WIN_RATIO:
            continue
        if member_id not in claims or share > claims[member_id][0]:
            claims[member_id] = (share, index)
    for member_id, (_, index) in claims.items():
        result[index] = member_id
    # 見分けられなかった頭と、まだ当てていないキャラが一つずつ残ったら、消去法でその人とみなす
    # (二人のうち片方が分かれば、もう片方の髪色が多少ずれて描かれていても決まる)
    if claims:
        left_heads = [i for i, r in enumerate(result) if r is None]
        left_members = [m.id for m in cast if m.id not in claims]
        if len(left_heads) == 1 and len(left_members) == 1:
            result[left_heads[0]] = left_members[0]
    return result
