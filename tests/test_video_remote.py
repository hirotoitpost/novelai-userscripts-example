"""
リモートの ComfyUI で動画を作る(video_remote.py)のテスト。ComfyUI は httpx の MockTransport で差し替える(GPU も通信も使わない)。

実行方法:
  uv run pytest tests/test_video_remote.py -v
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import sys
from pathlib import Path

import httpx
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import video_remote  # noqa: E402

PLACEHOLDERS = {
    "image",
    "prompt",
    "negative",
    "seed",
    "width",
    "height",
    "length",
    "prefix",
}


def test_sizes_counts_and_prompt() -> None:
    # 縦横比のまま 832×480 の面積以内、16 の倍数。小さい絵は大きくしない
    assert video_remote.video_size((832, 1216)) == (512, 752)
    assert video_remote.video_size((1216, 832)) == (752, 512)
    assert video_remote.video_size((320, 240)) == (320, 240)
    assert [video_remote.frame_count(s) for s in video_remote.SECONDS] == [33, 49, 81]
    # NovelAI の重みは外し、動きの文を先に
    assert (
        video_remote.video_prompt(
            "she waves", "1.4::closed eyes::, 1girl, -1::hat::, smile"
        )
        == "she waves. closed eyes, 1girl, hat, smile"
    )
    assert video_remote.video_prompt("she waves", "") == "she waves"


def test_fill_keeps_types_and_rejects_unknown() -> None:
    filled = video_remote.fill(
        {"a": "{{seed}}", "b": ["x_{{seed}}", 3], "c": {"d": "{{prompt}}"}},
        {"seed": 7, "prompt": "hi"},
    )
    assert filled == {"a": 7, "b": ["x_7", 3], "c": {"d": "hi"}}
    with pytest.raises(video_remote.VideoRemoteError):
        video_remote.fill({"a": "{{nope}}"}, {})


@pytest.mark.parametrize("kind", sorted(video_remote.WORKFLOWS))
def test_workflows_use_known_placeholders(kind: str) -> None:
    workflow = video_remote.load_workflow(kind)
    assert "_comment" not in workflow
    used = set(re.findall(r"\{\{(\w+)\}\}", json.dumps(workflow)))
    # end は最後の絵も受け取る
    assert used == PLACEHOLDERS | ({"end_image"} if kind == "end" else set())
    # 入力のつながりの先(["ノード", 出力])はすべてある
    for node in workflow.values():
        for value in node["inputs"].values():
            if (
                isinstance(value, list)
                and len(value) == 2
                and isinstance(value[1], int)
            ):
                assert value[0] in workflow
    classes = {node["class_type"] for node in workflow.values()}
    assert (
        "WanImageToVideo" if kind == "i2v" else "WanFirstLastFrameToVideo"
    ) in classes and "SaveWEBM" in classes


def _png(color: str, size: tuple[int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _fake_comfy(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail: bool = False,
    animated: bool = False,
    flaky: list | None = None,
) -> dict:
    """flaky: 進み具合の問い合わせで、はじめに返す一時的な失敗(httpx.Response か、送出する例外)を順に。"""
    seen: dict = {"history_calls": 0, "auth": []}
    flaky = list(flaky or [])

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"].append(request.headers.get("Authorization"))
        path = request.url.path
        if path == "/upload/image":
            assert b"nai_ugoira_" in request.content
            seen["uploads"] = seen.get("uploads", 0) + 1
            return httpx.Response(
                200, json={"name": "nai_ugoira_x.png", "subfolder": "", "type": "input"}
            )
        if path == "/prompt":
            seen["prompt"] = json.loads(request.content)["prompt"]
            return httpx.Response(
                200, json={"prompt_id": "p1", "number": 1, "node_errors": {}}
            )
        if path == "/history/p1":
            if flaky:
                failure = flaky.pop(0)
                if isinstance(failure, Exception):
                    raise failure
                return failure
            seen["history_calls"] += 1
            if seen["history_calls"] == 1:
                return httpx.Response(200, json={})
            if fail:
                return httpx.Response(
                    200,
                    json={
                        "p1": {
                            "status": {
                                "status_str": "error",
                                "completed": False,
                                "messages": [
                                    [
                                        "execution_error",
                                        {"exception_message": "CUDA out of memory"},
                                    ]
                                ],
                            },
                            "outputs": {},
                        }
                    },
                )
            files = (
                [{"filename": "anim.webp", "subfolder": "nai_ugoira", "type": "output"}]
                if animated
                else [
                    {
                        "filename": f"f_{i:05d}.png",
                        "subfolder": "nai_ugoira",
                        "type": "output",
                    }
                    for i in range(3)
                ]
            )
            return httpx.Response(
                200,
                json={
                    "p1": {
                        "status": {"status_str": "success", "completed": True},
                        "outputs": {"14": {"images": files}},
                    }
                },
            )
        if path == "/view":
            if animated:
                buf = io.BytesIO()
                frames = [Image.new("RGB", (64, 96), c) for c in ("red", "green")]
                frames[0].save(
                    buf,
                    "WEBP",
                    save_all=True,
                    append_images=frames[1:],
                    duration=62,
                    loop=0,
                    lossless=True,
                )
                return httpx.Response(200, content=buf.getvalue())
            # コマごとに色を変える(同時に受け取っても、順番が保たれるか確かめる)
            index = int(request.url.params["filename"][2:7])
            return httpx.Response(200, content=_png(("red", "green", "blue")[index], (64, 96)))
        return httpx.Response(404)

    monkeypatch.setenv("VIDEO_GEN_BASE_URL", "https://comfy.example")
    monkeypatch.setenv("VIDEO_GEN_TOKEN", "secret-token-1234567")
    monkeypatch.setattr(video_remote, "POLL", 0.0)
    monkeypatch.setattr(video_remote, "RETRY_WAIT", 0.0)
    monkeypatch.setattr(
        video_remote,
        "_client",
        lambda: httpx.AsyncClient(
            base_url=video_remote.base_url(),
            headers=video_remote._headers(),
            transport=httpx.MockTransport(handler),
        ),
    )
    return seen


def test_generate_fills_workflow_and_returns_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _fake_comfy(monkeypatch)
    messages: list[str] = []
    source = Image.new("RGB", (832, 1216), "white")
    frames = asyncio.run(
        video_remote.generate(
            source,
            "she blinks",
            kind="loop",
            seconds=2,
            seed=9,
            on_progress=messages.append,
        )
    )
    # 受け取ったコマは元の絵の大きさに戻す
    assert len(frames) == 3 and all(f.size == (832, 1216) for f in frames)
    assert [f.getpixel((0, 0)) for f in frames] == [(255, 0, 0), (0, 128, 0), (0, 0, 255)]
    wan = next(
        n
        for n in seen["prompt"].values()
        if n["class_type"] == "WanFirstLastFrameToVideo"
    )["inputs"]
    assert (wan["width"], wan["height"], wan["length"]) == (512, 752, 33)
    sampler = next(
        n for n in seen["prompt"].values() if n["class_type"] == "KSamplerAdvanced"
    )["inputs"]
    assert sampler["noise_seed"] == 9
    assert (
        next(n for n in seen["prompt"].values() if n["class_type"] == "LoadImage")[
            "inputs"
        ]["image"]
        == "nai_ugoira_x.png"
    )
    assert set(seen["auth"]) == {"Bearer secret-token-1234567"}
    assert any("動画を作っています" in m for m in messages)


def test_generate_splits_animated_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_comfy(monkeypatch, animated=True)
    frames = asyncio.run(
        video_remote.generate(Image.new("RGB", (64, 96)), "x", kind="i2v", seconds=2)
    )
    assert [f.getpixel((0, 0))[1] > 100 for f in frames] == [False, True]


def test_generate_reports_comfy_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_comfy(monkeypatch, fail=True)
    with pytest.raises(video_remote.VideoRemoteError, match="CUDA out of memory"):
        asyncio.run(video_remote.generate(Image.new("RGB", (64, 96)), "x", seconds=2))


def test_status_without_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIDEO_GEN_BASE_URL", raising=False)
    assert asyncio.run(video_remote.status())["configured"] is False
    with pytest.raises(video_remote.VideoRemoteError, match="VIDEO_GEN_BASE_URL"):
        asyncio.run(video_remote.generate(Image.new("RGB", (64, 96)), "x", seconds=2))


def test_waiting_survives_brief_tunnel_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # cloudflared が一時的に 502 を返す・接続が切れる。GPU 側では作り続けているので、やり直して受け取る
    _fake_comfy(
        monkeypatch,
        flaky=[
            httpx.Response(502, text="Bad gateway"),
            httpx.ConnectError("reset"),
            httpx.Response(530),
        ],
    )
    messages: list[str] = []
    frames = asyncio.run(
        video_remote.generate(
            Image.new("RGB", (64, 96)), "x", seconds=2, on_progress=messages.append
        )
    )
    assert len(frames) == 3
    assert sum("やり直しています" in m for m in messages) == 3


def test_waiting_gives_up_after_retries_and_on_auth_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_comfy(monkeypatch, flaky=[httpx.Response(502)] * (video_remote.RETRIES + 1))
    with pytest.raises(video_remote.VideoRemoteError, match="HTTP 502"):
        asyncio.run(video_remote.generate(Image.new("RGB", (64, 96)), "x", seconds=2))
    # 合言葉の誤りは、やり直しても変わらないのですぐ止める
    _fake_comfy(monkeypatch, flaky=[httpx.Response(401)])
    messages: list[str] = []
    with pytest.raises(video_remote.VideoRemoteError, match="VIDEO_GEN_TOKEN"):
        asyncio.run(
            video_remote.generate(
                Image.new("RGB", (64, 96)), "x", seconds=2, on_progress=messages.append
            )
        )
    assert not any("やり直しています" in m for m in messages)


def test_video_output_is_split_into_frames(tmp_path) -> None:
    import cv2
    import numpy as np

    # 動画の保存ノード(SaveWEBM など)の出力は、OpenCV で読んでコマに分ける
    path = tmp_path / "v.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"mp4v"), 16, (64, 96))
    for value in (0, 128, 255):
        writer.write(np.full((96, 64, 3), value, np.uint8))
    writer.release()
    frames = video_remote._frames_from(path.read_bytes(), "nai_ugoira/x_00001_.mp4")
    assert len(frames) == 3 and frames[0].size == (64, 96)
    assert [round(float(np.asarray(f)[48, 32].mean()) / 64) for f in frames] == [0, 2, 4]
    with pytest.raises(video_remote.VideoRemoteError):
        video_remote._frames_from(b"not a video", "x.webm")
    history = {"outputs": {"14": {"videos": [{"filename": "x.webm", "subfolder": "", "type": "output"}]}}}
    assert [f["filename"] for f in video_remote._output_files(history)] == ["x.webm"]


def test_generate_sends_the_end_image(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _fake_comfy(monkeypatch)
    source = Image.new("RGB", (832, 1216), "white")
    frames = asyncio.run(
        video_remote.generate(source, "she smiles", kind="end", seconds=2, end=Image.new("RGB", (832, 1216), "gray"))
    )
    assert len(frames) == 3 and seen["uploads"] == 2
    wan = next(n for n in seen["prompt"].values() if n["class_type"] == "WanFirstLastFrameToVideo")["inputs"]
    # 最初と最後は、別々の LoadImage から
    assert wan["start_image"] != wan["end_image"]
    loads = [n["inputs"]["image"] for n in seen["prompt"].values() if n["class_type"] == "LoadImage"]
    assert loads == ["nai_ugoira_x.png", "nai_ugoira_x.png"]
    # 最後の絵は end のときだけ
    with pytest.raises(video_remote.VideoRemoteError):
        asyncio.run(video_remote.generate(source, "x", kind="end", seconds=2))
    with pytest.raises(video_remote.VideoRemoteError):
        asyncio.run(video_remote.generate(source, "x", kind="loop", seconds=2, end=source))
