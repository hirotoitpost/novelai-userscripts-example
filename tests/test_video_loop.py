"""ループ動画の音のフェード(video_loop.py)。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from python import video_loop, video_marks

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg が要ります")


def test_faded_ramps_both_ends() -> None:
    audio = np.ones((1000, 2))
    out = video_loop.faded(audio, 100)
    assert out[0, 0] == 0 and out[-1, 0] == 0
    assert np.all(np.diff(out[:100, 0]) >= 0) and np.all(np.diff(out[-100:, 0]) <= 0)
    assert np.all(out[100:900] == 1)
    # 長すぎるフェードは半分までに縮める・0 はそのまま
    assert video_loop.faded(audio, 5000)[500, 0] == pytest.approx(1)
    assert np.all(video_loop.faded(audio, 0) == 1)


def _sample_video(path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=128x96:rate=24:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", video_marks.encoder(), "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )


def _audio(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "48000",
                          "-f", "f32le", "-"], check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


@needs_ffmpeg
def test_apply_fade_keeps_original_and_can_redo(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    _sample_video(video)
    (tmp_path / "clip.json").write_text(json.dumps({"label": "試し", "loop": True}), encoding="utf-8")
    loud = np.abs(_audio(video)[:4800]).mean()

    assert video_loop.apply_fade(video, 0.5) == 0.5
    original = video_loop.original_of(video)
    assert original.is_file() and video_marks.probe(video).width == 128
    head = np.abs(_audio(video)[:4800]).mean()  # 最初の 0.1 秒はほぼ無音に近い
    assert head < loud * 0.2
    meta = json.loads((tmp_path / "clip.json").read_text(encoding="utf-8"))
    assert meta == {"label": "試し", "loop": True, "audio_fade": 0.5}

    # 0 に戻すと元の音(元の動画から作り直す)。長すぎる指定は長さの半分まで
    assert video_loop.apply_fade(video, 0) == 0
    assert np.abs(_audio(video)[:4800]).mean() == pytest.approx(loud, rel=0.05)
    assert video_loop.apply_fade(video, 7) <= 1.01
    assert not list(tmp_path.glob(".*"))  # 書きかけが残らない
