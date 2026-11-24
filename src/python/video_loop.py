"""
ループ動画の音のつなぎ目をなじませる(終わりでだんだん小さく、始まりでだんだん大きく)。

Grok で最初と最後に同じ絵を指定すると、映像は継ぎ目なく回るが、音はつながらない(ぶつ切りに聞こえる)。
そこで音の始まりと終わりにフェードをかける。長さは動画ごとに後から何度でも変えられるように、
手を加える前の動画を outputs/videos/originals/ に残し、いつもそこから作り直す(映像はコピーで、音だけ AAC にし直す)。
フェードは 0(そのまま)〜動画の長さの半分まで。今は 1.5 秒を既定にしている(6 秒の動画で聞いて決めた)。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from .video_marks import VideoError, _tool

DEFAULT_FADE = 1.5
ORIGINALS = "originals"
_RATE = 48000


def original_of(video: Path) -> Path:
    return video.parent / ORIGINALS / video.name


def _duration(path: Path) -> float:
    out = subprocess.run(
        [_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError as exc:
        raise VideoError("動画の長さを読めませんでした。") from exc


def has_audio(path: Path) -> bool:
    out = subprocess.run(
        [_tool("ffprobe"), "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
         str(path)],
        capture_output=True, text=True,
    )
    return "audio" in out.stdout


def faded(samples: np.ndarray, fade_samples: int) -> np.ndarray:
    """(n, ch) の音の始まりと終わりに、なめらかな(sin²)フェードをかける。"""
    out = samples.astype(np.float64, copy=True)
    f = min(fade_samples, len(out) // 2)
    if f <= 0:
        return out
    ramp = (np.sin(np.linspace(0, np.pi / 2, f)) ** 2)[:, None]
    out[:f] *= ramp
    out[-f:] *= ramp[::-1]
    return out


def _render(source: Path, target: Path, fade: float) -> None:
    ffmpeg = _tool("ffmpeg")
    duration = _duration(source)
    raw = subprocess.run(
        [ffmpeg, "-loglevel", "error", "-i", str(source), "-vn", "-ac", "2", "-ar", str(_RATE), "-f", "f32le", "-"],
        capture_output=True,
    )
    if raw.returncode != 0:
        raise VideoError("動画の音を読めませんでした。")
    audio = np.frombuffer(raw.stdout, dtype=np.float32).reshape(-1, 2)
    # 音の長さを動画にそろえる(足りない分は無音)。つなぎ目に音の切れ端を残さない
    n = int(round(duration * _RATE))
    audio = np.vstack([audio, np.zeros((max(0, n - len(audio)), 2), dtype=np.float32)])[:n]
    audio = faded(audio, int(fade * _RATE)).astype(np.float32)
    with tempfile.TemporaryDirectory() as tmp:
        pcm = Path(tmp) / "audio.f32"
        pcm.write_bytes(audio.tobytes())
        done = subprocess.run(
            [ffmpeg, "-loglevel", "error", "-y", "-i", str(source), "-f", "f32le", "-ar", str(_RATE), "-ac", "2",
             "-i", str(pcm), "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
             "-t", f"{duration:.3f}", "-movflags", "+faststart", str(target)],
            capture_output=True, text=True,
        )
    if done.returncode != 0:
        raise VideoError(f"音のフェードをかけられませんでした: {done.stderr[-300:]}")


def apply_fade(video: Path, fade: float) -> float:
    """
    動画の音にフェードをかけ直す(元の動画は originals/ に残し、いつもそこから作る)。かけた秒数を返す。
    横の .json に audio_fade を書く。0 なら元の音のまま。
    """
    if not has_audio(video) and not original_of(video).is_file():
        raise VideoError("この動画には音がありません。")
    original = original_of(video)
    if not original.is_file():
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(video, original)
    fade = max(0.0, min(float(fade), _duration(original) / 2))
    fade = round(fade, 2)
    # 書きかけを見せないよう、同じフォルダに書いてから置き換える(フォルダの更新時刻も変わり、一覧が読み直される)
    work = video.with_name(f".{video.stem}.fade{video.suffix}")
    try:
        if fade == 0:
            shutil.copy2(original, work)
        else:
            _render(original, work, fade)
        os.replace(work, video)
    finally:
        work.unlink(missing_ok=True)
    sidecar = video.with_suffix(".json")
    meta: dict = {}
    if sidecar.is_file():
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
    meta["audio_fade"] = fade
    sidecar.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return fade
