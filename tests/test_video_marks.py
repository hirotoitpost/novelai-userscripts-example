"""動画の見えない透かし(video_marks.py)と、台帳・X への動画の投稿。ffmpeg が無ければ動画を作るテストは飛ばす。"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image

from python import video_marks, watermark, watermark_registry, x_api, x_posts

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg が無い")


def _make_video(path: Path, seconds: float = 1.5) -> None:
    """模様の多いテスト動画(音声つき)。"""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"mandelbrot=s=480x640:r=24,trim=duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", video_marks.encoder(), "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )


@needs_ffmpeg
def test_embed_and_read_after_reencode(tmp_path: Path) -> None:
    source = tmp_path / "in.mp4"
    _make_video(source)
    marked = tmp_path / "marked.mp4"
    info = video_marks.embed_video(source, 0x0BADF00D, marked)
    assert (info.width, info.height) == (480, 640) and info.has_audio
    assert video_marks.read(video_marks.frames(marked)).mark_id == 0x0BADF00D
    assert video_marks.read(video_marks.frames(source)).mark_id is None
    # 低めのビットレートで圧縮し直しても、コマの多数決で読める
    again = tmp_path / "again.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(marked), "-c:v", video_marks.encoder(), "-b:v", "2500k",
         "-pix_fmt", "yuv420p", str(again)],
        check=True,
    )
    assert video_marks.read(video_marks.frames(again)).mark_id == 0x0BADF00D
    # 1コマを切り出した画像も、画像の照合(動画の埋め方も試す)で読める
    assert watermark.extract(video_marks.middle_frame(marked)).mark_id == 0x0BADF00D


@needs_ffmpeg
def test_mark_video_and_verify(tmp_path: Path) -> None:
    source = tmp_path / "in.mp4"
    _make_video(source)
    marked = watermark_registry.mark_video(source, purpose=watermark_registry.X_POST, source_path="v.mp4",
                                           ref="video:v.mp4", x_post_id=3)
    try:
        mark_id = watermark_registry.list_marks()[0]["mark_id"]
        data = marked.read_bytes()
        assert watermark_registry.verify(data)["method"] == "exact"
        result = watermark_registry.verify(data + b"\x00")  # 中身が少し違うファイル
        assert result["found"] and result["method"] == "watermark" and result["mark_id"] == mark_id
        frame = io.BytesIO()
        video_marks.middle_frame(marked).save(frame, "PNG")
        assert watermark_registry.verify(frame.getvalue())["mark_id"] == mark_id
    finally:
        marked.unlink(missing_ok=True)
    # やり直しは同じ番号
    again = watermark_registry.mark_video(source, purpose=watermark_registry.X_POST, ref="video:v.mp4", x_post_id=3)
    again.unlink(missing_ok=True)
    assert len(watermark_registry.list_marks()) == 1


def test_upload_video_chunked() -> None:
    calls: list[tuple[str, str]] = []
    status_polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith("/initialize"):
            body = json.loads(request.content)
            assert body == {"media_type": "video/mp4", "total_bytes": 9 * 1024 * 1024, "media_category": "tweet_video"}
            return httpx.Response(200, json={"data": {"id": "77"}})
        if path.endswith("/append"):
            return httpx.Response(200, json={"data": {"expires_at": 1}})
        if path.endswith("/finalize"):
            return httpx.Response(200, json={"data": {"id": "77", "processing_info": {"state": "pending", "check_after_secs": 1}}})
        if path == "/2/media/upload":
            assert request.url.params["command"] == "STATUS"
            status_polls["n"] += 1
            state = "succeeded" if status_polls["n"] >= 2 else "in_progress"
            return httpx.Response(200, json={"data": {"id": "77", "processing_info": {"state": state, "check_after_secs": 1}}})
        return httpx.Response(404)

    creds = x_api.Credentials("k", "s", "t", "ts")
    client = x_api.XClient(creds, httpx.Client(transport=httpx.MockTransport(handler)))
    slept: list[float] = []
    assert client.upload_video(b"x" * (9 * 1024 * 1024), sleep=slept.append) == "77"
    appends = [c for c in calls if c[1].endswith("/append")]
    assert len(appends) == 3  # 4MB ずつ
    assert status_polls["n"] == 2 and slept == [1.0, 1.0]


def _png(path: Path) -> None:
    Image.new("RGB", (32, 32), (10, 20, 30)).save(path, "PNG")


def test_video_must_be_alone_in_a_part(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    infos = {
        "video:a.mp4": x_posts.ImageInfo("video:a.mp4", "outputs/videos/a.mp4", "動画"),
        "img1": x_posts.ImageInfo("img1", "p1.png", "絵"),
    }
    monkeypatch.setattr(x_posts, "resolve", lambda refs: {r: infos[r] for r in refs if r in infos})
    monkeypatch.setattr(x_posts, "check_images", lambda used, scorer=None: [])
    with pytest.raises(x_posts.PostRejected) as caught:
        x_posts.build_parts([x_posts.PartInput("x", ["video:a.mp4", "img1"])])
    assert "動画は1件に1本" in caught.value.reasons[0]
    saved, _, _ = x_posts.build_parts([x_posts.PartInput("x", ["video:a.mp4"]), x_posts.PartInput("y", ["img1"])])
    assert saved[0]["images"][0]["path"].endswith(".mp4")


def test_run_post_uploads_video(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "files"
    (root / "outputs" / "videos").mkdir(parents=True)
    (root / "outputs" / "videos" / "a.mp4").write_bytes(b"orig")
    monkeypatch.setattr(x_posts, "user_files", lambda: root)
    infos = {"video:a.mp4": x_posts.ImageInfo("video:a.mp4", "outputs/videos/a.mp4", "動画")}
    monkeypatch.setattr(x_posts, "resolve", lambda refs: {r: infos[r] for r in refs if r in infos})
    monkeypatch.setattr(x_posts, "check_images", lambda used, scorer=None: [])
    marked = tmp_path / "marked.mp4"

    def fake_mark_video(path: Path, **kwargs: Any) -> Path:
        assert path == root / "outputs" / "videos" / "a.mp4" and kwargs["ref"] == "video:a.mp4"
        marked.write_bytes(b"marked")
        return marked

    monkeypatch.setattr(watermark_registry, "mark_video", fake_mark_video)

    class FakeX:
        def __init__(self) -> None:
            self.videos: list[bytes] = []
            self.posts: list[list[str]] = []

        def me(self) -> dict[str, Any]:
            return {"username": "tester"}

        def upload_video(self, data: bytes, mime: str = "video/mp4") -> str:
            self.videos.append(data)
            return "v1"

        def set_media_metadata(self, media_id: str, *, sensitive: bool = False, alt_text: str | None = None) -> None:
            pass

        def create_post(self, text: str, media_ids: list[str] | None = None, reply_to: str | None = None) -> str:
            self.posts.append(list(media_ids or []))
            return "t1"

    post = x_posts.create_post("", [x_posts.PartInput("見て", ["video:a.mp4"])], sensitive=False, scheduled_at=None)
    fake = FakeX()
    done = x_posts.run_post(post["id"], client=fake)  # type: ignore[arg-type]
    assert done is not None and done["status"] == x_posts.POSTED
    assert fake.videos == [b"marked"] and fake.posts == [["v1"]]
    assert not marked.exists()  # 一時ファイルは消す
