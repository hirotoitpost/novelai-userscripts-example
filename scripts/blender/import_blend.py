"""
Blender の中で動かすスクリプト(model_3d.import_blend から、画面なしで呼ぶ)。

    blender --background <model_dir>/model.blend --python scripts/blender/import_blend.py -- <model_dir>

利用者が Blender で直して保存した model.blend の体(いちばん頂点の多いメッシュ。部品 part_* は除く)を、骨を入れたときに
残した比べる元(rig_ref.npz。rig_model.add_edit_key)と比べ、直した所を blender_edits.json に書く:
- sculpt: 手直しのシェイプキー(edit)の位置と、直す前の頂点の位置との違い(edit に彫った所。編集モードで体の形を動かすと
  edit も一緒に動くので、それも入る。前に取り込んで焼き込んだ分も入る)。
- keys: ほかのシェイプキーごとの、体の形からの動きの、作ったままの動きからの変わり(そのシェイプキーを選んで彫った所)。
動いた頂点だけを、直す前の位置と動きの組で残す(骨を入れ直すと頂点の番号が変わることがあるので、位置で移す)。毎回、前に
取り込んだ分も含めて全部を書き直す。結果(数)か、取り込めない理由(error)を import.json に書く。
"""

import json
import sys
from pathlib import Path

import bpy
import numpy as np

EDIT_KEY = "edit"
EDITS_FILE = "blender_edits.json"
REF_FILE = "rig_ref.npz"
# これより小さい動き(m)は、直していないとみなす
MOVED = 1e-5


def body() -> bpy.types.Object:
    meshes = [o for o in bpy.data.objects if o.type == "MESH" and not o.name.startswith("part_")]
    if not meshes:
        fail("体のメッシュがありません")
    return max(meshes, key=lambda o: len(o.data.vertices))


def key_arrays(obj: bpy.types.Object) -> dict[str, np.ndarray]:
    result = {}
    for kb in obj.data.shape_keys.key_blocks if obj.data.shape_keys else []:
        data = np.empty(len(kb.data) * 3)
        kb.data.foreach_get("co", data)
        result[kb.name] = data.reshape(-1, 3)
    return result


def sparse(orig: np.ndarray, delta: np.ndarray) -> dict | None:
    moved = np.linalg.norm(delta, axis=1) > MOVED
    if not moved.any():
        return None
    return {"ref": np.round(orig[moved], 6).tolist(), "delta": np.round(delta[moved], 6).tolist()}


class ImportFailed(Exception):
    """取り込めない(理由は import.json の error に書く。Windows の Blender の標準出力は日本語が化けることがあるので)。"""


def fail(message: str) -> None:
    raise ImportFailed(message)


def main() -> None:
    folder = Path(sys.argv[sys.argv.index("--") + 1])
    try:
        report = compare(folder)
    except ImportFailed as exc:
        report = {"error": str(exc)}
    (folder / "import.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")


def compare(folder: Path) -> dict:
    if not (folder / REF_FILE).is_file():
        fail("比べる元がありません。骨を入れ直してから、Blender で開いて直してください")
    ref = np.load(folder / REF_FILE)
    orig = ref["orig"].astype(float)
    made = {name[len("made:"):]: ref[name].astype(float) for name in ref.files if name.startswith("made:")}
    now = key_arrays(body())
    if "Basis" not in now:
        fail("シェイプキー(Basis)がありません")
    if len(now["Basis"]) != len(orig):
        fail(
            f"頂点の数が変わっています({len(orig)} → {len(now['Basis'])})。リメッシュ・ダイナミックトポロジーなど、"
            "頂点を足したり減らしたりする直し方は取り込めません"
        )
    # 体の形の直し: edit の位置(無ければ体の形)- 直す前の位置
    sculpt = sparse(orig, now.get(EDIT_KEY, now["Basis"]) - orig)
    keys = {}
    for name, offset in made.items():
        if name not in now:
            continue
        change = sparse(orig, (now[name] - now["Basis"]) - offset)
        if change:
            keys[name] = change
    edits = {k: v for k, v in {"sculpt": sculpt, "keys": keys}.items() if v}
    edits_path = folder / EDITS_FILE
    if edits:
        edits_path.write_text(json.dumps(edits), encoding="utf-8")
    else:
        edits_path.unlink(missing_ok=True)
    return {
        "sculpt": len(sculpt["ref"]) if sculpt else 0,
        "keys": {name: len(change["ref"]) for name, change in keys.items()},
        # 足したシェイプキー(まだ取り込めない)
        "added": sorted(name for name in now if name not in made and name not in ("Basis", EDIT_KEY)),
    }


main()
