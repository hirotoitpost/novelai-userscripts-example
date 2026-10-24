"""
素体の部位の絵の自動チェック。描いた絵を、タグの判定(image_tagger の WD タガー)・頭の数(manga_v2.detect)・骨格(Aポーズだけ。
ugoira_rig の RTMPose)で調べ、部位ごとの条件に合うかと点数を出す。条件に合わなければ、呼び出し側がシードを変えて描き直す
(合う絵が出なければ、点数のいちばん高い絵を残す)。

条件は、手で確かめて直していたこと(2026-10 の素体の実機)をそのまま決まりにしたもの:
- 1枚に人が2人以上(前後の2人並び・三面図・後ろに全身が写り込む)
- 真後ろの部位で振り返る・こちらを見る(looking back / looking at viewer)
- 手をこちらへ突き出して遠近で縮む(foreshortening / reaching towards viewer)
- 足の裏をこちらへ向ける構図(soles)
- Aポーズで腕が体から離れていない・足首まで写っていない
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image

# タグの確からしさがこれ以上なら「その絵にある」とみなす
_ON = 0.5
# 1枚に人が2人以上いるとみなすタグ
_CROWD_TAGS = ("multiple views", "2girls", "2boys", "multiple girls", "multiple boys", "turnaround", "reference sheet")


@dataclass(frozen=True)
class Rule:
    # 落とすタグ(これがあれば不合格)と、あってほしいタグ(無ければ不合格)。(タグ, しきい値, 画面での理由)
    reject: tuple[tuple[str, float, str], ...] = ()
    require: tuple[tuple[str, float, str], ...] = ()
    # 頭の数の上限(全身・上半身は 1、手足の寄りは写らなくてもよいので 1)
    max_heads: int = 1
    # Aポーズの骨格を調べる
    a_pose: bool = False


_REAR = Rule(
    reject=(("looking back", _ON, "振り返っている"), ("looking at viewer", _ON, "こちらを見ている")),
    require=(("from behind", _ON, "後ろからの絵になっていない"),),
)
_SIDE = Rule(reject=(("looking at viewer", 0.7, "こちらを見ている"),))
_HAND = Rule(reject=(("foreshortening", 0.6, "手を突き出して遠近で縮んでいる"),
                     ("reaching towards viewer", _ON, "手をこちらへ伸ばしている")))
_FOOT = Rule(reject=(("soles", _ON, "足の裏をこちらへ向けている"),))

RULES: dict[str, Rule] = {
    "front": Rule(require=(("full body", _ON, "全身が写っていない"),)),
    "side": _SIDE,
    "back": _REAR,
    "three_quarter": Rule(require=(("full body", _ON, "全身が写っていない"),)),
    "a_pose": Rule(require=(("full body", _ON, "全身が写っていない"),), a_pose=True),
    "head_side": _SIDE,
    "head_back": _REAR,
    "upper_back": _REAR,
    "hand_palm": _HAND,
    "hand_back": _HAND,
    "foot": _FOOT,
}


@dataclass
class CheckResult:
    passed: bool
    # 点数(高いほど良い)。不合格の絵どうしでも、残す絵を選ぶのに使う
    score: float
    reasons: list[str] = field(default_factory=list)
    # 判定に使ったタグの確からしさ(画面で確かめる用)
    tags: dict[str, float] = field(default_factory=dict)
    heads: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "passed": self.passed, "score": round(self.score, 3), "reasons": self.reasons,
            "tags": {k: round(v, 2) for k, v in self.tags.items()}, "heads": self.heads,
        }


def a_pose_problems(points: dict[str, np.ndarray]) -> list[str]:
    """Aポーズの骨格の問題(腕が体から離れていない・足首が写っていない・関節が取れない)。"""
    need = ("left_shoulder", "right_shoulder", "left_wrist", "right_wrist", "left_hip", "right_hip")
    missing = [n for n in need if n not in points]
    if missing:
        return [f"関節が取れない({', '.join(missing)})"]
    problems = []
    if "left_ankle" not in points or "right_ankle" not in points:
        problems.append("足首まで写っていない")
    shoulder = float(np.linalg.norm(points["left_shoulder"] - points["right_shoulder"]))
    for side, label in (("left", "左"), ("right", "右")):
        # 手首が、同じ側の腰より外へ肩幅の 0.15 倍以上出ていれば、腕が体から離れている
        outward = (points[f"{side}_wrist"][0] - points[f"{side}_hip"][0]) * (1 if side == "left" else -1)
        if outward < shoulder * 0.15:
            problems.append(f"{label}腕が体から離れていない")
    return problems


def judge(part: str, tags: dict[str, float], heads: int, pose_problems: list[str] | None = None) -> CheckResult:
    """タグ・頭の数・骨格の問題から、合否と点数。点数は、条件から外れた分だけ下がる(合格なら 1 に近い)。"""
    rule = RULES.get(part, Rule())
    reasons: list[str] = []
    score = 1.0
    for tag, threshold, reason in rule.reject:
        p = tags.get(tag, 0.0)
        score -= p
        if p >= threshold:
            reasons.append(reason)
    for tag, threshold, reason in rule.require:
        p = tags.get(tag, 0.0)
        score -= max(0.0, threshold - p)
        if p < threshold:
            reasons.append(reason)
    crowd = max((tags.get(t, 0.0) for t in _CROWD_TAGS), default=0.0)
    score -= crowd
    if heads > rule.max_heads or crowd >= 0.4:
        reasons.append("人が2人以上写っている")
        score -= 1.0
    for problem in pose_problems or []:
        reasons.append(problem)
        score -= 0.5
    watched = {t for t, _, _ in (*rule.reject, *rule.require)} | set(_CROWD_TAGS)
    return CheckResult(
        passed=not reasons, score=score, reasons=reasons,
        tags={t: p for t, p in tags.items() if t in watched and p >= 0.1}, heads=heads,
    )


def check(part: str, image: Image.Image) -> CheckResult:
    """描いた絵を調べる(タグの判定・頭の数・Aポーズなら骨格)。"""
    from .image_tagger import tag_image
    from .manga_v2.detect import detect_heads_in_image

    rgb = image.convert("RGB")
    tags = {s.tag: s.probability for s in tag_image(rgb) if s.category == "general"}
    heads = len(detect_heads_in_image(rgb))
    problems = None
    if RULES.get(part, Rule()).a_pose:
        from .body_rig import RigError, detect

        try:
            _, points = detect(rgb)
            problems = a_pose_problems(points)
        except RigError as exc:
            problems = [str(exc)]
    return judge(part, tags, heads, problems)
