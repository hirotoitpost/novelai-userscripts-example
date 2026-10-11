"""
NovelAI の画像に埋め込まれた設定を、画像生成ページの設定にする処理(nai_metadata)と、その API
(POST /api/metadata/generation)のテスト。埋め込みは PNG のテキストとして自分で作る。

実行方法:
  uv run pytest tests/test_nai_metadata.py -v
"""

from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from PIL.PngImagePlugin import PngInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python.nai_metadata import generation_settings, model_of  # noqa: E402
from python.routes import metadata as metadata_routes  # noqa: E402

COMMENT: dict[str, Any] = {
    # 一番上の prompt は品質タグを足す前のもの(novelai-sdk で生成した画像の形)。最終形は v4_prompt の base_caption
    "prompt": "2girls, park, picnic",
    "uc": "lowres, blurry, worst quality",
    "steps": 23,
    "scale": 5.5,
    "width": 1024,
    "height": 1536,
    "seed": 2058055545,
    "sampler": "k_euler_ancestral",
    "noise_schedule": "karras",
    "cfg_rescale": 0.2,
    "skip_cfg_above_sigma": None,
    "sm": False,
    "sm_dyn": False,
    "dynamic_thresholding": False,
    "reference_strength_multiple": [],
    "request_type": "PromptGenerateRequest",
    "v4_prompt": {
        "caption": {
            "base_caption": "2girls, park, picnic, very aesthetic, masterpiece, no text",
            "char_captions": [
                {"char_caption": "girl, black hair, smile", "centers": [{"x": 0.3, "y": 0.5}]},
                {"char_caption": "girl, blonde hair, waving", "centers": [{"x": 0.7, "y": 0.5}]},
            ],
        }
    },
    "v4_negative_prompt": {
        "caption": {
            "base_caption": "lowres, blurry, worst quality",
            "char_captions": [{"char_caption": "hat", "centers": [{"x": 0.3, "y": 0.5}]}],
        }
    },
}


def png(comment: dict[str, Any] | None, source: str = "NovelAI Diffusion V4.5 4BDE2A90") -> bytes:
    info = PngInfo()
    if comment is not None:
        info.add_text("Comment", json.dumps(comment))
        info.add_text("Source", source)
        info.add_text("Software", "NovelAI")
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buffer, "PNG", pnginfo=info)
    return buffer.getvalue()


def test_settings_are_read_for_the_generate_page() -> None:
    settings = generation_settings(png(COMMENT))
    assert settings is not None
    assert settings["prompt"] == "2girls, park, picnic, very aesthetic, masterpiece, no text"
    assert settings["negative_prompt"] == COMMENT["uc"]
    assert settings["model"] == "nai-diffusion-4-5-full"
    assert (settings["width"], settings["height"], settings["size_preset"]) == (1024, 1536, "large_portrait")
    assert (settings["steps"], settings["scale"], settings["cfg_rescale"]) == (23, 5.5, 0.2)
    assert (settings["sampler"], settings["noise_schedule"]) == ("k_euler_ancestral", "karras")
    assert settings["seed"] == 2058055545 and settings["variety_boost"] is False
    assert settings["characters"] == [
        {"prompt": "girl, black hair, smile", "negative": "hat", "x": 0.3, "y": 0.5},
        {"prompt": "girl, blonde hair, waving", "negative": "", "x": 0.7, "y": 0.5},
    ]
    assert settings["notes"] == []


def test_unusual_values_are_kept_within_what_the_page_accepts() -> None:
    comment = {
        **COMMENT,
        "width": 896,
        "height": 1152,
        "steps": 80,
        "scale": 12,
        "seed": 99999999999,
        "sampler": "ddim_v3",
        "noise_schedule": "native",
        "skip_cfg_above_sigma": 19,
        "sm": True,
        "dynamic_thresholding": True,
        "reference_strength_multiple": [0.6],
        "request_type": "Img2ImgRequest",
        "v4_prompt": json.dumps(COMMENT["v4_prompt"]),  # JSON 文字列のこともある
    }
    settings = generation_settings(png(comment, "NovelAI Diffusion V4.5 ABCDEF12"))
    assert settings is not None
    # プリセットに無いサイズは、幅と高さのまま渡す
    assert (settings["width"], settings["height"], settings["size_preset"]) == (896, 1152, None)
    assert settings["steps"] == 50 and settings["scale"] == 10.0 and settings["seed"] is None
    assert settings["sampler"] == "ddim" and settings["noise_schedule"] is None
    assert settings["variety_boost"] is True and len(settings["characters"]) == 2
    notes = " ".join(settings["notes"])
    for word in (
        "Full / Curated",
        "ステップ数",
        "プロンプトガイダンス",
        "シード値",
        "native",
        "i2i",
        "バイブス",
        "SMEA",
        "Decrisper",
    ):
        assert word in notes, word

    odd = generation_settings(png({**COMMENT, "width": 1000, "height": None, "sampler": "mystery"}))
    assert odd is not None and odd["width"] is None and odd["height"] is None and odd["sampler"] is None


def test_model_is_read_from_the_source() -> None:
    assert model_of("NovelAI Diffusion V4.5 C02D4F98")[0] == "nai-diffusion-4-5-curated"
    assert model_of("NovelAI Diffusion V4 37442FCA") == ("nai-diffusion-4-full", None)
    assert model_of("NovelAI Diffusion V4 00000000")[0] == "nai-diffusion-4-full"
    assert model_of("Stable Diffusion XL C1E1DE52") == ("nai-diffusion-3", None)
    model, note = model_of("NovelAI Diffusion V5 12345678")
    assert model is None and note and "変えていません" in note
    assert model_of("")[0] is None


def test_images_without_novelai_settings() -> None:
    assert generation_settings(png(None)) is None
    assert generation_settings(png({"steps": 20})) is None  # プロンプトが無い
    assert generation_settings(b"not an image") is None


def test_older_images_use_the_top_level_prompt() -> None:
    # V3 までの画像(v4_prompt が無い)は、一番上の prompt / uc が最終形
    comment = {"prompt": "1girl, best quality", "uc": "lowres", "steps": 28}
    old = generation_settings(png(comment, "Stable Diffusion XL C1E1DE52"))
    assert old is not None and old["prompt"] == "1girl, best quality" and old["negative_prompt"] == "lowres"
    assert old["model"] == "nai-diffusion-3" and old["characters"] == [] and old["steps"] == 28


def test_api() -> None:
    app = FastAPI()
    app.include_router(metadata_routes.router)
    client = TestClient(app)
    data = base64.b64encode(png(COMMENT)).decode()
    res = client.post("/api/metadata/generation", json={"image": f"data:image/png;base64,{data}"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["prompt"].endswith("masterpiece, no text")
    assert body["seed"] == 2058055545 and body["size_preset"] == "large_portrait"
    assert body["source"] == "NovelAI Diffusion V4.5 4BDE2A90"
    plain = base64.b64encode(png(None)).decode()
    res = client.post("/api/metadata/generation", json={"image": plain})
    assert res.status_code == 422 and "NovelAI の生成設定が入っていません" in res.json()["detail"]
