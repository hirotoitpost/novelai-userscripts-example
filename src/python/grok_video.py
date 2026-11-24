"""
ギャラリーの絵から、xAI の Grok Imagine で短い動画を作る(画像から動画。.env の XAI_API_KEY)。

  POST https://api.x.ai/v1/videos/generations  {model, prompt, image: {url: data URL}, duration, resolution, generate_audio}
       → {request_id}
  GET  https://api.x.ai/v1/videos/{request_id}  → status pending / done(video.url は一時的な URL)/ failed

できた動画は outputs/videos/ に保存し、横に同じ名前の .json(label・prompt・model・source_key・費用など)を置く
(ギャラリーの「動画」に並ぶ。routes/library.py)。作るのは裏のスレッドで、数分かかる。進み具合は job で見る。
料金は1秒いくら(モデルで違う)。音声付き・高画質では案内より高くなる(1.5・720p・6秒・音声付きで 0.85 ドルだった)。

ループ(loop=True): 最後のコマにも同じ絵を指定する(last_frame。1.5 だけ・720p まで)。映像は継ぎ目なく回る。
音はつながらないので、できたあと video_loop.apply_fade で始まりと終わりにフェードをかける(長さは後から変えられる)。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from . import video_loop
from .db import as_user, current_user_id, user_files
from .video_loop import DEFAULT_FADE

log = logging.getLogger(__name__)

API = "https://api.x.ai/v1"
# モデルと、案内の1秒あたりの料金(ドル)。音声・解像度で実際は高くなることがある
MODELS = {
    "grok-imagine-video-1.5": 0.08,
    "grok-imagine-video-1.5-lite": 0.02,
    "grok-imagine-video": 0.05,
}
DEFAULT_MODEL = "grok-imagine-video-1.5"
RESOLUTIONS = ("480p", "720p", "1080p")
# 最後のコマを指定できる(ループにできる)モデルと解像度
LOOP_MODELS = ("grok-imagine-video-1.5",)
LOOP_RESOLUTIONS = ("480p", "720p")
MIN_SECONDS, MAX_SECONDS = 1, 15
POLL_SECONDS = 5.0
TIMEOUT_SECONDS = 15 * 60
# 絵柄を崩さないための一言(画面で外せる)
KEEP_STYLE = (
    "Keep the art style, colors, outlines, the character's face and hairstyle exactly as in the image. "
    "Do not add hair or clothing that is not in the image."
)
_UNSAFE = re.compile(r"[^0-9A-Za-z_-]+")


class VideoJobError(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.environ.get("XAI_API_KEY", "").strip())


def _headers() -> dict[str, str]:
    key = os.environ.get("XAI_API_KEY", "").strip()
    if not key:
        raise VideoJobError("XAI_API_KEY が .env にありません。")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def estimate(model: str, seconds: int) -> float:
    return round(MODELS.get(model, MODELS[DEFAULT_MODEL]) * seconds, 2)


@dataclass
class Job:
    id: str
    user_id: int | None
    source_key: str
    status: str = "starting"  # starting / generating / done / failed
    progress: int = 0
    message: str = ""
    video_key: str | None = None
    cost_usd: float | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def public(self) -> dict[str, Any]:
        item = asdict(self)
        item.pop("user_id", None)
        return item


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def get_job(job_id: str) -> Job | None:
    with _lock:
        job = _jobs.get(job_id)
    if job is None or job.user_id != current_user_id():
        return None
    return job


def _mime(path: Path) -> str:
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(path.suffix.lower(), "image/png")


def build_body(
    image: Path, prompt: str, model: str, seconds: int, resolution: str, audio: bool, loop: bool = False,
) -> dict[str, Any]:
    if model not in MODELS:
        raise VideoJobError(f"知らないモデルです: {model}")
    if resolution not in RESOLUTIONS:
        raise VideoJobError(f"解像度は {', '.join(RESOLUTIONS)} のどれかです。")
    if not MIN_SECONDS <= seconds <= MAX_SECONDS:
        raise VideoJobError(f"長さは {MIN_SECONDS}〜{MAX_SECONDS} 秒です。")
    data = image.read_bytes()
    if len(data) > 20 * 1024 * 1024:
        raise VideoJobError("元の絵が大きすぎます(20MB まで)。")
    if loop and model not in LOOP_MODELS:
        raise VideoJobError(f"ループにできるのは {', '.join(LOOP_MODELS)} だけです。")
    if loop and resolution not in LOOP_RESOLUTIONS:
        raise VideoJobError(f"ループの解像度は {', '.join(LOOP_RESOLUTIONS)} までです。")
    url = f"data:{_mime(image)};base64,{base64.b64encode(data).decode()}"
    body: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "image": {"url": url},
        "duration": seconds,
        "resolution": resolution,
        "generate_audio": audio,
    }
    if loop:
        # 最後のコマも同じ絵にして、終わりが始まりにつながるようにする
        body["last_frame"] = {"url": url}
    return body


def _video_name(source_key: str) -> str:
    stem = _UNSAFE.sub("_", source_key.replace(":", "_"))[:60].strip("_") or "video"
    return f"{stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def start(
    *, source_key: str, image: Path, prompt: str, model: str, seconds: int, resolution: str, audio: bool,
    label: str, adult: bool, loop: bool = False, audio_fade: float = DEFAULT_FADE,
    http: httpx.Client | None = None, sleep: Any = time.sleep,
) -> Job:
    """動画づくりを裏で始める(すぐ返る)。中身を確かめられない指定は、ここで VideoJobError。"""
    body = build_body(image, prompt, model, seconds, resolution, audio, loop)
    headers = _headers()
    job = Job(uuid.uuid4().hex, current_user_id(), source_key)
    with _lock:
        _jobs[job.id] = job
    folder = user_files() / "outputs" / "videos"
    user_id = current_user_id()

    def run() -> None:
        with as_user(user_id):
            client = http or httpx.Client(timeout=120)
            try:
                _run(job, client, headers, body, folder, label=label, adult=adult, sleep=sleep,
                     audio_fade=audio_fade if loop and audio else None)
            except Exception as exc:  # noqa: BLE001 - どんな失敗も画面に出す
                log.warning("動画を作れませんでした: %s", exc)
                job.status, job.message = "failed", str(exc)[:400]
            finally:
                if http is None:
                    client.close()

    threading.Thread(target=run, name=f"grok-video-{job.id[:8]}", daemon=True).start()
    return job


def _run(job: Job, client: httpx.Client, headers: dict[str, str], body: dict[str, Any], folder: Path, *,
         label: str, adult: bool, sleep: Any, audio_fade: float | None = None) -> None:
    r = client.post(f"{API}/videos/generations", headers=headers, json=body)
    if r.status_code >= 400:
        raise VideoJobError(f"xAI のエラー({r.status_code}): {r.text[:300]}")
    request_id = r.json().get("request_id")
    if not request_id:
        raise VideoJobError("xAI から request_id が返ってきませんでした。")
    job.status = "generating"
    waited = 0.0
    while True:
        sleep(POLL_SECONDS)
        waited += POLL_SECONDS
        if waited > TIMEOUT_SECONDS:
            raise VideoJobError("動画ができるまでの時間を過ぎました。")
        poll = client.get(f"{API}/videos/{request_id}", headers=headers)
        if poll.status_code >= 400:
            raise VideoJobError(f"xAI のエラー({poll.status_code}): {poll.text[:300]}")
        data = poll.json()
        status = data.get("status")
        job.progress = int(data.get("progress") or job.progress)
        if status == "failed":
            error = data.get("error") or {}
            raise VideoJobError(f"動画を作れませんでした: {error.get('message') or error.get('code') or '理由不明'}")
        if status == "done":
            break
    video = data.get("video") or {}
    if not video.get("url"):
        raise VideoJobError("内容の確認(モデレーション)で止められ、動画が返ってきませんでした。")
    ticks = (data.get("usage") or {}).get("cost_in_usd_ticks")
    job.cost_usd = round(ticks / 1e10, 4) if ticks else None
    content = client.get(video["url"], timeout=300).content
    folder.mkdir(parents=True, exist_ok=True)
    name = _video_name(job.source_key)
    (folder / f"{name}.mp4").write_bytes(content)
    sidecar = {
        "label": label,
        "prompt": body["prompt"],
        "model": body["model"],
        "source_key": job.source_key,
        "adult": adult,
        "duration": body["duration"],
        "resolution": body["resolution"],
        "audio": body["generate_audio"],
        "loop": "last_frame" in body,
        "cost_usd": job.cost_usd,
        "request_id": request_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (folder / f"{name}.json").write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
    if audio_fade:
        # ループの音のつなぎ目をなじませる(元の動画は originals/ に残るので、あとで長さを変えられる)
        job.message = "音のつなぎ目をなじませています"
        try:
            video_loop.apply_fade(folder / f"{name}.mp4", audio_fade)
        except Exception as exc:  # noqa: BLE001 - 動画はできているので、フェードの失敗は知らせるだけ
            log.warning("音のフェードをかけられませんでした: %s", exc)
            job.message = f"音のフェードはかけられませんでした: {exc}"[:400]
        else:
            job.message = ""
    job.video_key = f"video:{name}.mp4"
    job.status, job.progress = "done", 100
