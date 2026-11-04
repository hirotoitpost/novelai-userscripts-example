"""
Blender の中で動かすスクリプト(model_3d.render_anime から、画面なしで呼ぶ)。

    blender --background <model.blend|model_dressed.blend> --python scripts/blender/anime_render.py -- <anime.json> <out_dir>

骨を入れた形(服を着せた形)を、アニメ塗りで動かして描く(<out_dir>/<番号>.png。透明の背景)。
- 塗り: 光の当たり方を 3 段(明るい所・ふつう・影)の平らな色に分ける(EEVEE の Shader to RGB → 段の色)。色は
  anime.json の palette(キャラの絵から取った色。toon.py)。髪にはツヤ(光の照り返しを段にしたもの)を足す。
- 線: 裏返した殻(Solidify を外へ厚くし、表を隠した濃い色の面)で輪郭を描く。
- 顔: 正面の絵(face.image)を正面から映し、絵の肌の色で割って、目・眉・口・線の濃さだけを肌の色に掛ける。顔の向きが
  横を向く所・あごより下・頭のてっぺんは映さない(face_w の重み)。顔は影を浅くする(アニメの顔は明るい)。
- 動き: motion の動き(NLA の帯は止める)を step コマおきに描く(2 で 1 秒 12 コマ)。カメラは camera(°。0 で正面、
  右回り)から、休んだ姿勢の体の大きさで決めた所に置いたまま動かさない。
"""

import json
import math
import sys
from pathlib import Path

import bpy

LINE = 0.0025  # 線の太さ(m。髪は 0.6 倍)
SHADOW_AT, LIGHT_AT = 0.18, 0.62  # 段の境(光の強さ。これより暗いと影・明るいと明るい所)
FACE_LIFT = 0.35  # 顔の影を浅くする量(光の強さに足す)
SPEC_AT = 0.5  # 髪のツヤの出る照り返しの強さ


def linear(c) -> tuple[float, float, float, float]:
    """sRGB(0〜255)→ Blender の色(線形)。"""

    def f(x: float) -> float:
        x /= 255
        return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4

    return (f(c[0]), f(c[1]), f(c[2]), 1.0)


def mix(nt, kind: str, a, b, factor=None):
    """色を混ぜる節(kind: MIX・MULTIPLY・DIVIDE・DARKEN・ADD)。a・b は出力か色。"""
    node = nt.nodes.new("ShaderNodeMix")
    node.data_type = "RGBA"
    node.blend_type = kind
    if factor is None:
        node.inputs["Factor"].default_value = 1.0
    else:
        nt.links.new(factor, node.inputs["Factor"])
    for slot, value in ((6, a), (7, b)):
        if isinstance(value, tuple):
            node.inputs[slot].default_value = value
        else:
            nt.links.new(value, node.inputs[slot])
    return node.outputs[2]


def light_value(nt):
    """光の強さ(Diffuse を Shader to RGB で明るさにしたもの)。"""
    to_rgb = nt.nodes.new("ShaderNodeShaderToRGB")
    nt.links.new(nt.nodes.new("ShaderNodeBsdfDiffuse").outputs[0], to_rgb.inputs[0])
    gray = nt.nodes.new("ShaderNodeRGBToBW")
    nt.links.new(to_rgb.outputs[0], gray.inputs[0])
    return gray.outputs[0]


def steps(nt, value, tones):
    """光の強さを 3 段の色に。"""
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.interpolation = "CONSTANT"
    hi, base, shadow = tones
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = 0.0, linear(shadow)
    els[1].position, els[1].color = SHADOW_AT, linear(base)
    els.new(LIGHT_AT).color = linear(hi)
    nt.links.new(value, ramp.inputs[0])
    return ramp.outputs[0]


def finish(nt, color) -> None:
    emit = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(color, emit.inputs[0])
    nt.links.new(emit.outputs[0], nt.nodes.new("ShaderNodeOutputMaterial").inputs[0])


def new_material(name: str):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    return mat, mat.node_tree


def flat(name: str, tones):
    mat, nt = new_material(name)
    finish(nt, steps(nt, light_value(nt), tones))
    return mat


