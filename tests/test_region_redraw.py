"""
ギャラリーの拡大表示の範囲を高解像度にする処理(region_redraw・upscaler と、/api/image/region-redraw)のテスト。
AI 拡大のモデル・判定モデル・画像生成は差し替える。

実行方法:
  uv run pytest tests/test_region_redraw.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db, upscaler  # noqa: E402
from python.region_redraw import (  # noqa: E402
    RegionError,
    clamp_box,
    region_characters,
    region_prompt,
    target_size,
    upscale_size,
)


def test_clamp_box_keeps_it_inside() -> None:
    assert clamp_box((-10, 50, 500, 500), (400, 300)) == (0, 50, 400, 250)


def test_target_size_is_within_1mp_and_multiple_of_64() -> None:
    w, h = target_size(400, 400)
    assert (w, h) == (1024, 1024)
    w, h = target_size(300, 200)
    assert w % 64 == 0 and h % 64 == 0 and w * h <= 1024 * 1024 and w <= 300 * 4
    with pytest.raises(RegionError):
        target_size(900, 1200)  # ほとんど拡大していない


def test_upscale_size_is_up_to_4x_and_4mp() -> None:
    assert upscale_size(400, 400) == (1600, 1600)
    w, h = upscale_size(800, 800)
    assert w * h <= 4 * 1024 * 1024 + 4096 and w > 800


def test_region_prompt_drops_whole_picture_tags() -> None:
    prompt = region_prompt("2girls, full body, best quality, brown hair, park", "1girl, portrait, yellow eyes, brown hair")
    assert prompt == "1girl, portrait, yellow eyes, brown hair, best quality, park"


def test_region_characters_by_position() -> None:
    settings = {
        "use_coords": True,
        "characters": [
            {"prompt": "girl A", "negative": "", "x": 0.3, "y": 0.5},
            {"prompt": "girl B", "negative": "bad", "x": 0.7, "y": 0.5},
        ],
    }
    # 右側だけ: B だけが残り、位置は範囲の中の位置(真ん中)に直す
    kept = region_characters(settings, (400, 0, 600, 1000), (1000, 1000))
    assert kept == [{"prompt": "girl B", "negative_prompt": "bad", "position": (0.5, 0.5)}]
    # 位置が AI まかせ: 1人なら残し、2人なら分からないので残さない
    assert region_characters({**settings, "use_coords": False}, (0, 0, 10, 10), (100, 100)) == []
    one = {"use_coords": False, "characters": [settings["characters"][0]]}
    assert region_characters(one, (0, 0, 10, 10), (100, 100))[0]["prompt"] == "girl A"


def test_upscale_tiles_cover_the_whole_image(monkeypatch: pytest.MonkeyPatch) -> None:
    # モデルの代わりに、各画素を 4x4 に広げる(出力の形はモデルと同じ (1, 3, 4, 高さ, 4, 幅) で、中身は
    # (3, 4×高さ, 4×幅) を並べ直したもの)。入力が 16 の倍数でなければ、モデルと同じく失敗する
    class FakeSession:
        def run(self, names, feeds):
            x = feeds["input"]
            _, c, h, w = x.shape
            assert h % 16 == 0 and w % 16 == 0
            return [np.repeat(np.repeat(x, 4, axis=2), 4, axis=3).reshape(1, c, 4, h, 4, w)]

    monkeypatch.setattr(upscaler, "_get_session", lambda: FakeSession())
    rng = np.random.default_rng(0)
    arr = rng.integers(0, 256, (150, 290, 3), dtype=np.uint8)
    out = np.asarray(upscaler.upscale(Image.fromarray(arr)))
    assert out.shape == (600, 1160, 3)
    assert (out[::4, ::4] == arr).all()


def _gallery_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """生成履歴に 400x400 の画像を1枚入れて、そのギャラリーのキーを返す。"""
    from python.routes import image, library

    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(library, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(image, "_HISTORY_DIR", tmp_path / "outputs" / "history")
    (tmp_path / "outputs" / "history").mkdir(parents=True)
    Image.new("RGB", (400, 400), "white").save(tmp_path / "outputs" / "history" / "src.png")
    conn = db.get_connection()
    try:
        row = db.record_generation(
            conn, prompt="1girl, full body, park", negative_prompt=None, model="nai-diffusion-4-5-full", size="[400, 400]",
            steps=28, scale=5.0, seed=7, chunk_ids=[], image_paths=["outputs/history/src.png"],
        )
    finally:
        conn.close()
    monkeypatch.setattr(library, "_served_path", lambda path: tmp_path / path)
    monkeypatch.setattr(upscaler, "upscale", lambda img: img.resize((img.width * 4, img.height * 4)))
    return f"history:{row['id']}:src.png"


def _client(fake_generate=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from python.client import get_client
    from python.routes import image

    class FakeImage:
        async def generate(self, params):
            if fake_generate:
                fake_generate(params)
            return [Image.new("RGB", params.size, "blue")]

    class FakeClient:
        image = FakeImage()

    app = FastAPI()
    app.include_router(image.router)
    app.dependency_overrides[get_client] = lambda: FakeClient()
    return TestClient(app)


def test_region_redraw_makes_a_new_gallery_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key = _gallery_image(tmp_path, monkeypatch)
    monkeypatch.setattr("python.image_tagger.tag_image", lambda img: [])
    seen = []
    client = _client(seen.append)
    res = client.post("/api/image/region-redraw", json={"key": key, "box": [100, 100, 200, 200], "strength": 0.3})
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["width"], body["height"]) == (768, 768) and body["enlarged_by"] == "ai"
    params = seen[0]
    # 画像に設定が無いので、記録のプロンプトから全体のための構図を外して使う。シードは元の画像のもの
    assert "full body" not in params.prompt and "park" in params.prompt
    assert params.seed == 7 and params.i2i is not None and params.i2i.strength == 0.3
    # ギャラリーに新しい画像として並び、元の画像のキーが付く
    from python.routes import library

    made = next(i for i in library._gallery_images() if i.key == body["key"])
    assert made.source_key == key and (tmp_path / made.path).is_file()


def test_region_upscale_only_does_not_call_novelai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key = _gallery_image(tmp_path, monkeypatch)

    def no_call(params):
        raise AssertionError("NovelAI を呼ばない")

    client = _client(no_call)
    res = client.post("/api/image/region-redraw", json={"key": key, "box": [100, 100, 200, 200], "mode": "upscale"})
    assert res.status_code == 200, res.text
    assert (res.json()["width"], res.json()["height"]) == (800, 800)
    assert client.post("/api/image/region-redraw", json={"key": "history:999:x.png", "box": [0, 0, 9, 9]}).status_code == 404
