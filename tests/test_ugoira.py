"""
うごイラ(ugoira.py と /api/ugoira)のテスト。頭の検出・判定モデル・NovelAI は差し替え、DB とファイルは一時フォルダにする。

実行方法:
  uv run pytest tests/test_ugoira.py -v
"""

from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db, trash, ugoira  # noqa: E402

HEAD = (100.0, 100.0, 300.0, 300.0)


def test_regions_prompts_and_negatives() -> None:
    assert ugoira.region_box(HEAD, "eyes", (400, 600)) == (130, 176, 270, 248)
    assert ugoira.region_box(HEAD, "hair", (400, 600))[0] == 20
    mask = ugoira.region_mask(HEAD, "hair", (400, 600))
    # 髪の範囲から顔は外す
    assert mask.getpixel((200, 240)) == 0 and mask.getpixel((40, 200)) == 255
    # 目の範囲では、目のタグを外す(色は残す)。コマのタグは強める
    prompt = ugoira.frame_prompt("1girl, yellow eyes, narrowed eyes, sanpaku, smile, brown hair", "eyes", "closed eyes")
    assert prompt == "1.4::closed eyes::, 1girl, yellow eyes, smile, brown hair"
    assert ugoira.frame_prompt("1girl, grin, open mouth, teeth", "mouth", "closed mouth") == "1.4::closed mouth::, 1girl"
    assert ugoira.frame_negative("closed eyes") == "open eyes, eyes visible, pupils, iris"
    assert ugoira.frame_negative("half-closed eyes") == "wide eyes, closed eyes"
    assert ugoira.fit_size((1600, 1600)) == (1024, 1024) and ugoira.fit_size((832, 1216)) == (832, 1216)
    with pytest.raises(ugoira.UgoiraError):
        ugoira.pick_head([])


def test_composite_keeps_outside_the_region() -> None:
    source = Image.new("RGB", (400, 600), "white")
    generated = Image.new("RGB", (400, 600), "black")
    mask = ugoira.region_mask(HEAD, "mouth", source.size)
    result = ugoira.composite(source, generated, mask)
    assert result.getpixel((10, 10)) == (255, 255, 255)
    # 範囲の中は描き直した絵(明るさを元に寄せるのは、ずれ 30 まで)
    assert result.getpixel((200, 270)) == (30, 30, 30)


def test_match_tone_follows_the_source_at_the_edge() -> None:
    # 元の絵はグラデーション。描き直した絵は全体が暗く・コントラストが弱い(コマごとのトーンのずれ)
    ramp = np.tile(np.linspace(40, 220, 400, dtype=np.float32), (600, 1))
    source = Image.fromarray(np.stack([ramp] * 3, axis=-1).astype(np.uint8))
    generated = Image.fromarray((np.asarray(source, dtype=np.float32) * 0.8 + 10).astype(np.uint8))
    mask = ugoira.region_mask(HEAD, "eyes", source.size)
    matched = np.asarray(ugoira.match_tone(source, generated, mask), dtype=np.float32)
    assert np.abs(matched - np.asarray(source, dtype=np.float32)).max() <= 2


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from python.client import get_client
    from python.routes import image, library, trash as trash_routes, ugoira as ugoira_routes

    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "data" / "app.db")
    for module in (ugoira, trash, library):
        monkeypatch.setattr(module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library, "_OUTPUTS", tmp_path / "outputs")
    monkeypatch.setattr(library, "_served_path", lambda path: tmp_path / path)
    monkeypatch.setattr("python.image_tagger.tag_image", lambda img: [])
    monkeypatch.setattr("python.manga_v2.detect.detect_heads_in_image", lambda img: [HEAD])
    prompts: list[tuple[str, str]] = []
    calls: list = []

    class FakeImage:
        async def generate(self, params):
            prompts.append((params.prompt, params.negative_prompt or ""))
            calls.append(params)
            return [Image.new("RGB", params.size, "black")]

    class JobClient:
        image = FakeImage()

        async def close(self):
            pass

    monkeypatch.setattr(image, "_job_client", lambda api_key: JobClient())
    app = FastAPI()
    for router in (ugoira_routes.router, trash_routes.router):
        app.include_router(router)
    app.dependency_overrides[get_client] = lambda: JobClient()
    (tmp_path / "outputs" / "history").mkdir(parents=True)
    Image.new("RGB", (400, 600), "white").save(tmp_path / "outputs" / "history" / "src.png")
    conn = db.get_connection()
    try:
        row = db.record_generation(
            conn, prompt="1girl, yellow eyes, smile", negative_prompt=None, model="nai-diffusion-4-5-full", size="[400, 600]",
            steps=28, scale=5.0, seed=7, chunk_ids=[], image_paths=["outputs/history/src.png"],
        )
    finally:
        conn.close()
    with TestClient(app) as test_client:
        yield test_client, f"history:{row['id']}:src.png", prompts


