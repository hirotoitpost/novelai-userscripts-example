"""Grok で動画を作る(grok_video.py)。xAI は呼ばず、偽の応答で確かめる。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest
from PIL import Image

from python import grok_video


def _wait(job: grok_video.Job) -> grok_video.Job:
    for _ in range(200):
        if job.status in ("done", "failed"):
            return job
        time.sleep(0.02)
    raise AssertionError("終わりませんでした")


@pytest.fixture()
def picture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    # 本物の outputs/videos に書かないように
    monkeypatch.setattr(grok_video, "user_files", lambda: tmp_path / "files")
    path = tmp_path / "pic.png"
    Image.new("RGB", (64, 96), (200, 100, 50)).save(path)
    return path


def _client(polls: list[dict], seen: list[dict]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"request_id": "req-1"})
        if request.url.path == "/v1/videos/req-1":
            return httpx.Response(200, json=polls.pop(0))
        if request.url.host == "vidgen.example":
            return httpx.Response(200, content=b"MP4DATA")
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_generate_saves_video_and_sidecar(picture: Path) -> None:
    seen: list[dict] = []
    polls = [
        {"status": "pending", "progress": 40},
        {"status": "done", "video": {"url": "https://vidgen.example/v.mp4"}, "usage": {"cost_in_usd_ticks": 8_500_000_000}},
    ]
    job = grok_video.start(
        source_key="history:1:a.png", image=picture, prompt="walks", model="grok-imagine-video-1.5", seconds=6,
        resolution="720p", audio=True, label="しずく(動画)", adult=False, http=_client(polls, seen), sleep=lambda s: None,
    )
    _wait(job)
    assert job.status == "done", job.message
    assert job.cost_usd == 0.85 and job.video_key and job.video_key.startswith("video:history_1_a_png_")
    body = seen[0]
    assert body["model"] == "grok-imagine-video-1.5" and body["duration"] == 6 and body["generate_audio"] is True
    assert body["image"]["url"].startswith("data:image/png;base64,")
    name = job.video_key.split(":", 1)[1]
    folder = picture.parent / "files" / "outputs" / "videos"
    assert (folder / name).read_bytes() == b"MP4DATA"
    meta = json.loads((folder / name).with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["source_key"] == "history:1:a.png" and meta["cost_usd"] == 0.85 and meta["label"] == "しずく(動画)"
    assert grok_video.get_job(job.id) is job


def test_failure_and_moderation_are_reported(picture: Path) -> None:
    job = grok_video.start(
        source_key="history:1:a.png", image=picture, prompt="x", model="grok-imagine-video-1.5-lite", seconds=3,
        resolution="480p", audio=False, label="", adult=False,
        http=_client([{"status": "failed", "error": {"code": "invalid_argument", "message": "blocked"}}], []),
        sleep=lambda s: None,
    )
    assert _wait(job).status == "failed" and "blocked" in job.message
    job = grok_video.start(
        source_key="history:1:a.png", image=picture, prompt="x", model="grok-imagine-video-1.5", seconds=3,
        resolution="480p", audio=False, label="", adult=False,
        http=_client([{"status": "done", "video": {"url": "", "respect_moderation": False}}], []), sleep=lambda s: None,
    )
    assert _wait(job).status == "failed" and "モデレーション" in job.message


def test_validation(picture: Path) -> None:
    with pytest.raises(grok_video.VideoJobError):
        grok_video.build_body(picture, "x", "unknown-model", 6, "720p", True)
    with pytest.raises(grok_video.VideoJobError):
        grok_video.build_body(picture, "x", grok_video.DEFAULT_MODEL, 30, "720p", True)
    with pytest.raises(grok_video.VideoJobError):
        grok_video.build_body(picture, "x", grok_video.DEFAULT_MODEL, 6, "4k", True)
    assert grok_video.estimate("grok-imagine-video-1.5-lite", 10) == 0.2
