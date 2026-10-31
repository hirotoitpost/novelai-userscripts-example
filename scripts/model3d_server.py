"""
素体の絵から 3D の形(メッシュ)を作る Hunyuan3D-2mv(https://github.com/Tencent-Hunyuan/Hunyuan3D-2)を、GPU のマシン
(Colab など)で HTTP から使うための小さなサーバー。Hunyuan3D-2 のリポジトリのルートで動かす
(notebooks/model3d_colab.ipynb が起動する)。

    MODEL3D_TOKEN=合言葉 python model3d_server.py --port 8191

- GET  /health              GPU の名前・モデルを読み込んだか・待っている仕事の数(合言葉が要る)
- POST /jobs                絵(multipart の front・left・back。front は必須)から形を作る仕事を入れ、すぐに仕事の id を返す。
                            seed(既定 12345)・steps(既定 50)・octree_resolution(既定 380)
- GET  /jobs/{id}           仕事の状態(queued・running・done・error)と、かかった秒数・失敗の理由
- GET  /jobs/{id}/result    できあがり: 形だけの GLB(テクスチャは塗らない)

正面・横・後ろを渡すのは、横から見た胸・お尻の厚みを素体に合わせるため(docs/trials/2026-10-31_model-3d-step1.md)。
横・後ろは、まっすぐ立った真横・真後ろの絵にする。1体に 2〜3 分かかり、Cloudflare のトンネルは 1 回の呼び出しを
100 秒で切る(524)ので、仕事を入れて状態を聞く形にする。外へはトンネルで出すので、すべての呼び出しで
Authorization: Bearer <合言葉> を確かめる。1度に1体だけ動かす。
"""

from __future__ import annotations

import argparse
import asyncio
import io
import os
import secrets
import tempfile
import threading
import time
import traceback
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

TOKEN = os.environ.get("MODEL3D_TOKEN", "")
app = FastAPI()
_lock = asyncio.Lock()
_jobs: dict[str, dict] = {}
_models: dict = {}
_load_lock = threading.Lock()


def _check(authorization: str | None) -> None:
    if not TOKEN:
        raise HTTPException(status_code=500, detail="MODEL3D_TOKEN が設定されていません")
    if not authorization or not secrets.compare_digest(authorization, f"Bearer {TOKEN}"):
        raise HTTPException(status_code=401, detail="unauthorized")


def _load() -> dict:
    """背景消しと Hunyuan3D-2mv を1度だけ読み込む(初回は重みの取得で数分)。"""
    with _load_lock:
        if not _models:
            from hy3dgen.rembg import BackgroundRemover
            from hy3dgen.shapegen import Hunyuan3DDiTFlowMatchingPipeline

            _models["rembg"] = BackgroundRemover()
            _models["mv"] = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
                "tencent/Hunyuan3D-2mv", subfolder="hunyuan3d-dit-v2-mv", variant="fp16",
            )
    return _models


def _generate(images: dict[str, bytes], out: Path, *, seed: int, steps: int, octree_resolution: int) -> None:
    import torch
    from PIL import Image

    models = _load()
    # 素体の絵は灰色の背景なので、背景を消して RGBA にする
    views = {k: models["rembg"](Image.open(io.BytesIO(v)).convert("RGB")) for k, v in images.items()}
    mesh = models["mv"](
        image=views, num_inference_steps=steps, octree_resolution=octree_resolution, num_chunks=20000,
        generator=torch.manual_seed(seed), output_type="trimesh",
    )[0]
    mesh.export(str(out))


@app.get("/health")
async def health(authorization: str | None = Header(None)) -> dict:
    _check(authorization)
    import torch

    return {
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "loaded": bool(_models),
        "busy": _lock.locked(),
        "jobs": sum(1 for j in _jobs.values() if j["status"] in ("queued", "running")),
    }


async def _run(job_id: str, images: dict[str, bytes], out: Path, options: dict) -> None:
    job = _jobs[job_id]
    async with _lock:
        job["status"] = "running"
        started = time.time()
        try:
            await asyncio.to_thread(_generate, images, out, **options)
        except Exception as exc:  # noqa: BLE001 - 失敗の理由をそのまま返す
            job["status"] = "error"
            job["detail"] = f"{type(exc).__name__}: {exc}\n" + traceback.format_exc()[-1500:]
            return
        finally:
            job["seconds"] = round(time.time() - started)
            try:
                import torch

                torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass
        job["result"] = str(out)
        job["status"] = "done"
        print(f"job {job_id}: {job['seconds']} 秒 → {out}", flush=True)


@app.post("/jobs")
async def create_job(
    front: UploadFile = File(...),
    left: UploadFile | None = File(None),
    back: UploadFile | None = File(None),
    seed: int = Form(12345),
    steps: int = Form(50),
    octree_resolution: int = Form(380),
    authorization: str | None = Header(None),
) -> dict:
    _check(authorization)
    if not 10 <= steps <= 100:
        raise HTTPException(status_code=422, detail="steps は 10〜100")
    if not 128 <= octree_resolution <= 512:
        raise HTTPException(status_code=422, detail="octree_resolution は 128〜512")
    images = {name: await f.read() for name, f in (("front", front), ("left", left), ("back", back)) if f is not None}
    job_id = uuid.uuid4().hex
    out = Path(tempfile.mkdtemp(prefix="model3d_")) / "model.glb"
    _jobs[job_id] = {"status": "queued", "seconds": None, "detail": None, "result": None}
    asyncio.create_task(_run(job_id, images, out, {"seed": seed, "steps": steps, "octree_resolution": octree_resolution}))
    return {"id": job_id}


@app.get("/jobs/{job_id}")
async def job_status(job_id: str, authorization: str | None = Header(None)) -> dict:
    _check(authorization)
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="その仕事はありません")
    return {k: job[k] for k in ("status", "seconds", "detail")}


@app.get("/jobs/{job_id}/result")
async def job_result(job_id: str, authorization: str | None = Header(None)) -> FileResponse:
    _check(authorization)
    job = _jobs.get(job_id)
    if job is None or job["status"] != "done":
        raise HTTPException(status_code=404, detail="まだできていません")
    return FileResponse(job["result"], media_type="model/gltf-binary", filename="model.glb")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8191)
    args = parser.parse_args()
    # 最初の仕事を待たせないよう、起動してすぐにモデルを読み込み始める
    threading.Thread(target=_load, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port)
