"""
ログの設定と生成の記録(app_logging.py)のテスト。NovelAI には送らない(送る部分は差し替える)。

実行方法:
  uv run pytest tests/test_app_logging.py -v
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import app_logging, novelai_image_v5  # noqa: E402
from python.novelai_image_v5 import CharacterReferenceInput, generate_image_v5, reference_image_b64  # noqa: E402


def _png(color: str = "white") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 24), color).save(buf, format="PNG")
    return buf.getvalue()


def _events(folder: Path) -> list[dict]:
    return [json.loads(line) for path in sorted(folder.glob("generation-*.jsonl")) for line in path.read_text("utf-8").splitlines()]


def test_request_summary_hashes_the_images() -> None:
    image = base64.b64encode(_png()).decode()
    body = {"parameters": {"seed": 1, "director_reference_images": [image], "image": f"data:image/png;base64,{image}"}}
    summary = app_logging.request_summary(body)
    digest = app_logging.sha256_hex(_png())
    assert summary["parameters"]["director_reference_images"] == [f"sha256:{digest}"]
    assert summary["parameters"]["image"] == f"sha256:{digest}"
    # 元の本文は変えない(このあと NovelAI へ送る)
    assert body["parameters"]["director_reference_images"] == [image]


def test_generation_records_what_was_sent_and_the_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "generation-logs"
    result = _png("black")

    async def fake_request(api_key: str, body: dict) -> bytes:
        return result

    monkeypatch.setattr(novelai_image_v5, "_request_image", fake_request)
    ref = reference_image_b64(_png("red"))
    image = asyncio.run(
        generate_image_v5(
            "key", "1girl, beach", "bad", width=832, height=1216, seed=42,
            character_tags=["1girl"], character_reference=[CharacterReferenceInput(ref, 0.3)],
            log_context={"purpose": "panel", "story_id": 7, "scene_index": 3},
        )
    )
    assert image == result
    (event,) = _events(folder)
    assert (event["source"], event["purpose"], event["story_id"], event["scene_index"]) == ("novelai_image_v5", "panel", 7, 3)
    parameters = event["request"]["parameters"]
    assert parameters["seed"] == 42 and parameters["director_reference_strength_values"] == [0.3]
    # 参照画像は送ったとおりの画像のハッシュ(記録の写しと比べられる)
    assert parameters["director_reference_images"] == [f"sha256:{app_logging.b64_sha256(ref)}"]
    assert event["result"]["status"] == "ok" and event["result"]["image_sha256"] == app_logging.sha256_hex(result)


def test_failed_generation_is_recorded_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing(api_key: str, body: dict) -> bytes:
        raise RuntimeError("NovelAI V5 image API error 500: busy")

    monkeypatch.setattr(novelai_image_v5, "_request_image", failing)
    with pytest.raises(RuntimeError):
        asyncio.run(generate_image_v5("key", "1girl", "", width=64, height=64))
    (event,) = _events(tmp_path / "generation-logs")
    assert event["result"]["status"] == "error" and "500" in event["result"]["error"]


def test_old_monthly_logs_are_pruned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "logs"
    monkeypatch.setenv("NAI_GENERATION_LOG_DIR", str(folder))
    monkeypatch.setenv("NAI_GENERATION_LOG_KEEP_MONTHS", "2")
    monkeypatch.setattr(app_logging, "_pruned", set())
    folder.mkdir()
    (folder / "generation-2001-01.jsonl").write_text("{}\n", "utf-8")
    app_logging.record_generation_event({"source": "test"})
    assert not (folder / "generation-2001-01.jsonl").exists()
    assert len(list(folder.glob("generation-*.jsonl"))) == 1


def test_setup_logging_writes_app_logs_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NAI_LOG_DIR", str(tmp_path))
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        for handler in before:
            if handler.get_name() == app_logging._HANDLER_NAME:
                root.removeHandler(handler)
        path = app_logging.setup_logging()
        app_logging.setup_logging()
        assert sum(h.get_name() == app_logging._HANDLER_NAME for h in root.handlers) == 1
        logging.getLogger("python.test").info("コマを生成しました")
        for handler in root.handlers:
            handler.flush()
        assert "コマを生成しました" in path.read_text("utf-8")
    finally:
        for handler in list(root.handlers):
            if handler.get_name() == app_logging._HANDLER_NAME and handler not in before:
                root.removeHandler(handler)
                handler.close()
        for name in app_logging._UVICORN_LOGGERS:
            for handler in list(logging.getLogger(name).handlers):
                if handler.get_name() == app_logging._HANDLER_NAME and handler not in before:
                    logging.getLogger(name).removeHandler(handler)
        for handler in before:
            if handler not in root.handlers:
                root.addHandler(handler)


def test_sdk_generation_is_recorded_with_the_sent_reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from novelai._utils.image import crop_and_resize

    from python.models import GenerateImageRequest
    from python.routes import image

    monkeypatch.setattr(image, "_HISTORY_DIR", tmp_path / "outputs" / "history")
    monkeypatch.setattr(image, "_PROJECT_ROOT", tmp_path, raising=False)
    ref = base64.b64encode(_png("red")).decode()
    gen = GenerateImageRequest(
        prompt="1girl", model="nai-diffusion-4-5-full", size=[832, 1216], steps=28, scale=5.0, seed=9,
        quality=False, uc_preset="none", n_samples=1,
        character_references=[{"image": f"data:image/png;base64,{ref}", "type": "character", "fidelity": 1.0, "strength": 0.5}],
    )
    saved = image.save_generation(gen, [_png("black")])
    (event,) = _events(tmp_path / "generation-logs")
    assert event["source"] == "sdk" and event["result"]["history_id"] == saved["id"]
    assert event["result"]["image_sha256"] == [app_logging.sha256_hex(_png("black"))]
    # SDK が送る形(1024x1536 に整えたもの)のハッシュ
    assert event["request"]["character_references"][0]["image"] == f"sha256:{app_logging.b64_sha256(crop_and_resize(ref))}"
    assert event["request"]["seed"] == 9
