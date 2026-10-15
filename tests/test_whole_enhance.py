"""
ギャラリーの画像全体の画質を上げる処理(whole_enhance.py と /api/image/enhance)のテスト。AI 拡大と NovelAI は差し替える。

実行方法:
  uv run pytest tests/test_whole_enhance.py -v
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python.region_redraw import RegionError  # noqa: E402
from python.whole_enhance import redraw_size, suggestion, upscale_target, whole_characters  # noqa: E402
from test_region_redraw import _client, _gallery_image  # noqa: E402


def test_sizes() -> None:
    assert upscale_target(400, 300, 2) == (800, 600)
    assert upscale_target(400, 300, 4) == (1600, 1200)
    # 長い辺は 4096 まで
    assert upscale_target(1600, 1200, 4) == (4096, 3072)
    with pytest.raises(RegionError):
        upscale_target(3800, 2000, 2)
    # 描き直しは 1MP 以内・64 の倍数に。小さい画像は大きく、大きい画像は小さく
    assert redraw_size(400, 600) == (832, 1216)
    assert redraw_size(2000, 2000) == (1024, 1024)
    assert "AI拡大" in suggestion(300, 400) and "十分大きい" in suggestion(4000, 3000)


def test_whole_characters_keep_positions_only_when_placed() -> None:
    characters = [{"prompt": "girl A", "negative": "", "x": 0.15, "y": 0.5}, {"prompt": " "}]
    assert whole_characters({"characters": characters, "use_coords": True}) == [
        {"prompt": "girl A", "negative_prompt": "", "position": (0.1, 0.5)}
    ]
    assert whole_characters({"characters": characters}) == [{"prompt": "girl A", "negative_prompt": ""}]


def _wait(client, job_id: str) -> dict:
    for _ in range(200):
        job = client.get(f"/api/image/enhance/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("終わりませんでした")


def test_enhance_upscale_does_not_call_novelai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key = _gallery_image(tmp_path, monkeypatch)

    def no_call(params):
        raise AssertionError("NovelAI を呼ばない")

    with _client(no_call) as client:
        plan = client.get("/api/image/enhance/plan", params={"key": key}).json()
        assert (plan["width"], plan["height"]) == (400, 400) and plan["upscale"]["4"] == [1600, 1600]
        job = _wait(client, client.post("/api/image/enhance", json={"key": key, "mode": "upscale", "scale": 4}).json()["id"])
    assert job["status"] == "done", job
    result = job["result"]
    assert (result["width"], result["height"]) == (1600, 1600)
    from python.routes import library

    made = next(i for i in library._gallery_images() if i.key == result["key"])
    assert made.source_key == key and Image.open(tmp_path / made.path).size == (1600, 1600)


def test_enhance_redraw_then_upscale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from python.routes import image

    key = _gallery_image(tmp_path, monkeypatch)
    seen = []

    class FakeImage:
        async def generate(self, params):
            seen.append(params)
            return [Image.new("RGB", params.size, "blue")]

    class JobClient:
        image = FakeImage()
        closed = False

        async def close(self):
            JobClient.closed = True

    # 切り離した処理は、リクエストのクライアントではなく自分で作ったクライアントを使い、終わったら閉じる
    monkeypatch.setattr(image, "_job_client", lambda api_key: JobClient())
    with _client() as client:
        job = _wait(client, client.post("/api/image/enhance", json={"key": key, "mode": "redraw", "scale": 2, "strength": 0.3}).json()["id"])
    assert job["status"] == "done", job
    params = seen[0]
    # 1MP 以内で描き直す。全体なので、記録のプロンプトはそのまま使う(構図のタグも残す)。シードは元の画像のもの
    assert params.size == (1024, 1024) and params.i2i.strength == 0.3 and params.seed == 7
    assert "full body" in params.prompt
    # 描き直した絵(1024)より、元の2倍(800)は小さいので、AI 拡大はしない
    assert (job["result"]["width"], job["result"]["height"]) == (1024, 1024) and JobClient.closed


def test_sexual_images_with_minor_cues_are_not_enhanced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from python import minor_guard
    from python.image_tagger import TagScore

    key = _gallery_image(tmp_path, monkeypatch)
    scores = [TagScore("explicit", 0.9, "rating"), TagScore("loli", 0.4, "general")]
    assert minor_guard.image_decision("1girl, cafe", scores).action == minor_guard.BLOCK
    assert minor_guard.image_decision("1girl, cafe", [TagScore("general", 0.9, "rating"), TagScore("loli", 0.4, "general")]).action == minor_guard.OK
    assert minor_guard.image_decision("1girl, nude, school uniform", []).action == minor_guard.BLOCK
    assert minor_guard.image_decision("1girl, nude, mature female", []).action == minor_guard.OK
    monkeypatch.setattr("python.image_tagger.tag_image", lambda img: scores)
    with _client() as client:
        region = client.post("/api/image/region-redraw", json={"key": key, "box": [100, 100, 200, 200], "mode": "upscale"})
        job = _wait(client, client.post("/api/image/enhance", json={"key": key}).json()["id"])
    assert region.status_code == 422 and "loli" in region.json()["detail"]
    assert job["status"] == "error" and "画質は上げられません" in job["message"]
