"""
髪の層(長い髪)のテスト: 体の部位分けのモデル(body_parsing)の前後の処理、点で髪を指す切り出し、腕と胴の間のすき間(後ろ髪)
の決め方。モデル(ONNX・MobileSAM)は呼ばず、作った値に差し替える。

実行方法:
  uv run pytest tests/test_body_hair_layer.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import body_parsing, body_rig  # noqa: E402


def test_parse_maps_logits_back_to_the_image(monkeypatch: pytest.MonkeyPatch) -> None:
    # 縦長の絵(400×800)は、長い辺を 1024 にして左右を空ける。出力(256×256)の右半分を「髪」にすると、絵の右半分が髪になる
    logits = np.full((1, 19, 256, 256), -5.0, np.float32)
    logits[0, 0, :, 128:] = 5.0

    class Session:
        def run(self, _outputs, feeds):
            assert feeds["image"].shape == (1, 3, 1024, 1024)
            return [logits]

    monkeypatch.setattr(body_parsing, "_get_session", lambda: Session())
    parts = body_parsing.parse(Image.new("RGB", (400, 800)))
    assert set(parts) == set(body_parsing.LABELS)
    hair = parts["hair"]
    assert hair.shape == (800, 400) and hair[:, 250:].all() and not hair[:, :150].any()


def test_hair_from_points_uses_the_smallest_clean_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    from python import point_segment

    small = np.zeros((100, 100), bool)
    small[10:20, 10:20] = True
    big = np.zeros((100, 100), bool)
    big[0:60, 0:60] = True  # 外したい点(50, 50)を含む
    bigger = np.zeros((100, 100), bool)
    bigger[0:30, 0:30] = True
    monkeypatch.setattr(point_segment, "encode", lambda image: None)
    monkeypatch.setattr(point_segment, "segment", lambda f, pos, neg: [(big, 0.9), (bigger, 0.8), (small, 0.7)])
    out = body_rig.hair_from_points(Image.new("RGB", (100, 100)), [{"pos": [[15, 15]], "neg": [[50, 50]]}])
    assert (out == small).all()


def test_gap_is_only_between_arm_and_torso(monkeypatch: pytest.MonkeyPatch) -> None:
    h, w = 300, 300
    parts = {name: np.zeros((h, w), bool) for name in body_parsing.LABELS}
    parts["topwear"][100:250, 120:180] = True  # 胴
    parts["handwear"][100:250, 195:215] = True  # 腕(胴との間に 15px のすき間)
    parts["topwear"][150:160, 140:160] = False  # 胸の前の髪(胴だけに囲まれた穴)
    parts["face"][40:90, 130:170] = True
    monkeypatch.setattr(body_parsing, "available", lambda: True)
    monkeypatch.setattr(body_parsing, "parse", lambda image: parts)
    joints = {"nose": np.array([150.0, 60.0]), "neck": np.array([150.0, 95.0]),
              "left_shoulder": np.array([190.0, 100.0]), "right_shoulder": np.array([110.0, 100.0])}
    core, gap = body_rig._body_core(Image.new("RGB", (w, h)), joints)
    assert gap[200, 188]  # 腕と胴の間
    assert not gap[155, 150]  # 胸の前の髪の穴はすき間にしない
    assert not gap[60, 150] and core[60, 150]  # 顔