def _wait(client, job_id: str) -> dict:
    for _ in range(200):
        job = client.get(f"/api/ugoira/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("終わりませんでした")


def test_partial_makes_frames_and_exports(client, tmp_path: Path) -> None:
    test_client, key, prompts = client
    motions = test_client.get("/api/ugoira/motions").json()["motions"]
    blink = next(m for m in motions if m["name"] == "まばたき")
    job = _wait(test_client, test_client.post("/api/ugoira/partial", json={"key": key, "motion_id": blink["id"]}).json()["id"])
    assert job["status"] == "done", job
    project = test_client.get(f"/api/ugoira/{job['project_id']}").json()
    assert [f["delay"] for f in project["frames"]] == blink["delays"] and len(project["frames"]) == 4
    # 1コマ目(タグ空)は元のまま。ほかは描き直す。閉じた目のコマは open eyes をネガティブに入れる
    assert len(prompts) == 3 and prompts[1][0].startswith("1.4::closed eyes::") and "open eyes" in prompts[1][1]
    first = Image.open(tmp_path / project["frames"][0]["path"])
    assert first.getpixel((200, 220)) == (255, 255, 255)
    # 並べ替え・表示時間
    frames = list(reversed(project["frames"]))
    frames[0]["delay"] = 500
    saved = test_client.put(f"/api/ugoira/{project['id']}/frames", json={"frames": frames}).json()
    assert saved["frames"][0]["delay"] == 500 and saved["frames"][0]["path"] == project["frames"][-1]["path"]
    assert test_client.put(f"/api/ugoira/{project['id']}/frames", json={"frames": [{"path": "x.png", "delay": 100}]}).status_code == 422
    # 書き出し
    archive = zipfile.ZipFile(io.BytesIO(test_client.get(f"/api/ugoira/{project['id']}/export?format=zip").content))
    assert archive.namelist()[:4] == ["000000.jpg", "000001.jpg", "000002.jpg", "000003.jpg"]
    assert json.loads(archive.read("animation.json"))["frames"][0]["delay"] == 500
    gif = Image.open(io.BytesIO(test_client.get(f"/api/ugoira/{project['id']}/export?format=gif&max_side=200").content))
    # 同じ絵が続くコマは、GIF では1枚にまとまり表示時間が足される(全体の長さは変わらない)
    total = 0
    for index in range(gif.n_frames):
        gif.seek(index)
        total += gif.info["duration"]
    assert total == sum(f["delay"] for f in saved["frames"]) and max(gif.size) == 200
    webp = Image.open(io.BytesIO(test_client.get(f"/api/ugoira/{project['id']}/export?format=webp").content))
    assert webp.n_frames >= 2 and webp.info.get("loop") == 0


def test_motions_and_trash(client, tmp_path: Path) -> None:
    test_client, key, _ = client
    made = test_client.post("/api/ugoira/motions", json={
        "name": "ウインク", "region": "eyes", "frames": ["", "one eye closed"], "delays": [800, 300],
    }).json()
    assert made["name"] == "ウインク"
    assert test_client.post("/api/ugoira/motions", json={"name": "x", "region": "eyes", "frames": ["a", "b"], "delays": [5, 100]}).status_code == 422
    job = _wait(test_client, test_client.post("/api/ugoira/partial", json={"key": key, "motion_id": made["id"]}).json()["id"])
    project_id = job["project_id"]
    files = [tmp_path / f["path"] for f in test_client.get(f"/api/ugoira/{project_id}").json()["frames"]]
    assert test_client.delete(f"/api/ugoira/{project_id}").status_code == 204
    assert test_client.get(f"/api/ugoira/{project_id}").status_code == 404 and not any(f.exists() for f in files)
    assert not files[0].parent.exists()
    entry = test_client.get("/api/trash").json()["entries"][0]
    assert entry["kind"] == "ugoira"
    assert test_client.post(f"/api/trash/{entry['id']}/restore").status_code == 200
    assert test_client.get(f"/api/ugoira/{project_id}").status_code == 200 and all(f.exists() for f in files)


def test_whole_motion_uses_img2img_and_redraws_every_frame(client, tmp_path: Path) -> None:
    test_client, key, prompts = client
    wave = next(m for m in test_client.get("/api/ugoira/motions").json()["motions"] if m["name"] == "手を振る")
    assert wave["region"] == "person"
    job = _wait(test_client, test_client.post("/api/ugoira/partial", json={"key": key, "motion_id": wave["id"]}).json()["id"])
    assert job["status"] == "done", job
    project = test_client.get(f"/api/ugoira/{job['project_id']}").json()
    # B は、タグの無いコマも描き直す(4コマとも生成)。毎回、元の絵から img2img する
    assert project["method"] == "whole" and len(prompts) == 4
    assert prompts[1][0].startswith("1.4::waving::, 1.4::hand up::")


def test_default_motions_are_seeded_once(client) -> None:
    test_client, _, _ = client
    motions = test_client.get("/api/ugoira/motions").json()["motions"]
    names = [m["name"] for m in motions]
    assert names.count("手を振る") == 1 and "まばたき" in names
    # 消した既定の動きは、入れ直さない
    wave = next(m for m in motions if m["name"] == "手を振る")
    test_client.delete(f"/api/ugoira/motions/{wave['id']}")
    assert "手を振る" not in [m["name"] for m in test_client.get("/api/ugoira/motions").json()["motions"]]
    assert ugoira.region_mask(HEAD, "all", (400, 600)).getpixel((0, 0)) == 255
    person = ugoira.region_mask(HEAD, "person", (400, 600))
    assert person.getpixel((200, 590)) == 255 and person.getpixel((200, 10)) == 0


def test_frame_interpolation_pads_and_crops(monkeypatch: pytest.MonkeyPatch) -> None:

    from python import frame_interpolation

    seen = {}

    class FakeSession:
        def run(self, names, feeds):
            x = feeds["input"]
            seen["shape"] = x.shape
            # 2枚の平均(t の位置で混ぜる)を返す
            t = float(feeds["timestep"].reshape(-1)[0])
            return [x[:, :3] * (1 - t) + x[:, 3:] * t]

    monkeypatch.setattr(frame_interpolation, "_get_session", lambda: FakeSession())
    a = Image.new("RGB", (100, 70), (0, 0, 0))
    b = Image.new("RGB", (100, 70), (200, 200, 200))
    middle = frame_interpolation.between(a, b, 0.5)
    # 64 の倍数に広げて通し、元の大きさに戻す
    assert seen["shape"] == (1, 6, 128, 128) and middle.size == (100, 70) and middle.getpixel((50, 30)) == (100, 100, 100)
    assert frame_interpolation.same(a, a.copy()) and not frame_interpolation.same(a, b)
    # 目・口だけが変わるコマ(絵全体では小さな違い)も、同じとは見なさない
    blank = Image.new("RGB", (512, 512), "white")
    blink = blank.copy()
    blink.paste((0, 0, 0), (220, 240, 280, 280))
    assert not frame_interpolation.same(blank, blink)


def test_interpolate_makes_a_new_project(client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from python import frame_interpolation

    test_client, key, _ = client
    monkeypatch.setattr(frame_interpolation, "between", lambda a, b, t: Image.blend(a, b.resize(a.size), t))
    blink = next(m for m in test_client.get("/api/ugoira/motions").json()["motions"] if m["name"] == "まばたき")
    job = _wait(test_client, test_client.post("/api/ugoira/partial", json={"key": key, "motion_id": blink["id"]}).json()["id"])
    source = test_client.get(f"/api/ugoira/{job['project_id']}").json()
    # 1コマ目(白)と2コマ目(黒)は違う。2〜4コマ目は同じ黒(テストの生成)なので、そのあいだは飛ばす
    made = _wait(test_client, test_client.post(f"/api/ugoira/{source['id']}/interpolate", json={"count": 2, "loop": True}).json()["id"])
    assert made["status"] == "done", made
    result = test_client.get(f"/api/ugoira/{made['project_id']}").json()
    tags = [f["tags"] for f in result["frames"]]
    # 白→黒、黒→白(最後から最初へ戻る)のあいだにだけ2枚ずつ足す
    assert len(result["frames"]) == 4 + 2 + 2 and tags[1:3] == ["(中割り)", "(中割り)"] and tags[-2:] == ["(中割り)", "(中割り)"]
    # 中割りの表示時間: 前後の短い方を3で分ける(2200 と 60 → 20)
    assert result["frames"][1]["delay"] == 20 and result["frames"][0]["delay"] == 2200
    # 元のうごイラはそのまま。新しい方はコマの画像を写す(片方を消しても、もう片方は残る)
    assert len(test_client.get(f"/api/ugoira/{source['id']}").json()["frames"]) == 4
    assert all(f["path"].startswith(f"outputs/ugoira/{result['id']}/") for f in result["frames"])
    test_client.delete(f"/api/ugoira/{source['id']}")
    assert (tmp_path / result["frames"][0]["path"]).is_file()


def test_partial_can_add_in_betweens(client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from python import frame_interpolation

    test_client, key, _ = client
    monkeypatch.setattr(frame_interpolation, "between", lambda a, b, t: Image.blend(a, b.resize(a.size), t))
    blink = next(m for m in test_client.get("/api/ugoira/motions").json()["motions"] if m["name"] == "まばたき")
    job = _wait(test_client, test_client.post("/api/ugoira/partial", json={"key": key, "motion_id": blink["id"], "interpolate": 1}).json()["id"])
    assert job["status"] == "done" and "中割り" in job["message"], job
    project = test_client.get(f"/api/ugoira/{job['project_id']}").json()
    # 白→黒、黒→白(最後から最初)のあいだに1枚ずつ。同じ絵のあいだは飛ばす。同じうごイラの中に足す
    assert [f["tags"] == "(中割り)" for f in project["frames"]] == [False, True, False, False, False, True]
    assert all(f["path"].startswith(f"outputs/ugoira/{project['id']}/") for f in project["frames"])


def _striped(size=(400, 600)) -> Image.Image:
    xs = np.arange(size[0])[None, :] + np.arange(size[1])[:, None]
    value = ((xs // 8) % 2 * 180 + 40).astype(np.uint8)
    return Image.fromarray(np.stack([value] * 3, axis=-1))


def test_warp_moves_only_the_person() -> None:
    from python import ugoira_warp

    source = _striped()
    # 頭と体の所だけが人物(背景の左上は人物でない)
    mask = np.zeros((600, 400), np.float32)
    mask[90:, 80:320] = 1.0
    for kind in ugoira_warp.WARPS:
        frames = ugoira_warp.render(source, HEAD, kind, 8, 1.0, mask)
        assert len(frames) == 8
        # 最初のコマは元の絵のまま。途中のコマは動く。頭から離れた背景(左下)は動かない
        assert np.array_equal(np.asarray(frames[0]), np.asarray(source)), kind
        middle = np.asarray(frames[4], dtype=np.int16)
        assert np.abs(middle - np.asarray(source, dtype=np.int16)).max() > 50, kind
        assert np.array_equal(middle[-60:, :60], np.asarray(source, dtype=np.int16)[-60:, :60]), kind
    with pytest.raises(ugoira_warp.WarpError):
        ugoira_warp.render(source, HEAD, "dance", 8)


def test_warp_endpoint_makes_a_project(client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from python import anime_seg

    test_client, key, _ = client
    _striped().save(tmp_path / "outputs" / "history" / "src.png")
    monkeypatch.setattr(anime_seg, "person_mask", lambda image: np.ones((image.height, image.width), np.float32))
    warps = test_client.get("/api/ugoira/motions").json()["warps"]
    assert {w["kind"] for w in warps} == {"breath", "hair", "tilt", "nod"}
    job = _wait(test_client, test_client.post("/api/ugoira/warp", json={"key": key, "kind": "tilt", "frames": 8}).json()["id"])
    assert job["status"] == "done", job
    project = test_client.get(f"/api/ugoira/{job['project_id']}").json()
    assert project["method"] == "warp" and len(project["frames"]) == 8
    # 1周 2400ms(首をかしげるの既定)を8コマで
    assert {f["delay"] for f in project["frames"]} == {300}
    assert test_client.post("/api/ugoira/warp", json={"key": key, "kind": "dance"}).status_code == 422


def test_in_between_keeps_unchanged_areas(monkeypatch: pytest.MonkeyPatch) -> None:
    from python import frame_interpolation

    class NoisySession:
        # モデルの出力: 全体が少し明るくずれる(実機の RIFE も、変わっていない所がわずかにずれた)
        def run(self, _outputs, feeds):
            x = feeds["input"]
            return [np.clip((x[:, :3] + x[:, 3:]) / 2 + 0.03, 0, 1)]

    monkeypatch.setattr(frame_interpolation, "_get_session", lambda: NoisySession())
    a = Image.new("RGB", (256, 256), (120, 120, 120))
    b = a.copy()
    b.paste((0, 0, 0), (100, 100, 140, 120))
    middle = np.asarray(frame_interpolation.between(a, b, 0.5))
    # 変わった所はモデルの絵、離れた所は a のまま
    assert middle[110, 120, 0] < 100 and (middle[:40, :40] == 120).all()


def test_video_makes_frames_from_the_remote_model(client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from python import video_remote

    test_client, key, _ = client
    # 接続先が無ければ始めない
    monkeypatch.delenv("VIDEO_GEN_BASE_URL", raising=False)
    assert test_client.post("/api/ugoira/video", json={"key": key, "motion": "she blinks"}).status_code == 503
    monkeypatch.setenv("VIDEO_GEN_BASE_URL", "https://comfy.example")
    asked: dict = {}

    async def fake_generate(source, prompt, **kwargs):
        asked.update(prompt=prompt, **kwargs)
        # loop は最後のコマが最初と同じ絵(外す)
        return [Image.new("RGB", source.size, c) for c in ("white", "gray", "black", "white")]

    monkeypatch.setattr(video_remote, "generate", fake_generate)
    job = _wait(test_client, test_client.post("/api/ugoira/video", json={"key": key, "motion": "she blinks", "seconds": 2}).json()["id"])
    assert job["status"] == "done", job
    project = test_client.get(f"/api/ugoira/{job['project_id']}").json()
    assert project["method"] == "video" and len(project["frames"]) == 3
    assert {f["delay"] for f in project["frames"]} == {62}
    # 動きの文を先に、元の絵のプロンプトをあとに。シードは元の絵のもの
    assert asked["prompt"] == "she blinks. 1girl, yellow eyes, smile" and asked["seed"] == 7 and asked["kind"] == "loop"
    assert Image.open(tmp_path / project["frames"][0]["path"]).size == ugoira.fit_size((400, 600))