def hair_material(tones, shine):
    """髪: 3 段の色に、照り返し(Glossy)を段にしたツヤを足す。"""
    mat, nt = new_material("hair")
    color = steps(nt, light_value(nt), tones)
    gloss = nt.nodes.new("ShaderNodeBsdfGlossy")
    gloss.inputs["Roughness"].default_value = 0.35
    to_rgb = nt.nodes.new("ShaderNodeShaderToRGB")
    nt.links.new(gloss.outputs[0], to_rgb.inputs[0])
    gray = nt.nodes.new("ShaderNodeRGBToBW")
    nt.links.new(to_rgb.outputs[0], gray.inputs[0])
    band = nt.nodes.new("ShaderNodeMath")
    band.operation = "GREATER_THAN"
    band.inputs[1].default_value = SPEC_AT
    nt.links.new(gray.outputs[0], band.inputs[0])
    finish(nt, mix(nt, "MIX", color, linear(shine), band.outputs[0]))
    return mat


def skin_material(tones, face: dict | None):
    """肌: 顔の重み(face_w)の所は影を浅くし、顔の絵の濃さ(目・眉・口・線)を掛ける。"""
    mat, nt = new_material("skin_face" if face else "skin")
    value = light_value(nt)
    if face is None:
        finish(nt, steps(nt, value, tones))
        return mat
    weight = nt.nodes.new("ShaderNodeAttribute")
    weight.attribute_name = "face_w"
    lift = nt.nodes.new("ShaderNodeMath")
    lift.operation = "MULTIPLY_ADD"
    nt.links.new(weight.outputs["Fac"], lift.inputs[0])
    lift.inputs[1].default_value = FACE_LIFT
    nt.links.new(value, lift.inputs[2])
    color = steps(nt, lift.outputs[0], tones)
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(face["image"])
    tex.extension = "CLIP"
    tex.interpolation = "Cubic"
    uv = nt.nodes.new("ShaderNodeUVMap")
    uv.uv_map = "face"
    nt.links.new(uv.outputs[0], tex.inputs[0])
    # 絵の肌の色で割って 1 を超えない所(目・眉・口・線)だけを濃さにする
    ink = mix(nt, "DARKEN", mix(nt, "DIVIDE", tex.outputs[0], linear(face["skin"])), (1.0, 1.0, 1.0, 1.0))
    ink = mix(nt, "MIX", (1.0, 1.0, 1.0, 1.0), ink, weight.outputs["Fac"])
    finish(nt, mix(nt, "MULTIPLY", color, ink))
    return mat


def project_face(arm, body, face: dict) -> None:
    """休んだ姿勢の体に、正面の絵を正面から映す UV(face)と、顔の重み(face_w)を付ける。絵の人物の高さ = 体の高さ。"""
    w, h = face["size"]
    x0, y0, x1, y1 = face["bbox"]
    mesh = body.data
    world = body.matrix_world
    co = [world @ v.co for v in mesh.vertices]
    normals = [(world.to_3x3() @ v.normal).normalized() for v in mesh.vertices]
    lo, hi = min(c.z for c in co), max(c.z for c in co)
    scale = (y1 - y0) / (hi - lo)
    cx = (min(c.x for c in co) + max(c.x for c in co)) / 2
    head = arm.data.bones["head"]
    chin = (arm.matrix_world @ head.head_local).z + face.get("chin", 0.045)
    crown = (arm.matrix_world @ head.tail_local).z + 0.05
    weights = mesh.color_attributes.new("face_w", "FLOAT_COLOR", "POINT")
    for i, (c, n) in enumerate(zip(co, normals)):
        front = min(1.0, max(0.0, (-n.y - 0.15) / 0.35))
        below = min(1.0, max(0.0, (c.z - chin) / 0.015))
        above = min(1.0, max(0.0, (crown - c.z) / 0.02))
        value = front * below * above
        weights.data[i].color = (value, value, value, 1.0)
    uv = mesh.uv_layers.new(name="face")
    for loop in mesh.loops:
        c = co[loop.vertex_index]
        px = (x0 + x1) / 2 + (c.x - cx) * scale
        py = y1 - (c.z - lo) * scale
        uv.data[loop.index].uv = (px / w, 1 - py / h)


