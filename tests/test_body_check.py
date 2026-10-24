"""
素体の部位の自動チェック(body_check.py)の決まりのテスト。タグ・頭の数・骨格は作った値を渡す(モデルは呼ばない)。

実行方法:
  uv run pytest tests/test_body_check.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import body_check  # noqa: E402


def test_rear_views_reject_looking_back() -> None:
    good = body_check.judge("back", {"from behind": 0.95, "facing away": 0.5}, heads=1)
    assert good.passed and good.score > 0.9
    bad = body_check.judge("back", {"from behind": 0.85, "looking back": 0.84, "looking at viewer": 0.79}, heads=1)
    assert not bad.passed and "振り返っている" in bad.reasons and bad.score < good.score
    # 後ろからの絵になっていない
    assert "後ろからの絵になっていない" in body_check.judge("head_back", {"from behind": 0.2}, heads=1).reasons


def test_crowds_hands_and_feet() -> None:
    assert "人が2人以上写っている" in body_check.judge("front", {"full body": 0.9}, heads=2).reasons
    assert "人が2人以上写っている" in body_check.judge("front", {"full body": 0.9, "multiple views": 0.6}, heads=1).reasons
    assert not body_check.judge("hand_palm", {"foreshortening": 0.93}, heads=1).passed
    assert body_check.judge("hand_palm", {}, heads=0).passed
    assert not body_check.judge("foot", {"soles": 0.8}, heads=0).passed
    assert body_check.judge("foot", {"soles": 0.3, "foot focus": 0.9}, heads=0).passed


def test_a_pose_skeleton() -> None:
    p = {
        "left_shoulder": (500, 260), "right_shoulder": (330, 260), "left_hip": (480, 570), "right_hip": (360, 570),
        "left_wrist": (590, 570), "right_wrist": (245, 565), "left_ankle": (460, 990), "right_ankle": (400, 1050),
    }
    points = {k: np.array(v, np.float32) for k, v in p.items()}
    assert body_check.a_pose_problems(points) == []
    # 腕を体に付けた絵(手首が腰の内側)
    close = dict(points, left_wrist=np.array([485, 570], np.float32))
    assert body_check.a_pose_problems(close) == ["左腕が体から離れていない"]
    no_feet = {k: v for k, v in points.items() if "ankle" not in k}
    assert "足首まで写っていない" in body_check.a_pose_problems(no_feet)
    result = body_check.judge("a_pose", {"full body": 0.95}, heads=1, pose_problems=["左腕が体から離れていない"])
    assert not result.passed
