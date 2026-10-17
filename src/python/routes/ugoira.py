"""
うごイラの API(/api/ugoira)。中身は ugoira.py。

  GET    /motions                 動き(まばたき など)の一覧
  POST   /motions                 動きを足す
  PUT    /motions/{id}            動きを直す
  DELETE /motions/{id}            動きを消す
  POST   /partial                 うごイラを作る(処理を切り離す。GET /jobs/{id} で進み具合)。動きの範囲で方法が決まる:
                                    目・口・顔・髪は A(部分だけインペイント)、体ごと・絵全体は B(全体を img2img)
  POST   /warp                    D. 絵を変形して動かす(描き直さない。呼吸・髪がなびく・首をかしげる・うなずく)
  POST   /rig                     E. 部位ごとに分けて、骨格とばねで動かす(描き直さない。髪が風になびく・首をかしげる)
  GET    /{id}/rig                E の半自動: 元の絵と、いまの髪の範囲
  POST   /{id}/rig/edit           E の半自動: タップした所のまわりを拡大して切り出し、髪の範囲に足す・引く
  POST   /{id}/rig/undo|reset     E の半自動: 一つ前に戻す・自動の範囲に戻す
  POST   /{id}/rig/redo           E の半自動: いまの髪の範囲で、コマを作り直す
  GET    /video/status            F の接続先(リモートの GPU の ComfyUI)につながるか
  POST   /video                   F. 動画モデル(Wan 2.2)で動かす。リモートの GPU(Colab・GCP)の ComfyUI に作らせる
  GET    /jobs/{id}               進み具合
  GET    /                        作ったうごイラの一覧
  GET    /{id}                    1つ
  POST   /{id}/interpolate        C. コマのあいだを補う(中割りを足したうごイラを新しく作る。処理を切り離す)
  PUT    /{id}/frames             コマの並び・表示時間を変える
  GET    /{id}/export             書き出す(format=zip|gif|webp、max_side=長い辺)
  DELETE /{id}                    ごみ箱へ(30日のあいだは元に戻せる)
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from novelai import AsyncNovelAI
from pydantic import BaseModel, Field

from .. import ugoira, ugoira_rig, ugoira_warp
from ..client import get_client

router = APIRouter(prefix="/api/ugoira", tags=["ugoira"])
# B(全体を img2img)の既定の強さ。実機で、0.55 では手が上がらず、0.8 では背景・服まで変わった。0.7 で手を振りつつ
# 顔・髪はそろった
WHOLE_STRENGTH = 0.7
ClientDep = Annotated[AsyncNovelAI, Depends(get_client)]


class MotionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    region: Literal["eyes", "mouth", "face", "hair", "person", "all"]
    frames: list[str] = Field(min_length=2, max_length=24)
    delays: list[int] = Field(min_length=2, max_length=24)


class PartialRequest(BaseModel):
    key: str = Field(min_length=1, description="ギャラリーの画像のキー")
    motion_id: int
    # 描き直しの強さ(弱いほど元の絵に近い)。無ければ方法ごとの既定: A(部分・インペイント)1.0、B(全体・img2img)0.7
    strength: float | None = Field(None, ge=0.2, le=1.0)
    seed: int | None = Field(None, ge=0, le=4294967295)
    # 作ったあと、コマのあいだに中割り(C)を足す枚数(0 なら足さない)。最後から最初へ戻るところにも足す
    interpolate: int = Field(0, ge=0, le=3)


class WarpRequest(BaseModel):
    key: str = Field(min_length=1, description="ギャラリーの画像のキー")
    kind: Literal["breath", "hair", "tilt", "nod"]
    # 動きの大きさ(1 が標準)
    amount: float = Field(1.0, ge=0.3, le=2.0)
    # 1周のコマ数と長さ(ミリ秒。無ければ種類ごとの既定)
    frames: int = Field(16, ge=4, le=48)
    period: int | None = Field(None, ge=400, le=10000)
    # 人物だけを動かす(背景は動かさない。人物の切り分けのモデルを使う)
    keep_background: bool = True


class RigRequest(BaseModel):
    key: str = Field(min_length=1, description="ギャラリーの画像のキー")
    kind: Literal["hair", "tilt"]
    # 髪の表現: アニメ調(束ごと・2コマずつ止める)か、リアル寄り(細かい毛筋・毎コマ動く・少しぶれる)
    style: Literal["anime", "real"] = "anime"
    amount: float = Field(1.0, ge=0.3, le=2.0)
    period: int | None = Field(None, ge=600, le=10000)
    # 動いて見える所の裏の埋め方: lama(この PC で)か novelai(胴体だけを切り出して描く。Opus・1MP 以内なら Anlas 0。
    # 髪が残ったらシードを変えて描き直し、3回とも残ったら lama)
    plate: Literal["lama", "novelai"] = "lama"


class FramesRequest(BaseModel):
    frames: list[dict[str, Any]] = Field(min_length=1, max_length=200)


def _error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


# ---- 動き ----


@router.get("/motions")
async def get_motions() -> dict[str, Any]:
    # warps: D(変形)の種類と、1周の長さの既定
    return {
        "motions": ugoira.list_motions(),
        "regions": ugoira.REGION_LABELS,
        "warps": [{"kind": kind, "name": name, "period": period} for kind, (name, period) in ugoira_warp.WARPS.items()],
        # rigs・styles: E(部位ごと)の動きと、髪の表現
        "rigs": [{"kind": kind, "name": name, "period": period} for kind, (name, period) in ugoira_rig.RIGS.items()],
        "styles": ugoira_rig.STYLES,
    }


@router.post("/motions")
async def post_motion(req: MotionRequest) -> dict[str, Any]:
    try:
        return ugoira.save_motion(None, req.name, req.region, req.frames, req.delays)
    except ugoira.UgoiraError as exc:
        raise _error(exc)


@router.put("/motions/{motion_id}")
async def put_motion(motion_id: int, req: MotionRequest) -> dict[str, Any]:
    if not any(m["id"] == motion_id for m in ugoira.list_motions()):
        raise HTTPException(status_code=404, detail="その動きはありません。")
    try:
        return ugoira.save_motion(motion_id, req.name, req.region, req.frames, req.delays)
    except ugoira.UgoiraError as exc:
        raise _error(exc)


@router.delete("/motions/{motion_id}", status_code=204)
async def remove_motion(motion_id: int) -> None:
    ugoira.delete_motion(motion_id)


# ---- 作る(処理を切り離す) ----


@dataclass
class _Job:
    status: str = "running"
    message: str = "開始しました"
    progress: int = 0
    total: int = 0
    project_id: int | None = None
    task: Any = field(default=None, repr=False)


_jobs: dict[str, _Job] = {}


def _job_response(job_id: str, job: _Job) -> dict[str, Any]:
    return {"id": job_id, **{k: getattr(job, k) for k in ("status", "message", "progress", "total", "project_id")}}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="その処理はありません(バックエンドを起動し直すと消えます)。")
    return _job_response(job_id, job)


def _png_b64(image: Any) -> str:
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


@router.post("/partial")
async def start_partial(req: PartialRequest, client: ClientDep) -> dict[str, Any]:
    from .library import _gallery_images

    item = next((i for i in _gallery_images() if i.key == req.key), None)
    if item is None:
        raise HTTPException(status_code=404, detail="画像が見つかりません。")
    motion = next((m for m in ugoira.list_motions() if m["id"] == req.motion_id), None)
    if motion is None:
        raise HTTPException(status_code=404, detail="その動きはありません。")
    job_id = uuid4().hex
    job = _Job(total=len(motion["frames"]) * (1 + req.interpolate))
    _jobs[job_id] = job
    job.task = asyncio.create_task(_run_partial(job, req, item, motion, getattr(client, "api_key", None)))
    return _job_response(job_id, job)


async def _run_partial(job: _Job, req: PartialRequest, item: Any, motion: dict[str, Any], api_key: str | None) -> None:
    from novelai.types import GenerateImageParams
    from PIL import Image

    from ..manga_v2.detect import detect_heads_in_image
    from ..models import GenerateImageRequest
    from ..nai_metadata import generation_settings
    from ..whole_enhance import whole_characters
    from .image import _build_kwargs, _guard_image, _job_client
    from .library import _served_path

    try:
        data = _served_path(item.path).read_bytes()
        with Image.open(io.BytesIO(data)) as opened:
            original = opened.convert("RGB")
        job.message = "画像を確かめています"
        # 未成年キャラの安全対策(画質向上と同じ判定): 性的な画像に未成年を思わせるタグがあれば作らない
        await asyncio.to_thread(_guard_image, data, item.prompt, original, item.key)
        size = ugoira.fit_size(original.size)
        source = original.resize(size, Image.Resampling.LANCZOS) if size != original.size else original
        head = ugoira.pick_head(await asyncio.to_thread(detect_heads_in_image, source))
        mask = ugoira.region_mask(head, motion["region"], source.size)
        settings = generation_settings(data) or {}
        base_prompt = settings.get("prompt") or item.prompt or ""
        seed = next((s for s in (req.seed, settings.get("seed"), item.seed) if s is not None), 1)
        whole = motion["region"] in ugoira.WHOLE_REGIONS
        strength = req.strength if req.strength is not None else (WHOLE_STRENGTH if whole else 1.0)
        project_id = ugoira.create_project(f"{motion['name']}({item.label or item.key})", item.key, "whole" if whole else "partial")
        job.project_id = project_id
        frames: list[dict[str, Any]] = []
        client = None
        try:
            for index, (tags, delay) in enumerate(zip(motion["frames"], motion["delays"])):
                job.message = f"コマ {index + 1}/{len(motion['frames'])} を描いています"
                # A ではタグの無いコマは元の絵のまま。B では元の絵だけ描き方が違って見える(動きが飛ぶ)ので、
                # タグの無いコマも同じ強さで描き直す
                if not tags.strip() and not whole:
                    image = source
                else:
                    fields: dict[str, Any] = {
                        "prompt": ugoira.frame_prompt(base_prompt, motion["region"], tags),
                        "quality": not settings,
                        "uc_preset": "none" if settings else "light",
                        "negative_prompt": ", ".join(
                            p for p in (settings.get("negative_prompt"), ugoira.frame_negative(tags)) if p
                        ) or None,
                        "size": list(source.size),
                        "steps": min(settings.get("steps") or 28, 28),
                        "seed": seed,
                        **(
                            # B: 元の絵から img2img(毎回、元の絵から。前のコマからつなぐと少しずつずれていく)。体ごとのときは、
                            # 背景は元の絵のまま重ねる(ちらつきを抑える)
                            {"i2i": {"image": _png_b64(source), "strength": strength, "noise": 0.0, "seed": seed}}
                            if whole
                            # A: 範囲だけインペイント
                            else {"inpaint": {"image": _png_b64(source), "mask": _png_b64(mask.convert("RGB")), "strength": strength, "seed": seed}}
                        ),
                        "characters": whole_characters(settings) or None,
                    }
                    for key in ("model", "scale", "sampler", "noise_schedule"):
                        if settings.get(key):
                            fields[key] = settings[key]
                    try:
                        gen = GenerateImageRequest(**fields)
                    except ValueError:
                        gen = GenerateImageRequest(**{k: v for k, v in fields.items() if k not in ("model", "sampler", "noise_schedule")})
                    if client is None:
                        client = _job_client(api_key)
                    generated = await client.image.generate(GenerateImageParams(**_build_kwargs(gen)))
                    image = ugoira.composite(source, generated[0], mask)
                frames.append({"path": ugoira.save_frame(project_id, index, image), "delay": int(delay), "tags": tags})
                ugoira._store_frames(project_id, frames)
                job.progress = index + 1
        finally:
            if client is not None:
                await client.close()
        count = len(frames)
        if req.interpolate and len(frames) >= 2:
            images = [Image.open(ugoira._PROJECT_ROOT / f["path"]).convert("RGB") for f in frames]
            frames = await _add_between(job, project_id, frames, images, req.interpolate, True)
            ugoira._store_frames(project_id, frames)
        job.message = f"できました({count}コマ)" if len(frames) == count else f"できました({count}コマ + 中割り → {len(frames)}コマ)"
        job.status = "done"
    except HTTPException as exc:
        job.status, job.message = "error", str(exc.detail)
    except Exception as exc:  # noqa: BLE001 失敗も状態として返す
        job.status, job.message = "error", str(exc)


# ---- F. 動画モデルで動かす(リモートの GPU) ----


class VideoRequest(BaseModel):
    key: str = Field(min_length=1, description="ギャラリーの画像のキー")
    # 動きの説明(英語の自然な文が効く。例: she blinks slowly and her hair sways in the wind)。元の絵のプロンプトをあとに足す
    motion: str = Field(min_length=1, max_length=500)
    # loop: 最初と最後を元の絵にして、繰り返しても継ぎ目が出ないようにする。i2v: 元の絵から始めて、自由に動かす
    kind: Literal["loop", "i2v"] = "loop"
    seconds: Literal[2, 3, 5] = 3
    seed: int | None = Field(None, ge=0, le=4294967295)
    # 作ったあと、コマのあいだに中割り(C)を足す枚数(16fps → 32fps。コマが多いので 1 まで)
    interpolate: int = Field(0, ge=0, le=1)
    # 受け取ったコマを整える(明るさをそろえる・繰り返しの継ぎ目を閉じる・4コマごとのちらつきをならす。video_smooth.py)
    smooth: bool = True


@router.get("/video/status")
async def get_video_status() -> dict[str, Any]:
    """F の接続先(.env の VIDEO_GEN_BASE_URL)が設定されているか・つながるか。"""
    from .. import video_remote

    return await video_remote.status()


@router.post("/video")
async def start_video(req: VideoRequest) -> dict[str, Any]:
    """
    F. 元の絵から、リモートの GPU(Colab・GCP)の ComfyUI で動画モデル(Wan 2.2)に動画を作らせ、コマにする
    (video_remote.py)。NovelAI は使わない。体ごと・髪・表情が同時に、なめらかに動く代わりに、細部は元の絵から少し変わる。
    """
    from .. import video_remote
    from .library import _gallery_images

    if not video_remote.base_url():
        raise HTTPException(status_code=503, detail="動画モデルの接続先が設定されていません(.env の VIDEO_GEN_BASE_URL)。docs/video_remote_jp.md を見てください。")
    item = next((i for i in _gallery_images() if i.key == req.key), None)
    if item is None:
        raise HTTPException(status_code=404, detail="画像が見つかりません。")
    job_id = uuid4().hex
    job = _Job(total=_video_total(video_remote.frame_count(req.seconds), req))
    _jobs[job_id] = job
    job.task = asyncio.create_task(_run_video(job, req, item))
    return _job_response(job_id, job)


def _video_total(received: int, req: VideoRequest) -> int:
    """
    進み具合の総数: 保存するコマ(loop は最後のコマを外す)と、足す中割り(loop は最後から最初へも足すので、コマの数と同じ
    あいだ。i2v は戻らないので1つ少ない)。
    """
    frames = received - 1 if req.kind == "loop" and received > 2 else received
    gaps = frames if req.kind == "loop" else frames - 1
    return frames + req.interpolate * max(0, gaps)


async def _run_video(job: _Job, req: VideoRequest, item: Any) -> None:
    from PIL import Image

    from .. import app_settings, video_remote
    from ..nai_metadata import generation_settings
    from .image import _guard_image
    from .library import _served_path

    async def report(message: str) -> None:
        job.message = message

    try:
        data = _served_path(item.path).read_bytes()
        with Image.open(io.BytesIO(data)) as opened:
            original = opened.convert("RGB")
        job.message = "画像を確かめています"
        # 未成年キャラの安全対策(ほかの作り方と同じ判定)。外のマシンへ送る前に確かめる
        await asyncio.to_thread(_guard_image, data, item.prompt, original, item.key)
        size = ugoira.fit_size(original.size)
        source = original.resize(size, Image.Resampling.LANCZOS) if size != original.size else original
        settings = generation_settings(data) or {}
        prompt = video_remote.video_prompt(req.motion, settings.get("prompt") or item.prompt or "")
        seed = next((s for s in (req.seed, settings.get("seed"), item.seed) if s is not None), 1)
        images = await video_remote.generate(
            source, prompt, kind=req.kind, seconds=req.seconds, seed=int(seed),
            negative=app_settings.get("video.negative"), on_progress=report,
        )
        # 受け取ったコマの数(ワークフローを差し替えると、頼んだ数と違うことがある)で総数を合わせ直す
        job.total = _video_total(len(images), req)
        if req.kind == "loop" and len(images) > 2:
            # 最後のコマは最初のコマと同じ絵(元の絵)。繰り返すと同じ絵が2回続いて止まって見えるので外す
            images = images[:-1]
        if req.smooth:
            from .. import video_smooth

            job.message = "コマを整えています(明るさ・継ぎ目・ちらつき)"
            images = await asyncio.to_thread(video_smooth.smooth, images, req.kind == "loop")
        label = "繰り返し" if req.kind == "loop" else "動画"
        project_id = ugoira.create_project(f"{req.motion[:24]}({label}・{item.label or item.key})", item.key, "video")
        job.project_id = project_id
        delay = round(1000 / video_remote.FPS)
        frames = []
        job.message = "コマを保存しています"
        for index, image in enumerate(images):
            frames.append({"path": ugoira.save_frame(project_id, index, image), "delay": delay, "tags": "(動画)"})
            job.progress = index + 1
        ugoira._store_frames(project_id, frames)
        count = len(frames)
        if req.interpolate and count >= 2:
            # 最後から最初へのあいだは、繰り返す(loop)ときだけ補う。自由に動かす(i2v)は戻らないので、混ざった絵ができる
            frames = await _add_between(job, project_id, frames, images, req.interpolate, req.kind == "loop")
            ugoira._store_frames(project_id, frames)
        job.message = f"できました({count}コマ)" if len(frames) == count else f"できました({count}コマ + 中割り → {len(frames)}コマ)"
        job.status = "done"
    except HTTPException as exc:
        job.status, job.message = "error", str(exc.detail)
    except Exception as exc:  # noqa: BLE001 失敗も状態として返す
        job.status, job.message = "error", str(exc)


# ---- 作ったもの ----


@router.get("")
async def get_projects() -> list[dict[str, Any]]:
    return ugoira.list_projects()


@router.get("/{project_id}")
async def get_project(project_id: int) -> dict[str, Any]:
    project = ugoira.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="うごイラが見つかりません。")
    return project


class InterpolateRequest(BaseModel):
    # コマのあいだに足す枚数
    count: int = Field(1, ge=1, le=3)
    # 最後のコマから最初のコマへ戻るところにも足す(繰り返して見るとき)
    loop: bool = True


@router.post("/{project_id}/interpolate")
async def start_interpolate(project_id: int, req: InterpolateRequest) -> dict[str, Any]:
    """
    C. コマのあいだを補う(frame_interpolation.py。この PC で、NovelAI は使わない)。元のうごイラは残し、中割りを足した
    うごイラを新しく作る(コマの画像も写すので、片方を消してももう片方は残る)。
    """
    project = ugoira.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="うごイラが見つかりません。")
    if len(project["frames"]) < 2:
        raise HTTPException(status_code=422, detail="コマが2枚以上いります。")
    gaps = len(project["frames"]) - (0 if req.loop else 1)
    job_id = uuid4().hex
    job = _Job(total=gaps * req.count)
    _jobs[job_id] = job
    job.task = asyncio.create_task(_run_interpolate(job, project, req))
    return _job_response(job_id, job)


async def _add_between(
    job: _Job, project_id: int, frames: list[dict[str, Any]], images: list[Any], count: int, loop: bool, *, copy: bool = False
) -> list[dict[str, Any]]:
    """
    コマのあいだに中割りを count 枚ずつ足した並びを返す(中割りの絵は project_id のフォルダに保存する)。
    copy なら元のコマの絵も project_id のフォルダに写す(別のうごイラとして作るとき)。job.progress は今の値から進める。
    """
    from .. import frame_interpolation

    result: list[dict[str, Any]] = []
    job.message = "中割りのモデルを読み込んでいます(はじめての時は、取ってきます)"
    for index, frame in enumerate(frames):
        result.append({**frame, "path": ugoira.save_frame(project_id, len(result), images[index])} if copy else dict(frame))
        following = index + 1
        if following == len(frames):
            if not loop:
                break
            following = 0
        a, b = images[index], images[following]
        if frame_interpolation.same(a, b):
            job.progress += count
            continue
        # 中割りのコマの表示時間: 前後のコマの短い方を、足す枚数+1 で分ける(長く止めるコマを、中割りで延ばさない)
        delay = max(20, min(int(frame["delay"]), int(frames[following]["delay"])) // (count + 1))
        for step in range(1, count + 1):
            job.message = f"中割り: コマ {index + 1} と {following + 1} のあいだ({step}/{count})"
            middle = await asyncio.to_thread(frame_interpolation.between, a, b, step / (count + 1))
            result.append({"path": ugoira.save_frame(project_id, len(result), middle), "delay": delay, "tags": "(中割り)"})
            job.progress += 1
    return result


async def _run_interpolate(job: _Job, project: dict[str, Any], req: InterpolateRequest) -> None:
    from PIL import Image

    try:
        frames = project["frames"]
        images = [Image.open(ugoira._PROJECT_ROOT / f["path"]).convert("RGB") for f in frames]
        new_id = ugoira.create_project(f"{project['name']}(中割り{req.count})", project.get("source_key") or "", f"{project['method']}+interpolated")
        job.project_id = new_id
        result = await _add_between(job, new_id, frames, images, req.count, req.loop, copy=True)
        ugoira._store_frames(new_id, result)
        job.message = f"できました({len(frames)}コマ → {len(result)}コマ)"
        job.status = "done"
    except Exception as exc:  # noqa: BLE001 失敗も状態として返す
        job.status, job.message = "error", str(exc)


# ---- D. 絵を変形して動かす ----


@router.post("/warp")
async def start_warp(req: WarpRequest) -> dict[str, Any]:
    """D. 元の絵の画素を少しずつ動かしてうごイラにする(この PC で。NovelAI は使わない)。"""
    from .library import _gallery_images

    item = next((i for i in _gallery_images() if i.key == req.key), None)
    if item is None:
        raise HTTPException(status_code=404, detail="画像が見つかりません。")
    job_id = uuid4().hex
    job = _Job(total=req.frames)
    _jobs[job_id] = job
    job.task = asyncio.create_task(_run_warp(job, req, item))
    return _job_response(job_id, job)


async def _run_warp(job: _Job, req: WarpRequest, item: Any) -> None:
    from PIL import Image

    from .. import anime_seg
    from ..manga_v2.detect import detect_heads_in_image
    from .image import _guard_image
    from .library import _served_path

    try:
        data = _served_path(item.path).read_bytes()
        with Image.open(io.BytesIO(data)) as opened:
            original = opened.convert("RGB")
        job.message = "画像を確かめています"
        await asyncio.to_thread(_guard_image, data, item.prompt, original, item.key)
        size = ugoira.fit_size(original.size)
        source = original.resize(size, Image.Resampling.LANCZOS) if size != original.size else original
        head = ugoira.pick_head(await asyncio.to_thread(detect_heads_in_image, source))
        mask = None
        if req.keep_background:
            job.message = "人物を背景から切り分けています(はじめての時は、モデルを取ってきます)"
            mask = await asyncio.to_thread(anime_seg.person_mask, source)
        name, default_period = ugoira_warp.WARPS[req.kind]
        job.message = "コマを作っています"
        images = await asyncio.to_thread(ugoira_warp.render, source, head, req.kind, req.frames, req.amount, mask)
        project_id = ugoira.create_project(f"{name}(変形・{item.label or item.key})", item.key, "warp")
        job.project_id = project_id
        delay = max(20, round((req.period or default_period) / req.frames))
        frames = []
        for index, image in enumerate(images):
            frames.append({"path": ugoira.save_frame(project_id, index, image), "delay": delay, "tags": f"({name})"})
            job.progress = index + 1
        ugoira._store_frames(project_id, frames)
        job.message = f"できました({len(frames)}コマ)"
        job.status = "done"
    except HTTPException as exc:
        job.status, job.message = "error", str(exc.detail)
    except Exception as exc:  # noqa: BLE001 失敗も状態として返す
        job.status, job.message = "error", str(exc)


@router.put("/{project_id}/frames")
async def put_frames(project_id: int, req: FramesRequest) -> dict[str, Any]:
    try:
        return ugoira.set_frames(project_id, req.frames)
    except ugoira.UgoiraError as exc:
        raise _error(exc)


@router.get("/{project_id}/export")
async def export_project(project_id: int, format: Literal["zip", "gif", "webp"] = "zip", max_side: int | None = None) -> Response:  # noqa: A002
    project = ugoira.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="うごイラが見つかりません。")
    if max_side is not None and not 64 <= max_side <= 4096:
        raise HTTPException(status_code=422, detail="長い辺は 64〜4096 にしてください。")
    try:
        data, ext, mime = await asyncio.to_thread(ugoira.export, project, format, max_side)
    except ugoira.UgoiraError as exc:
        raise _error(exc)
    filename = f"ugoira_{project_id}.{ext}"
    return Response(data, media_type=mime, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: int) -> None:
    """ごみ箱へ(trash.py。コマの画像ごと。30日のあいだは元に戻せる)。"""
    from .. import trash
    from ..db import get_connection

    conn = get_connection()
    try:
        rows = trash.rows(conn, "SELECT * FROM ugoira_projects WHERE id = ?", (project_id,))
        if not rows:
            raise HTTPException(status_code=404, detail="うごイラが見つかりません。")
        conn.execute("DELETE FROM ugoira_projects WHERE id = ?", (project_id,))
        conn.commit()
    finally:
        conn.close()
    files = [f["path"] for f in json.loads(rows[0]["frames"])]
    trash.put("ugoira", rows[0]["name"], {"rows": {"ugoira_projects": rows}}, files)
    # コマを預けたあとの空のフォルダは消す(戻すときは作り直される)
    try:
        ugoira.frame_dir(project_id).rmdir()
    except OSError:
        pass


# ---- E. 部位ごとに、骨格とばねで動かす ----


@router.post("/rig")
async def start_rig(req: RigRequest, client: ClientDep) -> dict[str, Any]:
    """
    E. 人物・髪・頭を分け(人物の切り分け・点で指す切り出し)、骨格(RTMPose)から首の位置を取り、髪はばねの鎖で、
    頭は首を中心に動かす。動いて見えるようになった裏は LaMa で埋める。この PC で作る(NovelAI は使わない)。
    構図(寝ている・横顔・後ろ姿 など)は、絵のプロンプトのタグから選ぶ。
    """
    from .library import _gallery_images

    item = next((i for i in _gallery_images() if i.key == req.key), None)
    if item is None:
        raise HTTPException(status_code=404, detail="画像が見つかりません。")
    job_id = uuid4().hex
    job = _Job(total=4)
    _jobs[job_id] = job
    job.task = asyncio.create_task(_run_rig(job, req, item, getattr(client, "api_key", None)))
    return _job_response(job_id, job)


async def _rig_parts(job: _Job, source: Any, prompt: str, hair: Any = None) -> Any:
    """人物の切り分け・骨格・構図の型から部位を分ける(hair を渡すと、髪はそれを使う)。"""
    from .. import anime_seg
    from ..manga_v2.detect import detect_heads_in_image

    head = ugoira.pick_head(await asyncio.to_thread(detect_heads_in_image, source))
    job.message = "人物を背景から切り分けています(はじめての時は、モデルを取ってきます)"
    person = await asyncio.to_thread(anime_seg.person_mask, source)
    job.progress = 1
    ys, xs = (person > 0.5).nonzero()
    if len(xs) == 0:
        raise ugoira_rig.RigError("人物が見つかりませんでした。")
    job.message = "骨格を取っています(はじめての時は、モデルを取ってきます)"
    skeleton = await asyncio.to_thread(ugoira_rig.detect_pose, source, (xs.min(), ys.min(), xs.max(), ys.max()))
    template = ugoira_rig.pose_template(prompt)
    job.progress = 2
    job.message = f"髪・頭を分けています(構図: {template.label})"
    parts = await asyncio.to_thread(ugoira_rig.split_parts, source, head, person, skeleton, template, hair)
    job.progress = 3
    return parts


# 半自動で直すときのファイル(うごイラのフォルダに置く)。元の絵(作ったときの大きさ)、自動の髪の範囲、いまの髪の範囲、
# 一つ前の髪の範囲、画面に重ねる赤い絵、作ったときの設定
_RIG_SOURCE = "rig_source.png"
_RIG_HAIR_AUTO = "rig_hair_auto.png"
_RIG_HAIR = "rig_hair.png"
_RIG_HAIR_PREV = "rig_hair_prev.png"
_RIG_VIEW = "rig_view.png"
_RIG_SETTINGS = "rig.json"


def _save_hair(project_id: int, hair: Any, name: str = _RIG_HAIR) -> None:
    import numpy as np
    from PIL import Image

    folder = ugoira.frame_dir(project_id)
    alpha = (np.clip(hair, 0, 1) * 255).astype(np.uint8)
    Image.fromarray(alpha).save(folder / name)
    if name == _RIG_HAIR:
        view = np.zeros((*alpha.shape, 4), np.uint8)
        view[..., 0] = 255
        view[..., 3] = (alpha.astype(np.float32) * 0.55).astype(np.uint8)
        Image.fromarray(view).save(folder / _RIG_VIEW)


def _load_hair(project_id: int, name: str = _RIG_HAIR) -> Any:
    import numpy as np
    from PIL import Image

    with Image.open(ugoira.frame_dir(project_id) / name) as image:
        return np.asarray(image.convert("L"), np.float32) / 255.0


async def _novelai_plate(job: _Job, source: Any, parts: Any, settings: dict[str, Any], api_key: str | None) -> tuple[Any, str]:
    """
    髪の裏を NovelAI で描く: 頭を除いた胴体だけを切り出し、髪と頭の所を「頭なし・髪なし」で描かせて、LaMa で埋めた絵に重ねる。
    描いた胴体に髪の色が残っていたら、シードを変えて描き直す。3回とも残ったら LaMa のまま。
    """
    from novelai.types import GenerateImageParams

    from ..models import GenerateImageRequest
    from .image import _build_kwargs, _job_client

    job.message = "髪の裏を埋めています(LaMa)"
    base = await asyncio.to_thread(ugoira_rig.base_plate, source, parts, settings["kind"])
    nai = settings.get("nai") or {}
    req = await asyncio.to_thread(
        ugoira_rig.torso_request, source, parts, nai.get("prompt") or settings.get("prompt") or "", nai.get("negative_prompt") or ""
    )
    if req is None:
        return base, "裏: LaMa(描かせる所がありません)"
    client = _job_client(api_key)
    try:
        first = int(nai.get("seed") or 1)
        for attempt in range(_PLATE_TRIES):
            job.message = f"髪の裏を NovelAI で描いています({attempt + 1}/{_PLATE_TRIES})"
            seed = (first + attempt * 7919) % 4294967295
            fields: dict[str, Any] = {
                "prompt": req.prompt,
                "negative_prompt": req.negative,
                "quality": False,
                "uc_preset": "none",
                "size": list(req.image.size),
                "steps": min(int(nai.get("steps") or 28), 28),
                "seed": seed,
                "inpaint": {"image": _png_b64(req.image), "mask": _png_b64(req.mask), "strength": 1.0, "seed": seed},
            }
            for key in ("model", "scale", "sampler", "noise_schedule"):
                if nai.get(key):
                    fields[key] = nai[key]
            try:
                gen = GenerateImageRequest(**fields)
            except ValueError:
                gen = GenerateImageRequest(**{k: v for k, v in fields.items() if k not in ("model", "sampler", "noise_schedule")})
            drawn = (await client.image.generate(GenerateImageParams(**_build_kwargs(gen))))[0]
            left = await asyncio.to_thread(ugoira_rig.hair_left, source, parts, req, drawn)
            if left <= ugoira_rig.HAIR_LEFT_LIMIT:
                merged = await asyncio.to_thread(ugoira_rig.merge_torso, base, req, drawn)
                job.message = "首の横の裏を埋め直しています"
                merged = await asyncio.to_thread(ugoira_rig.refill_above, merged, parts, settings["kind"], req.top)
                return merged, f"裏: NovelAI({attempt + 1}回目)"
        return base, "裏: LaMa(NovelAI では髪が残ったため)"
    finally:
        await client.close()


# NovelAI で髪の裏を描き直す回数
_PLATE_TRIES = 3


async def _render_rig(
    job: _Job, project_id: int, source: Any, parts: Any, settings: dict[str, Any], api_key: str | None = None
) -> tuple[list[dict[str, Any]], str]:
    plate, note = None, "裏: LaMa"
    if settings.get("plate") == "novelai":
        plate, note = await _novelai_plate(job, source, parts, settings, api_key)
    job.message = "動かしています(裏を埋めるモデルは、はじめての時に取ってきます)"
    rendered = await asyncio.to_thread(
        ugoira_rig.render, source, parts, settings["kind"], settings["style"], settings.get("period"), settings.get("amount", 1.0), plate
    )
    name = ugoira_rig.RIGS[settings["kind"]][0]
    frames = [
        {"path": ugoira.save_frame(project_id, index, image), "delay": delay, "tags": f"({name})"}
        for index, (image, delay) in enumerate(rendered)
    ]
    return frames, note


async def _run_rig(job: _Job, req: RigRequest, item: Any, api_key: str | None = None) -> None:
    from PIL import Image

    from ..nai_metadata import generation_settings
    from .image import _guard_image
    from .library import _served_path

    try:
        data = _served_path(item.path).read_bytes()
        with Image.open(io.BytesIO(data)) as opened:
            original = opened.convert("RGB")
        job.message = "画像を確かめています"
        await asyncio.to_thread(_guard_image, data, item.prompt, original, item.key)
        size = ugoira.fit_size(original.size)
        source = original.resize(size, Image.Resampling.LANCZOS) if size != original.size else original
        parts = await _rig_parts(job, source, item.prompt or "")
        name = ugoira_rig.RIGS[req.kind][0]
        project_id = ugoira.create_project(
            f"{name}(部位・{ugoira_rig.STYLES[req.style]}・{item.label or item.key})", item.key, "rig"
        )
        job.project_id = project_id
        # 髪の裏を NovelAI で描くときの元の生成の設定(絵に埋め込まれたもの。無ければプロンプトだけ)
        embedded = generation_settings(data) or {}
        nai = {k: embedded.get(k) for k in ("prompt", "negative_prompt", "seed", "steps", "model", "scale", "sampler", "noise_schedule")}
        settings = {**req.model_dump(), "prompt": item.prompt or "", "template": parts.template.name, "nai": nai}
        frames, note = await _render_rig(job, project_id, source, parts, settings, api_key)
        ugoira._store_frames(project_id, frames)
        # 半自動で直せるよう、元の絵・髪の範囲・設定を残す
        folder = ugoira.frame_dir(project_id)
        source.save(folder / _RIG_SOURCE)
        _save_hair(project_id, parts.hair, _RIG_HAIR_AUTO)
        _save_hair(project_id, parts.hair)
        (folder / _RIG_SETTINGS).write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
        job.progress = 4
        job.message = f"できました({len(frames)}コマ・構図: {parts.template.label}・{note})"
        job.status = "done"
    except HTTPException as exc:
        job.status, job.message = "error", str(exc.detail)
    except Exception as exc:  # noqa: BLE001 失敗も状態として返す
        job.status, job.message = "error", str(exc)


# ---- 半自動: 髪の範囲をタップで直して、作り直す ----


def _rig_project(project_id: int) -> dict[str, Any]:
    project = ugoira.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="うごイラが見つかりません。")
    if project["method"] != "rig" or not (ugoira.frame_dir(project_id) / _RIG_SETTINGS).is_file():
        raise HTTPException(status_code=409, detail="部位ごとに作ったうごイラではありません(髪の範囲は直せません)。")
    return project


def _rig_state(project_id: int) -> dict[str, Any]:
    folder = ugoira.frame_dir(project_id)
    settings = json.loads((folder / _RIG_SETTINGS).read_text(encoding="utf-8"))
    base = f"{ugoira.FOLDER}/{project_id}"
    return {
        "source": f"{base}/{_RIG_SOURCE}",
        "view": f"{base}/{_RIG_VIEW}",
        "version": int((folder / _RIG_VIEW).stat().st_mtime_ns),
        "can_undo": (folder / _RIG_HAIR_PREV).is_file(),
        "settings": {k: settings.get(k) for k in ("kind", "style", "amount", "period", "template", "plate")},
    }


@router.get("/{project_id}/rig")
async def get_rig(project_id: int) -> dict[str, Any]:
    """半自動で直すための、元の絵と、いまの髪の範囲(赤く重ねる絵)。"""
    _rig_project(project_id)
    return _rig_state(project_id)


class RigEditRequest(BaseModel):
    # タップした所(元の絵の画素座標)
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    mode: Literal["add", "remove"] = "add"
    # AI 拡大で切り出す(None なら、小さい絵のときだけ)
    detail: bool | None = None


@router.post("/{project_id}/rig/edit")
def edit_rig(project_id: int, req: RigEditRequest) -> dict[str, Any]:
    """タップした所のまわりを拡大して切り出し、髪の範囲に足す・引く(作り直すまで、コマは変わらない)。"""
    import shutil

    from PIL import Image

    _rig_project(project_id)
    folder = ugoira.frame_dir(project_id)
    with Image.open(folder / _RIG_SOURCE) as opened:
        source = opened.convert("RGB")
    if req.x >= source.width or req.y >= source.height:
        raise HTTPException(status_code=422, detail="絵の外です。")
    detail = req.detail if req.detail is not None else source.width * source.height < ugoira_rig.DETAIL_AUTO_PIXELS
    hair = _load_hair(project_id)
    region = ugoira_rig.zoom_segment(
        source, (req.x, req.y), detail, cache_key=("rig", project_id), prefer=hair if req.mode == "remove" else None
    )
    if not region.any():
        raise HTTPException(status_code=422, detail="そこからは範囲が切り出せませんでした。少しずらしてタップしてください。")
    shutil.copyfile(folder / _RIG_HAIR, folder / _RIG_HAIR_PREV)
    _save_hair(project_id, ugoira_rig.edit_hair(hair, region, req.mode))
    return {**_rig_state(project_id), "detail": detail}


@router.post("/{project_id}/rig/undo")
def undo_rig(project_id: int) -> dict[str, Any]:
    """一つ前の髪の範囲に戻す。"""
    _rig_project(project_id)
    folder = ugoira.frame_dir(project_id)
    if not (folder / _RIG_HAIR_PREV).is_file():
        raise HTTPException(status_code=409, detail="戻せる直しがありません。")
    _save_hair(project_id, _load_hair(project_id, _RIG_HAIR_PREV))
    (folder / _RIG_HAIR_PREV).unlink()
    return _rig_state(project_id)


@router.post("/{project_id}/rig/reset")
def reset_rig(project_id: int) -> dict[str, Any]:
    """自動で取った髪の範囲に戻す。"""
    import shutil

    _rig_project(project_id)
    folder = ugoira.frame_dir(project_id)
    shutil.copyfile(folder / _RIG_HAIR, folder / _RIG_HAIR_PREV)
    _save_hair(project_id, _load_hair(project_id, _RIG_HAIR_AUTO))
    return _rig_state(project_id)


class RigRedoRequest(BaseModel):
    # 裏の埋め方を変えて作り直す(無ければ作ったときのまま)
    plate: Literal["lama", "novelai"] | None = None


@router.post("/{project_id}/rig/redo")
async def redo_rig(project_id: int, client: ClientDep, req: RigRedoRequest | None = None) -> dict[str, Any]:
    """いまの髪の範囲で、このうごイラのコマを作り直す(漫画へのつながりはそのまま)。"""
    project = _rig_project(project_id)
    if req is not None and req.plate is not None:
        path = ugoira.frame_dir(project_id) / _RIG_SETTINGS
        settings = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**settings, "plate": req.plate}, ensure_ascii=False), encoding="utf-8")
    job_id = uuid4().hex
    job = _Job(total=4, project_id=project_id)
    _jobs[job_id] = job
    job.task = asyncio.create_task(_run_rig_redo(job, project, getattr(client, "api_key", None)))
    return _job_response(job_id, job)


async def _run_rig_redo(job: _Job, project: dict[str, Any], api_key: str | None = None) -> None:
    from PIL import Image

    try:
        project_id = project["id"]
        folder = ugoira.frame_dir(project_id)
        settings = json.loads((folder / _RIG_SETTINGS).read_text(encoding="utf-8"))
        with Image.open(folder / _RIG_SOURCE) as opened:
            source = opened.convert("RGB")
        parts = await _rig_parts(job, source, settings.get("prompt") or "", _load_hair(project_id))
        frames, note = await _render_rig(job, project_id, source, parts, settings, api_key)
        old = {f["path"] for f in project["frames"]}
        ugoira._store_frames(project_id, frames)
        for path in old - {f["path"] for f in frames}:
            (ugoira._PROJECT_ROOT / path).unlink(missing_ok=True)
        job.progress = 4
        job.message = f"作り直しました({len(frames)}コマ・{note})"
        job.status = "done"
    except HTTPException as exc:
        job.status, job.message = "error", str(exc.detail)
    except Exception as exc:  # noqa: BLE001 失敗も状態として返す
        job.status, job.message = "error", str(exc)
