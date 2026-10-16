"""
うごイラの E(ugoira_rig.py と POST /api/ugoira/rig)のテスト。人物の切り分け・骨格・点で指す切り出し・裏を埋めるモデルは
差し替える(モデルを取ってこない)。

実行方法:
  uv run pytest tests/test_ugoira_rig.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import ugoira_rig as rig  # noqa: E402

# 絵は 256×384(64 の倍数なので、うごイラを作るときに縮めない)
HEAD = (80.0, 60.0, 180.0, 170.0)


def test_pose_template_from_prompt_tags() -> None:
    assert rig.pose_template("1girl, lying, on back, bed").name == "on_back"
    assert rig.pose_template("1girl, on stomach").name == "on_stomach"
    assert rig.pose_template("1girl, from side, profile").name == "profile"
    assert rig.pose_template("1girl, from behind, long hair").name == "from_behind"
    assert rig.pose_template("1girl, squatting").name == "squatting"
    assert rig.pose_template("1girl, sitting, chair").name == "sitting"
    # 手がかりが無ければ立ち・上半身。言葉の一部だけ同じタグ(sideburns など)には当てない
    assert rig.pose_template("1girl, upper body, smile").name == "standing"
    assert rig.pose_template("1girl, sideburns, onside").name == "standing"
    assert not rig.pose_template("lying on back").hair_swing


def test_spring_follows_with_overshoot_and_settles() -> None:
    target = np.concatenate([np.zeros(10), np.ones(600)])
    anime = rig._follow(target, 1 / 120, 2.2, 0.45)
    smooth = rig._follow(target, 1 / 120, 1.6, 0.9)
    # アニメ調は少し行き過ぎてから止まる。リアル寄りはほとんど行き過ぎない。どちらも最後は目標に落ち着く
    assert anime.max() > 1.1 and smooth.max() < 1.03
    assert abs(anime[-1] - 1) < 0.01 and abs(smooth[-1] - 1) < 0.01


def test_chain_tips_lag_behind_the_root() -> None:
    steps = 240
    root = np.zeros((steps, 2))
    root[20:, 0] = 10.0
    sim = rig._chain(root, np.zeros((steps, 6, 2)), 1 / 120, 6, 55.0, 5.0)
    # 根元が動いた直後、毛先はまだ追いついていない(遅れて付いてくる)
    assert sim[25, 0, 0] == 10.0 and sim[25, -1, 0] < 2.0
    assert abs(sim[-1, -1, 0] - 10.0) < 1.5


def _parts(template: rig.PoseTemplate = rig.STANDING) -> rig.Parts:
    height, width = 384, 256
    person = np.zeros((height, width), np.float32)
    person[50:, 60:200] = 1.0
    hair = np.zeros_like(person)
    hair[50:330, 50:80] = 1.0
    hair[50:330, 180:210] = 1.0
    head = np.zeros_like(person)
    head[50:180, 75:185] = 1.0
    hanging = hair.copy()
    hanging[:190] = 0
    return rig.Parts(person, hair, head, hanging, (130.0, 190.0), HEAD, template)


def _striped(size=(256, 384)) -> Image.Image:
    xs = np.arange(size[0])[None, :] + np.arange(size[1])[:, None]
    value = ((xs // 6) % 2 * 180 + 40).astype(np.uint8)
    return Image.fromarray(np.stack([value] * 3, axis=-1))


@pytest.fixture()
def no_models(monkeypatch: pytest.MonkeyPatch) -> None:
    from python import inpaint_local

    # 裏を埋めるモデルの代わりに、埋める所を灰色にする
    def fill(image: np.ndarray, hole: np.ndarray) -> np.ndarray:
        out = image.copy()
        out[hole] = 128
        return out

    monkeypatch.setattr(inpaint_local, "inpaint", fill)


@pytest.mark.parametrize("kind,style,count", [("hair", "anime", 12), ("hair", "real", 24), ("tilt", "anime", 12), ("tilt", "real", 24)])
def test_render_moves_only_the_parts(no_models, kind: str, style: str, count: int) -> None:
    source = _striped()
    frames = rig.render(source, _parts(), kind, style)
    assert len(frames) == count
    first = np.asarray(frames[0][0], np.int16)
    original = np.asarray(source, np.int16)
    # 最初のコマは元の絵のまま。どこかのコマは動く。人物から離れた背景(左上の角)は動かない
    assert np.array_equal(first, original)
    assert max(np.abs(np.asarray(f, np.int16) - original).max() for f, _ in frames) > 50
    for frame, _ in frames:
        assert np.array_equal(np.asarray(frame, np.int16)[:30, :30], original[:30, :30])
    assert frames[0][1] == round(rig.RIGS[kind][1] / count)


def test_lying_pose_does_not_swing_hair(no_models) -> None:
    with pytest.raises(rig.RigError):
        rig.render(_striped(), _parts(rig.pose_template("lying on back")), "hair", "anime")
    with pytest.raises(rig.RigError):
        rig.render(_striped(), _parts(), "dance", "anime")


def test_rig_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_models) -> None:
    import time

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from python import anime_seg, db, trash, ugoira
    from python.routes import library, ugoira as ugoira_routes

    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "data" / "app.db")
    for module in (ugoira, trash, library):
        monkeypatch.setattr(module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library, "_OUTPUTS", tmp_path / "outputs")
    monkeypatch.setattr(library, "_served_path", lambda path: tmp_path / path)
    monkeypatch.setattr("python.image_tagger.tag_image", lambda img: [])
    monkeypatch.setattr("python.manga_v2.detect.detect_heads_in_image", lambda img: [HEAD])
    parts = _parts()
    monkeypatch.setattr(anime_seg, "person_mask", lambda image: parts.person)
    monkeypatch.setattr(rig, "detect_pose", lambda image, box: None)
    seen: list[str] = []

    def split(image, head, person, skeleton, template):
        seen.append(template.name)
        return rig.Parts(parts.person, parts.hair, parts.head, parts.hanging, parts.pivot, head, template)

    monkeypatch.setattr(rig, "split_parts", split)
    (tmp_path / "outputs" / "history").mkdir(parents=True)
    _striped().save(tmp_path / "outputs" / "history" / "src.png")
    conn = db.get_connection()
    try:
        row = db.record_generation(
            conn, prompt="1girl, from side, long hair", negative_prompt=None, model="nai-diffusion-4-5-full", size="[256, 384]",
            steps=28, scale=5.0, seed=7, chunk_ids=[], image_paths=["outputs/history/src.png"],
        )
    finally:
        conn.close()
    app = FastAPI()
    app.include_router(ugoira_routes.router)
    with TestClient(app) as client:
        info = client.get("/api/ugoira/motions").json()
        assert {r["kind"] for r in info["rigs"]} == {"hair", "tilt"} and set(info["styles"]) == {"anime", "real"}
        job = client.post("/api/ugoira/rig", json={"key": f"history:{row['id']}:src.png", "kind": "hair", "style": "real"}).json()
        for _ in range(200):
            job = client.get(f"/api/ugoira/jobs/{job['id']}").json()
            if job["status"] != "running":
                break
            time.sleep(0.05)
        assert job["status"] == "done", job
        # 構図はプロンプトのタグから(横顔)
        assert seen == ["profile"] and "横顔" in job["message"]
        project = client.get(f"/api/ugoira/{job['project_id']}").json()
        assert project["method"] == "rig" and len(project["frames"]) == 24
