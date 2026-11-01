"""
素体の 3D モデル(model_3d.rig が骨を入れたもの)に付けるポーズと、描き出すカメラの向き(漫画のコマの参考)。
Blender(scripts/blender/pose_render.py)に JSON で渡す。

ポーズは、骨ごとの回転(度)。どれも骨を入れたときの姿勢(腕と脚を下ろして立つ)からの回転で、親の骨の回転に続けて回る:
- forward: 横の軸のまわり。正で、下へ伸びた骨の先が前へ出る(腕・脚を前へ振る)。胴は正で前へ倒れる
- side: 前後の軸のまわり(2D の動きの回転と同じ)。正で、正面から見て反時計回り(キャラの左の腕・脚は外へ開く)
- twist: 骨の向きのまわり。正で、骨の先から見て反時計回り(胴・頭は正でキャラの左を向く。下を向いた脚は、正で
  上から見て時計回り = キャラの右を向く)
hands は手の形(open 開く・relaxed 軽く曲げる・fist 握る・point 人差し指だけ伸ばす)。左右(.L・.R)ごと。
prop は置く物(chair: 腰の下に箱の椅子)。ポーズのあと、体のいちばん低い所を床(高さ 0)に合わせる。
"""

from __future__ import annotations

from dataclasses import dataclass, field

HAND_SHAPES = ("open", "relaxed", "fist", "point")


@dataclass(frozen=True)
class Pose:
    label: str
    bones: dict[str, tuple[float, float, float]]  # 骨 → (forward, side, twist)
    hands: dict[str, str] = field(default_factory=lambda: {"L": "relaxed", "R": "relaxed"})
    prop: str | None = None

    def to_json(self) -> dict:
        return {"label": self.label, "bones": {k: list(v) for k, v in self.bones.items()}, "hands": self.hands, "prop": self.prop}


def render_json(pose: str, cameras: list[str]) -> dict:
    """pose_render.py に渡す JSON(ポーズと、描くカメラの向き)。"""
    return {**POSES[pose].to_json(), "cameras": {name: list(CAMERAS[name][1:]) for name in cameras}}


# 腕を体の横に少し開いて下ろす(骨を入れた姿勢は腕が太ももに付きそうなので、立ちポーズはどれも少し開く)
_ARMS_DOWN = {"upper_arm.L": (0, 8, 0), "upper_arm.R": (0, -8, 0), "forearm.L": (8, 0, 0), "forearm.R": (8, 0, 0)}

POSES: dict[str, Pose] = {
    "stand": Pose("立つ", {**_ARMS_DOWN}),
    "walk": Pose(
        "歩く",
        {
            "thigh.L": (25, 0, 0), "shin.L": (-10, 0, 0), "foot.L": (-10, 0, 0),
            "thigh.R": (-15, 0, 0), "shin.R": (-30, 0, 0), "foot.R": (5, 0, 0),
            "upper_arm.L": (-20, 6, 0), "forearm.L": (10, 0, 0),
            "upper_arm.R": (25, -6, 0), "forearm.R": (20, 0, 0),
            "torso": (3, 0, 4), "head": (-3, 0, -4),
        },
    ),
    "run": Pose(
        "走る",
        {
            "torso": (12, 0, 6), "head": (-10, 0, -6),
            "thigh.L": (60, 0, 0), "shin.L": (-85, 0, 0), "foot.L": (10, 0, 0),
            "thigh.R": (-25, 0, 0), "shin.R": (-50, 0, 0), "foot.R": (20, 0, 0),
            "upper_arm.L": (-45, 10, 0), "forearm.L": (80, 0, 0),
            "upper_arm.R": (50, -10, 0), "forearm.R": (85, 0, 0),
        },
        {"L": "fist", "R": "fist"},
    ),
    "sit_chair": Pose(
        "椅子に座る",
        {
            "thigh.L": (88, 4, 0), "shin.L": (-88, 0, 0),
            "thigh.R": (88, -4, 0), "shin.R": (-88, 0, 0),
            "upper_arm.L": (25, 5, 0), "forearm.L": (45, 0, 0),
            "upper_arm.R": (25, -5, 0), "forearm.R": (45, 0, 0),
            "head": (5, 0, 0),
        },
        prop="chair",
    ),
    "look_back": Pose(
        "振り向く",
        {
            # 胴をキャラの右へひねり、脚は戻す(下を向いた骨のねじれは、上から見ると逆回りなので、胴と同じ符号で戻る)。
            # 頭はさらに右へ向けて、後ろを見る
            "torso": (0, 0, -35), "thigh.L": (0, 0, -35), "thigh.R": (0, 0, -35),
            "head": (0, 0, -55), **_ARMS_DOWN,
        },
    ),
    "lie_back": Pose(
        "あおむけに寝転ぶ",
        {
            "torso": (-90, 0, 0), "head": (10, 0, 0),
            "upper_arm.L": (0, 20, 0), "upper_arm.R": (0, -20, 0),
            "thigh.L": (0, 4, 0), "thigh.R": (0, -4, 0),
        },
        {"L": "open", "R": "open"},
    ),
    "point": Pose(
        "指さす",
        {
            "upper_arm.R": (80, -10, 0), "forearm.R": (5, 0, 0),
            "upper_arm.L": (0, 8, 0), "forearm.L": (8, 0, 0),
            "head": (0, 0, 0),
        },
        {"L": "relaxed", "R": "point"},
    ),
    "cheer": Pose(
        "バンザイ",
        {"upper_arm.L": (0, 160, 0), "upper_arm.R": (0, -160, 0), "head": (-8, 0, 0)},
        {"L": "open", "R": "open"},
    ),
}

# カメラ: 名前 → (表示名, キャラのまわりの角度(度。0 は正面、正でキャラの左へ回る), 高さ(身長に対する割合),
# 距離(全身が入る距離に対する割合。近いほど遠近が強い))。カメラは体の真ん中を向く(高いと見下ろし、低いと見上げる)
CAMERAS: dict[str, tuple[str, float, float, float]] = {
    "front": ("正面", 0, 0.55, 1.0),
    "three_quarter": ("斜め前", 35, 0.6, 1.0),
    "side": ("横", 90, 0.55, 1.0),
    "back": ("後ろ", 180, 0.55, 1.0),
    "low": ("あおり", 20, 0.02, 0.92),
    "high": ("俯瞰", 25, 1.7, 0.92),
}
STYLES = {"shade": "灰色の陰影", "line": "線画風"}


def labels() -> dict[str, dict[str, str]]:
    return {
        "poses": {k: p.label for k, p in POSES.items()},
        "cameras": {k: c[0] for k, c in CAMERAS.items()},
        "styles": dict(STYLES),
    }