def outline(obj, line_mat, thickness: float) -> None:
    mod = obj.modifiers.new("outline", "SOLIDIFY")
    mod.thickness = thickness
    mod.offset = 1
    mod.use_flip_normals = True
    mod.use_rim = False
    mod.material_offset = len(obj.data.materials)
    obj.data.materials.append(line_mat)


def rest(arm) -> None:
    if arm.animation_data:
        arm.animation_data.action = None
        for track in arm.animation_data.nla_tracks:
            track.mute = True
    for pb in arm.pose.bones:
        pb.matrix_basis.identity()
    bpy.context.view_layer.update()


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    spec = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    palette = spec["palette"]
    scene = bpy.context.scene
    arm = bpy.data.objects["rig"]
    rest(arm)
    face = spec.get("face")
    if face:
        project_face(arm, bpy.data.objects["body"], face)

    line_mat, nt = new_material("line")
    emit = nt.nodes.new("ShaderNodeEmission")
    emit.inputs[0].default_value = linear(palette["lines"])
    nt.links.new(emit.outputs[0], nt.nodes.new("ShaderNodeOutputMaterial").inputs[0])
    line_mat.use_backface_culling = True

    skin = skin_material(palette["skin"], None)
    skin_face = skin_material(palette["skin"], face) if face else skin
    hair = hair_material(palette["hair"], palette.get("shine", palette["hair"][0]))
    garments = palette.get("garments", {})
    weights = spec.get("shapes") or {}
    for obj in [o for o in bpy.data.objects if o.type == "MESH"]:
        if obj.name.startswith("part_"):
            # 形状パーツ(部品)は、強さが 0 なら隠す(pose_render.py と同じ)
            obj.hide_render = float(weights.get(obj.name[len("part_"):-2], 0.0)) <= 0
        keys = obj.data.shape_keys
        if keys is not None:
            for block in keys.key_blocks[1:]:
                block.value = float(weights.get(block.name, 0.0))
        obj.data.materials.clear()
        for poly in obj.data.polygons:
            poly.use_smooth = True
        if obj.name == "body":
            mat = skin_face
        elif obj.name == "hair":
            mat = hair
        elif obj.name.startswith("garment_"):
            key = next((k for k in garments if obj.name[len("garment_"):].startswith(k)), None)
            mat = flat(obj.name, garments[key] if key else palette["garment"])
        else:  # 手・形状パーツ
            mat = skin
        obj.data.materials.append(mat)
        outline(obj, line_mat, LINE * (0.6 if obj.name == "hair" else 1.0))

    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.view_settings.view_transform = "Standard"
    scene.render.film_transparent = True
    world = bpy.data.worlds.new("anime")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.0
    scene.world = world
    sun = bpy.data.objects.new("anime_sun", bpy.data.lights.new("anime_sun", "SUN"))
    sun.data.energy = 3.0
    # 落ちる影は描かない(体の面の細かいでこぼこが、影の粒になった)
    sun.data.use_shadow = False
    sun.rotation_euler = (math.radians(50), 0, math.radians(-30))
    scene.collection.objects.link(sun)

    width, height_px = spec.get("size", [512, 768])
    scene.render.resolution_x, scene.render.resolution_y = width, height_px
    for obj in [o for o in bpy.data.objects if o.type == "CAMERA"]:
        bpy.data.objects.remove(obj)
    cam = bpy.data.objects.new("anime_camera", bpy.data.cameras.new("anime_camera"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.data.lens = 50
    cam.data.sensor_fit = "VERTICAL"
    tall = (arm.matrix_world @ arm.data.bones["head"].tail_local).z
    fov = 2 * math.atan(cam.data.sensor_height / 2 / cam.data.lens)
    distance = tall * 0.62 / math.tan(fov / 2)
    a = math.radians(spec.get("camera", 0))
    cam.location = (math.sin(a) * distance, -math.cos(a) * distance, tall * 0.52)
    cam.rotation_euler = (math.radians(90), 0, a)

    action = bpy.data.actions[spec["motion"]]
    arm.animation_data_create()
    arm.animation_data.action = action
    start, end = (int(round(x)) for x in action.frame_range)
    for i, frame in enumerate(range(start, end + 1, spec.get("step", 2))):
        scene.frame_set(frame)
        scene.render.filepath = str(out / f"{i:03d}.png")
        bpy.ops.render.render(write_still=True)


main()
