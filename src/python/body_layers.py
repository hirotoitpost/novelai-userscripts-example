"""
see-through(see_through_remote.py)で層に分けた PSD から、素体の2Dモデル(body_rig と同じ rig.json)を作る。

see-through の層は、隠れていた所まで描き足してある(後ろ髪は頭の後ろまで、腕は胴に重なっていた所まで、胴は髪に隠れていた
肩まで)ので、絵を部位に切り分けたり、隠れた所をまわりから埋めたりしなくてよい。層ごとに1枚のメッシュを作り、その層が
動くべき骨だけで重みを付ける(腕の層は肩から手先の骨、顔の部品は頭の骨 など)。重ねる順は PSD の順(奥行きの順)のまま。

- 骨格は、PSD を重ねた絵から取る(座標は PSD の画面。see-through は絵を正方形の画面の中ほどに置く)
- 後ろ髪(back hair)は、目の高さより下に垂れた所に揺れものの鎖(body_physics)を付ける。前髪(front hair)は頭と動く
- 胸の揺れ(成人キャラだけ)は、上の服(topwear)の層に付ける
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import body_physics
from .body_rig import (
    BONES,
    _GRID,
    _SHOULDER_CAP,
    _add_breasts,
    _hair_bones,
    _hair_mesh,
    _NAMES,
    _overlay,
    _region_mesh,
    RigError,
    detect,
    joints,
    weights,
)

_HEAD = ("head",)
_TORSO = ("torso",)
_ARM = {side: ("torso", f"upper_arm.{side}", f"forearm.{side}", f"hand.{side}") for side in ("L", "R")}
_LEGS = ("torso", "thigh.L", "shin.L", "foot.L", "thigh.R", "shin.R", "foot.R")
_FEET = ("shin.L", "foot.L", "shin.R", "foot.R")

# 層の名前(see-through の部位。V3)→ 重みを付ける骨。左右は、キャラから見た左右(-l がキャラの左 = 絵の右)
LAYER_BONES: dict[str, tuple[str, ...]] = {
    "front hair": _HEAD, "headwear": _HEAD, "face": _HEAD, "mouth": _HEAD, "nose": _HEAD,
    "eyebrow": _HEAD, "eyebrow-l": _HEAD, "eyebrow-r": _HEAD, "eyelash": _HEAD, "eyelash-l": _HEAD, "eyelash-r": _HEAD,
    "eyewhite": _HEAD, "eyewhite-l": _HEAD, "eyewhite-r": _HEAD, "irides": _HEAD, "irides-l": _HEAD, "irides-r": _HEAD,
    "eyes": _HEAD, "eyel": _HEAD, "eyer": _HEAD, "browl": _HEAD, "browr": _HEAD, "eyewear": _HEAD,
    "ears": _HEAD, "ears-l": _HEAD, "ears-r": _HEAD, "earwear": _HEAD,
    "neck": ("torso", "head"), "neckwear": ("torso", "head"),
    "topwear": _TORSO, "objects": _TORSO, "wings": _TORSO, "tail": _TORSO, "remainder": _LEGS,
    "handwear": _ARM["L"] + _ARM["R"][1:], "handwear-l": _ARM["L"], "handwear-r": _ARM["R"],
    "bottomwear": _LEGS, "legwear": _LEGS, "footwear": _FEET,
}
BACK_HAIR = "back hair"
# どの層にも入らなかった所の層(remainder_layer)
REMAINDER = "remainder"


def load_layers(psd_path: Path) -> tuple[tuple[int, int], list[tuple[str, np.ndarray]]]:
    """PSD の(画面の大きさ, [(層の名前, 画面いっぱいの RGBA)])。並びは奥から手前。"""
    from psd_tools import PSDImage

    psd = PSDImage.open(psd_path)
    w, h = psd.size
    layers = []
    for layer in psd.descendants():
        if layer.is_group() or not layer.is_visible():
            continue
        image = layer.topil()
        if image is None:
            continue
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        canvas.paste(image.convert("RGBA"), (layer.left, layer.top))
        layers.append((layer.name.strip().lower(), np.asarray(canvas)))
    if not layers:
        raise RigError("PSD に層がありません。")
    return (w, h), layers


def _composite(size: tuple[int, int], layers: list[tuple[str, np.ndarray]]) -> Image.Image:
    """層を重ねた絵(白い背景)。骨格を取るのに使う。"""
    out = Image.new("RGBA", size, (255, 255, 255, 255))
    for _, rgba in layers:
        out.alpha_composite(Image.fromarray(rgba, "RGBA"))
    return out.convert("RGB")


def _layer_mesh(name: str, index: int, alpha: np.ndarray, bones: tuple[str, ...], j: dict[str, np.ndarray],
                texture: str) -> dict | None:
    region = cv2.dilate((alpha > 8).astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))).astype(bool)
    if region.sum() < 30:
        return None
    shoulder = float(np.linalg.norm(j["left_shoulder"] - j["right_shoulder"]))
    verts, tris = _region_mesh(region, _GRID * shoulder * (0.6 if len(bones) == 1 else 1.0))
    if len(bones) == 1:
        table = np.ones((len(verts), 1))
    else:
        anchor = None
        for side, joint in (("L", "left_shoulder"), ("R", "right_shoulder")):
            if f"upper_arm.{side}" in bones and "torso" in bones:
                radius = BONES[_NAMES.index(f"upper_arm.{side}")].radius * _SHOULDER_CAP * shoulder
                anchor = ("torso", joint, float(radius))
        table = weights(verts[:, 0], verts[:, 1], j, bones, anchor)
        if name == REMAINDER:
            # 腰の関節より上は胴だけで動かす(脚の骨に引かれると、上の服の層の下の縁と離れて、継ぎ目が透けた)
            hip = (j["left_hip"][1] + j["right_hip"][1]) / 2
            above = np.clip((hip - verts[:, 1]) / (0.1 * shoulder), 0.0, 1.0)[:, None]
            torso = np.zeros_like(table)
            torso[:, bones.index("torso")] = 1.0
            table = table * (1 - above) + torso * above
    return {
        "name": name, "z": index, "bones": list(bones), "texture": texture,
        "vertices": verts.round(1).tolist(), "triangles": tris.tolist(), "weights": np.asarray(table).round(4).tolist(),
    }


def remainder_layer(source: Path, size: tuple[int, int], layers: list[tuple[str, np.ndarray]]) -> np.ndarray | None:
    """
    元の絵の人物のうち、どの層にも入っていない所(see-through が層を出さなかった部位。素体スーツの腰まわりなど)の RGBA。
    see-through は絵の長い辺を画面に合わせて中ほどに置くので、同じ置き方で重ねる。小さなかけら(層の縁)は外す。無ければ None。
    """
    from .anime_seg import person_mask

    w, h = size
    image = Image.open(source).convert("RGB")
    scale = max(w, h) / max(image.size)
    nw, nh = round(image.width * scale), round(image.height * scale)
    left, top = (w - nw) // 2, (h - nh) // 2
    pixels = np.zeros((h, w, 3), np.uint8)
    pixels[top:top + nh, left:left + nw] = np.asarray(image.resize((nw, nh), Image.Resampling.LANCZOS))
    person = np.zeros((h, w), bool)
    person[top:top + nh, left:left + nw] = np.asarray(Image.fromarray(person_mask(image) > 0.5).resize((nw, nh)))
    # 髪の層は数えない(元の絵で髪がかかっていた腰などは、髪の層が揺れると見えるので、残りの層に入れておく)
    covered = np.zeros_like(person)
    trunk = np.zeros_like(person)
    hair = np.zeros_like(person)
    for name, rgba in layers:
        if name.endswith("hair"):
            hair |= rgba[..., 3] > 8
        else:
            covered |= rgba[..., 3] > 8
        if name in ("topwear", "bottomwear", "legwear"):
            trunk |= rgba[..., 3] > 8

    def closed(region: np.ndarray, ratio: float) -> np.ndarray:
        k = max(15, int(min(w, h) * ratio)) | 1
        return cv2.morphologyEx(region.astype(np.uint8), cv2.MORPH_CLOSE,
                                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))).astype(bool)

    # 体の形の内側だけ(体の外に垂れた髪の所まで入れると、髪が揺れたとき白いもやが見えた)。胴と脚の層は大きく閉じて、
    # 上の服と脚の層の間(腰・股)を入れる(小さく閉じると股に丸い穴が残った)。腕は巻き込まないよう、ほかは小さく閉じる
    inside = closed(covered, 0.04) | closed(trunk, 0.1)
    # 層と層の継ぎ目の細い筋も入れる(外すと、腰の継ぎ目に白い点が並んだ)。点のような小さなかけらだけ外す
    rest = (person & ~covered & inside).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(rest, connectivity=8)
    keep = [i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= 20]
    if not keep or sum(stats[i, cv2.CC_STAT_AREA] for i in keep) < 300:
        return None
    region = np.isin(labels, keep)
    # 隣の層の下へ少し入り込ませる(つなぎ目に透けた線を出さない)
    region = cv2.dilate(region.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool) & person
    # 背景の色の所(太ももの間のすき間など。体の形を閉じると入ってしまう)は入れない
    # (元の絵の中だけで。置いた絵のまわりの黒い余白を数えると、背景が暗い色になり、灰色のスーツまで外れた)
    outside = ~person
    outside[:top] = outside[top + nh:] = False
    outside[:, :left] = outside[:, left + nw:] = False
    if outside.any():
        background = np.median(pixels[outside], axis=0)
        region &= np.linalg.norm(pixels.astype(np.float32) - background, axis=2) > 24
    # 元の絵で髪がかかっていた所は、髪の色なので、まわりから描いて埋める(LaMa)。体の形の内側だけ(髪の層は股のあたりまで
    # 届くので、層のすぐそばだけにすると、股に丸い穴が開いた)
    region &= ~hair | inside
    under_hair = region & hair
    if under_hair.any():
        from .inpaint_local import inpaint

        count, labels = cv2.connectedComponents(under_hair.astype(np.uint8), connectivity=8)
        for index in range(1, count):
            pixels = inpaint(pixels, labels == index)
    return np.dstack([pixels, (region * 255).astype(np.uint8)])


def build_from_layers(psd_path: Path, out_dir: Path, *, hair: bool = True, breasts: bool = False, wind: float = 0.0,
                      source: Path | None = None) -> dict:
    """
    see-through の PSD から、out_dir に層ごとのテクスチャ・rig.json・overlay.png を書く(body_rig.build と同じ形の rig)。
    hair は後ろ髪に揺れの鎖を付け、breasts は上の服の層に胸の骨を付ける(成人キャラだけと呼び出し側で決める)。
    source(分ける前の元の絵)があれば、どの層にも入らなかった所を後ろ髪のすぐ前の層(remainder。胴と脚の骨)にする。
    """
    size, layers = load_layers(psd_path)
    if source is not None:
        rest = remainder_layer(source, size, layers)
        if rest is not None:
            # 後ろ髪のすぐ前(体の一部なので。いちばん奥に置くと、腰の間から後ろ髪の毛先が透けて見えた)
            at = next((i + 1 for i, (name, _) in enumerate(layers) if name == BACK_HAIR), 0)
            layers = layers[:at] + [(REMAINDER, rest)] + layers[at:]
    composite = _composite(size, layers)
    mask, points = detect(composite)
    j = joints(points, mask)

    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "layers").mkdir(parents=True)
    meshes: list[dict] = []
    extra: list[dict] = []
    seen: dict[str, int] = {}
    for index, (name, rgba) in enumerate(layers):
        # 同じ名前の層が重なったら番号を付ける(メッシュの名前は一意にする)
        seen[name] = seen.get(name, 0) + 1
        mesh_name = name if seen[name] == 1 else f"{name}#{seen[name]}"
        texture = f"layers/{index:02d}_{mesh_name.replace(' ', '_').replace('#', '_')}.png"
        Image.fromarray(rgba, "RGBA").save(out_dir / texture)
        alpha = rgba[..., 3]
        if name == BACK_HAIR and hair:
            hanging = alpha > 8
            chains = body_physics.hair_chains(hanging, j)
            if chains:
                bones = _hair_bones(chains)
                extra += bones
                mesh = _hair_mesh(hanging, bones, j, name=mesh_name, z=index, texture=texture)
                meshes.append(mesh)
                continue
        bones = LAYER_BONES.get(name, _HEAD if name.endswith("hair") else _TORSO)
        mesh = _layer_mesh(mesh_name, index, alpha, bones, j, texture)
        if mesh is not None:
            meshes.append(mesh)
    if breasts:
        top = [m for m in meshes if m["name"] == "topwear"]
        if top:
            for mesh in top:
                mesh["name"] = "body"  # _add_breasts は体のメッシュに付ける
            extra += _add_breasts(meshes, j)
            for mesh in top:
                mesh["name"] = "topwear"

    rig = {
        "source": psd_path.name,
        "kind": "see_through",
        "size": list(size),
        "texture": meshes[0]["texture"] if meshes else "",
        "outfit": None,
        "outfit_drift": None,
        "joints": {k: [round(float(v[0]), 2), round(float(v[1]), 2)] for k, v in j.items()},
        "bones": [
            {"name": b.name, "label": b.label, "parent": b.parent,
             "head": [float(v) for v in j[b.head]], "tail": [float(v) for v in j[b.tail]]}
            for b in BONES
        ] + extra,
        "meshes": meshes,
        "physics": {
            "hair": sorted({b["chain"] for b in extra if b.get("kind") == "hair"}),
            "breasts": any(b.get("kind") == "breast" for b in extra),
            "wind": wind,
        },
    }
    (out_dir / "rig.json").write_text(json.dumps(rig, ensure_ascii=False), encoding="utf-8")
    shutil.copy(psd_path, out_dir / "layers.psd")
    _overlay(composite, rig).save(out_dir / "overlay.png")
    return rig
