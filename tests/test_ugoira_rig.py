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

    def split(image, head, person, skeleton, template, hair=None):
        seen.append(template.name)
        used = parts.hair if hair is None else hair
        return rig.Parts(parts.person, used, parts.head, parts.hanging, parts.pivot, head, template)

    monkeypatch.setattr(rig, "split_parts", split)
    (tmp_path / "outputs" / "history").mkdir(parents=True)
    # 髪の所は一色(濃い茶色)。描いた胴体に髪が残ったかを、色で確かめるため
    picture = np.asarray(_striped()).copy()
    picture[parts.hair > 0.5] = (60, 40, 30)
    Image.fromarray(picture).save(tmp_path / "outputs" / "history" / "src.png")
    conn = db.get_connection()
    try:
        row = db.record_generation(
            conn, prompt="1girl, from side, long hair", negative_prompt=None, model="nai-diffusion-4-5-full", size="[256, 384]",
            steps=28, scale=5.0, seed=7, chunk_ids=[], image_paths=["outputs/history/src.png"],
        )
    finally:
        conn.close()
    from python.client import get_client
    from python.routes import image as image_routes

    drawn_colors: list[tuple[int, int, int]] = []

    class FakeImage:
        async def generate(self, params):
            # 描いた胴体: 決めた色の一色(髪の色なら「髪が残った」になる)
            drawn_colors.append(params.seed)
            return [Image.new("RGB", params.size, fake_color[0])]

    class FakeClient:
        api_key = "test"
        image = FakeImage()

        async def close(self):
            pass

    fake_color = [(0, 160, 0)]
    monkeypatch.setattr(image_routes, "_job_client", lambda api_key: FakeClient())
    app = FastAPI()
    app.include_router(ugoira_routes.router)
    app.dependency_overrides[get_client] = lambda: FakeClient()
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

        # 半自動: 髪の範囲をタップで直す(切り出しは差し替え。タップした所のまわり 20 画素角)
        pid = project["id"]
        state = client.get(f"/api/ugoira/{pid}/rig").json()
        assert state["source"].endswith("rig_source.png") and not state["can_undo"]
        taps: list[tuple] = []

        def zoom(image, point, detail, cache_key=None, prefer=None):
            taps.append((point, detail))
            region = np.zeros((image.height, image.width), np.float32)
            region[int(point[1]) - 10 : int(point[1]) + 10, int(point[0]) - 10 : int(point[0]) + 10] = 1.0
            return region

        monkeypatch.setattr(rig, "zoom_segment", zoom)
        hair_file = tmp_path / "outputs" / "ugoira" / str(pid) / "rig_hair.png"
        before = np.asarray(Image.open(hair_file))
        assert before[250, 128] == 0
        edited = client.post(f"/api/ugoira/{pid}/rig/edit", json={"x": 128, "y": 250, "mode": "add"}).json()
        # 小さい絵(256×384)なので、高精細(AI 拡大)を自動で使う
        assert edited["can_undo"] and edited["detail"] is True and taps[-1][1] is True
        assert np.asarray(Image.open(hair_file))[250, 128] == 255
        client.post(f"/api/ugoira/{pid}/rig/edit", json={"x": 128, "y": 250, "mode": "remove", "detail": False})
        assert np.asarray(Image.open(hair_file))[250, 128] == 0 and taps[-1][1] is False
        client.post(f"/api/ugoira/{pid}/rig/undo")
        assert np.asarray(Image.open(hair_file))[250, 128] == 255
        assert client.post(f"/api/ugoira/{pid}/rig/undo").status_code == 409
        client.post(f"/api/ugoira/{pid}/rig/reset")
        assert np.array_equal(np.asarray(Image.open(hair_file)), before)
        assert client.post(f"/api/ugoira/{pid}/rig/edit", json={"x": 999, "y": 10}).status_code == 422

        # いまの範囲で作り直す: 同じうごイラのコマが入れ替わり、前のコマの絵は消える
        client.post(f"/api/ugoira/{pid}/rig/edit", json={"x": 128, "y": 250, "mode": "add"})
        old = [tmp_path / f["path"] for f in project["frames"]]
        job = client.post(f"/api/ugoira/{pid}/rig/redo").json()
        for _ in range(200):
            job = client.get(f"/api/ugoira/jobs/{job['id']}").json()
            if job["status"] != "running":
                break
            time.sleep(0.05)
        assert job["status"] == "done", job
        redone = client.get(f"/api/ugoira/{pid}").json()
        assert len(redone["frames"]) == 24 and not any(p.exists() for p in old)
        # 裏を NovelAI で描いて作り直す: 髪が残らなければ1回で使う。残り続けたら3回で LaMa に戻す
        def redo(body):
            job = client.post(f"/api/ugoira/{pid}/rig/redo", json=body).json()
            for _ in range(200):
                job = client.get(f"/api/ugoira/jobs/{job['id']}").json()
                if job["status"] != "running":
                    return job
                time.sleep(0.05)

        job = redo({"plate": "novelai"})
        assert job["status"] == "done" and "NovelAI(1回目)" in job["message"], job
        assert client.get(f"/api/ugoira/{pid}/rig").json()["settings"]["plate"] == "novelai"
        fake_color[0] = (60, 40, 30)
        drawn_colors.clear()
        job = redo(None)
        assert job["status"] == "done" and "LaMa" in job["message"] and len(drawn_colors) == 3, job
        # ふつうのうごイラは、髪の範囲を直せない
        other = ugoira.create_project("x", "k", "partial")
        assert client.get(f"/api/ugoira/{other}/rig").status_code == 409


def test_zoom_segment_maps_back_to_the_picture(monkeypatch: pytest.MonkeyPatch) -> None:
    from python import point_segment

    encoded: list[tuple[int, int]] = []

    def encode(image):
        encoded.append(image.size)
        return point_segment.Features(np.zeros(1), image.size, 1.0)

    def segment(features, positives, negatives=()):
        # 拡大した区画で、タップした所を中心に 40 画素角(元の絵では 10 画素角)
        w, h = features.size
        x, y = positives[0]
        mask = np.zeros((h, w), bool)
        mask[int(y) - 20 : int(y) + 20, int(x) - 20 : int(x) + 20] = True
        return [(mask, 0.9)]

    monkeypatch.setattr(point_segment, "encode", encode)
    monkeypatch.setattr(point_segment, "segment", segment)
    rig._zoom_cache.clear()
    picture = _striped((600, 800))
    region = rig.zoom_segment(picture, (300.0, 400.0), cache_key="t")
    # 4倍に拡大した区画で切り出し、元の座標に戻す
    assert encoded == [(rig.ZOOM_TILE * 4, rig.ZOOM_TILE * 4)]
    ys, xs = np.nonzero(region > 0.5)
    assert 294 <= xs.min() <= 296 and 304 <= xs.max() <= 306 and 394 <= ys.min() <= 396
    # 同じ区画は覚えておく(2回目は特徴を作らない)
    rig.zoom_segment(picture, (305.0, 402.0), cache_key="t")
    assert len(encoded) == 1
    # 絵の端の近くは、区画を絵の中へずらす
    edge = rig.zoom_segment(picture, (5.0, 5.0))
    assert edge[5, 5] > 0.5
    assert np.array_equal(rig.edit_hair(np.zeros((2, 2)), np.ones((2, 2)), "add"), np.ones((2, 2)))
