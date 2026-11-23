"""
動画の見えない透かし。全コマに同じ番号を動画用の埋め方(watermark.VIDEO)で入れ、H.264 で書き出す(音声は AAC にして残す)。
読むときは何コマかの票を足して多数決にする(1コマでは読めない圧縮でも、足すと読める)。

ffmpeg / ffprobe が要る(PATH にあるもの)。H.264 のエンコーダーは、使えるものを順に選ぶ:
libx264 → h264_nvenc(NVIDIA)→ h264_qsv → h264_amf → h264_mf(Windows)→ libopenh264。
X の動画の決まり(H.264・YUV 4:2:0・60fps 以下・AAC-LC)に合わせる。大きすぎる動画は長辺 1280 に縮める。
"""

from __future__ import annotations

import functools
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import watermark

VIDEO_SUFFIXES = (".mp4", ".mov", ".webm")
MAX_SIDE = 1280
# 読むときに使うコマの数
READ_FRAMES = 24
_ENCODERS = ("libx264", "h264_nvenc", "h264_qsv", "h264_amf", "h264_mf", "libopenh264")


class VideoError(RuntimeError):
    pass


def is_video(path: str | Path) -> bool:
    return str(path).lower().endswith(VIDEO_SUFFIXES)


def _tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise VideoError(f"{name} が見つかりません(動画の透かしには ffmpeg が要ります)。")
    return found


@functools.lru_cache(maxsize=1)
def encoder() -> str:
    """使える H.264 のエンコーダー(実際に1コマ書けるもの)。"""
    listed = subprocess.run([_tool("ffmpeg"), "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    for name in _ENCODERS:
        if f" {name} " not in listed:
            continue
        probe = subprocess.run(
            [_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=256x256:d=0.2",
             "-c:v", name, "-pix_fmt", "yuv420p", "-f", "null", "-"],
            capture_output=True,
        )
        if probe.returncode == 0:
            return name
    raise VideoError("H.264 のエンコーダーが見つかりません。")


def _quality_args(name: str) -> list[str]:
    if name == "libx264":
        return ["-preset", "medium", "-crf", "17"]
    return ["-b:v", "10M", "-maxrate", "14M", "-bufsize", "20M"]


@dataclass
class Info:
    width: int
    height: int
    fps: float
    frames: int
    has_audio: bool

    @property
    def seconds(self) -> float:
        return self.frames / self.fps if self.fps else 0.0


def probe(path: Path) -> Info:
    out = subprocess.run(
        [_tool("ffprobe"), "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate,nb_frames",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise VideoError(f"動画を読めませんでした: {out.stderr.strip()[:200]}")
    streams = json.loads(out.stdout).get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise VideoError("映像が入っていません。")
    num, _, den = str(video.get("r_frame_rate", "24/1")).partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 24.0
    frames = int(video.get("nb_frames") or 0) or int(cv2.VideoCapture(str(path)).get(cv2.CAP_PROP_FRAME_COUNT))
    return Info(int(video["width"]), int(video["height"]), fps, frames, any(s.get("codec_type") == "audio" for s in streams))


def _target_size(width: int, height: int) -> tuple[int, int]:
    scale = min(1.0, MAX_SIDE / max(width, height))
    # H.264 の 4:2:0 は偶数の大きさが要る
    return max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)


def embed_video(source: Path, mark_id: int, target: Path) -> Info:
    """source の全コマに透かしを入れて target(mp4)に書き出す。戻り値は書き出した動画の情報。"""
    info = probe(source)
    if info.fps > 60:
        raise VideoError("60fps を超える動画には対応していません(X の決まり)。")
    size = _target_size(info.width, info.height)
    cap = cv2.VideoCapture(str(source))
    command = [
        _tool("ffmpeg"), "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}", "-r", f"{info.fps:g}", "-i", "-",
        "-i", str(source), "-map", "0:v", "-map", "1:a?",
        "-c:v", encoder(), *_quality_args(encoder()), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-shortest", str(target),
    ]
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    count = marked = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            if image.size != size:
                image = image.resize(size, Image.Resampling.LANCZOS)
            try:
                image = watermark.embed(image, mark_id, watermark.VIDEO)
                marked += 1
            except ValueError:
                # 模様の少ないコマ(暗転・ベタ塗り)はそのまま。ほかのコマの票で読む
                pass
            assert proc.stdin is not None
            proc.stdin.write(np.asarray(image).tobytes())
            count += 1
    except BrokenPipeError as exc:
        proc.kill()
        raise VideoError(f"透かしを入れられませんでした: {exc}") from exc
    finally:
        cap.release()
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
    _, err = proc.communicate()
    if proc.returncode != 0 or count == 0:
        raise VideoError(f"動画を書き出せませんでした: {err.decode(errors='replace').strip()[:300]}")
    if marked == 0:
        raise VideoError("模様の少ない動画で、どのコマにも透かしを入れられませんでした。")
    return probe(target)


def frames(path: Path, count: int = READ_FRAMES) -> list[Image.Image]:
    """動画から等間隔に count コマ取り出す(RGB)。"""
    cap = cv2.VideoCapture(str(path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    result: list[Image.Image] = []
    try:
        if total <= 0:
            # コマ数が分からない入れ物は、頭から読む
            while len(result) < count:
                ok, frame = cap.read()
                if not ok:
                    break
                result.append(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
            return result
        for index in np.linspace(0, total - 1, min(count, total)).astype(int):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(index))
            ok, frame = cap.read()
            if ok:
                result.append(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
    finally:
        cap.release()
    return result


def middle_frame(path: Path) -> Image.Image:
    picked = frames(path, 3)
    if not picked:
        raise VideoError("動画のコマを読めませんでした。")
    return picked[len(picked) // 2]


def read(images: list[Image.Image], size: tuple[int, int] | None = None) -> watermark.Reading:
    """何コマかの票を足して透かしを読む。size を渡すと、その大きさに戻してから読む。"""
    total: np.ndarray | None = None
    used = 0
    for image in images:
        if size and image.size != size:
            image = image.resize(size, Image.Resampling.LANCZOS)
        mean = watermark.votes(image, watermark.VIDEO)
        if mean is None:
            continue
        total = mean if total is None else total + mean
        used += 1
    if total is None:
        return watermark.Reading(None, 0.0)
    return watermark.decide(total / used)


def temp_path(suffix: str = ".mp4") -> Path:
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    handle.close()
    return Path(handle.name)
