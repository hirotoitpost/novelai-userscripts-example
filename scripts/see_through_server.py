"""
see-through(アニメ絵を層に分ける。https://github.com/mitmul/see-through)を、GPU のマシン(Colab など)で HTTP から使う
ための小さなサーバー。see-through のリポジトリのルートで動かす(notebooks/see_through_colab.ipynb が起動する)。

    SEE_THROUGH_TOKEN=合言葉 python see_through_server.py --port 8190

- GET  /health     GPU の名前など(合言葉が要る)
- POST /decompose  絵(multipart の image)を層に分け、PSD(層)と _depth.psd(層ごとの奥行き)を zip で返す。
                   resolution(既定 1280)・seed・tblr_split(既定 入。髪を前髪・後ろ髪に、腕・目を左右に分ける)

外へはトンネル(cloudflared)で出すので、すべての呼び出しで Authorization: Bearer <合言葉> を確かめる。1度に1枚だけ動かす
(GPU のメモリが足りなくなるので、重なった依頼は順番に待たせる)。
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

TOKEN = os.environ.get("SEE_THROUGH_TOKEN", "")
REPO = Path.cwd()
app = FastAPI()
_lock = asyncio.Lock()


def _check(authorization: str | None) -> None:
    if not TOKEN:
        raise HTTPException(status_code=500, detail="SEE_THROUGH_TOKEN が設定されていません")
    if not authorization or not secrets.compare_digest(authorization, f"Bearer {TOKEN}"):
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/health")
async def health(authorization: str | None = Header(None)) -> dict:
    _check(authorization)
    import torch

    return {
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "busy": _lock.locked(),
        "repo": str(REPO),
    }


@app.post("/decompose")
async def decompose(
    image: UploadFile = File(...),
    resolution: int = Form(1280),
    seed: int = Form(42),
    tblr_split: bool = Form(True),
    group_offload: bool = Form(False),
    authorization: str | None = Header(None),
) -> FileResponse:
    _check(authorization)
    if not 512 <= resolution <= 2048:
        raise HTTPException(status_code=422, detail="resolution は 512〜2048")
    async with _lock:
        work = Path(tempfile.mkdtemp(prefix="seethrough_"))
        src = work / "input.png"
        src.write_bytes(await image.read())
        out = work / "out"
        cmd = [sys.executable, "inference/scripts/inference_psd.py", "--srcp", str(src), "--save_dir", str(out),
               "--save_to_psd", "--resolution", str(resolution), "--seed", str(seed), "--disable_progressbar"]
        if tblr_split:
            cmd.append("--tblr_split")
        if group_offload:
            cmd.append("--group_offload")
        started = time.time()
        proc = await asyncio.to_thread(subprocess.run, cmd, cwd=REPO, capture_output=True, text=True)
        if proc.returncode != 0:
            tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-30:])
            shutil.rmtree(work, ignore_errors=True)
            raise HTTPException(status_code=500, detail=f"see-through が失敗しました:\n{tail}")
        psds = glob.glob(str(out / "**" / "*.psd"), recursive=True)
        layers = [p for p in psds if not p.endswith("_depth.psd")]
        if not layers:
            raise HTTPException(status_code=500, detail="PSD ができませんでした")
        bundle = work / "layers.zip"
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(layers[0], "layers.psd")
            depth = layers[0][: -len(".psd")] + "_depth.psd"
            if os.path.exists(depth):
                archive.write(depth, "layers_depth.psd")
        print(f"decompose: {time.time() - started:.0f} 秒 → {layers[0]}", flush=True)
        return FileResponse(bundle, media_type="application/zip", filename="layers.zip")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8190)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port)
