"""
リモートの GPU(Colab など)で動かす Hunyuan3D-2mv(素体の正面・横・後ろの絵から 3D の形を作る。
https://github.com/Tencent-Hunyuan/Hunyuan3D-2)を呼ぶ。素体の 3D モデル(漫画のポーズの参考)に使う。

この PC の GPU では動かせないので、notebooks/model3d_colab.ipynb で GPU のマシンに scripts/model3d_server.py を起動し、
その HTTP を呼ぶ。接続先は .env の MODEL3D_BASE_URL(cloudflared の URL)と MODEL3D_TOKEN(合言葉。Bearer で送る)。
"""

from __future__ import annotations

import os
import time
from contextlib import ExitStack
from pathlib import Path

import httpx

# 1体を作るのにかかる時間の上限(L4 で 2〜3 分。1回目はモデルの読み込みを含む)と、状態を聞く間隔・やり直す回数
TIMEOUT = 15 * 60
POLL = 5.0
RETRIES = 10
VIEWS = ("front", "left", "back")


class Model3DError(RuntimeError):
    """3D のサーバーにつながらない・失敗した。"""


def _config() -> tuple[str, str]:
    base = os.environ.get("MODEL3D_BASE_URL", "").rstrip("/")
    token = os.environ.get("MODEL3D_TOKEN", "")
    if not base or not token:
        raise Model3DError(
            "3D のサーバーが設定されていません。notebooks/model3d_colab.ipynb を GPU のマシンで動かし、"
            ".env に MODEL3D_BASE_URL と MODEL3D_TOKEN を書いてください。"
        )
    return base, token


def configured() -> bool:
    return bool(os.environ.get("MODEL3D_BASE_URL") and os.environ.get("MODEL3D_TOKEN"))


def health() -> dict:
    """サーバーの状態(GPU の名前・モデルを読み込んだか・処理中か)。"""
    base, token = _config()
    try:
        response = httpx.get(f"{base}/health", headers={"Authorization": f"Bearer {token}"}, timeout=20)
    except httpx.HTTPError as exc:
        raise Model3DError(f"3D のサーバーにつながりません: {exc}") from exc
    if response.status_code != 200:
        raise Model3DError(f"3D のサーバーの応答が {response.status_code} でした: {response.text[:200]}")
    return response.json()


def generate(views: dict[str, Path], out: Path, *, seed: int = 12345, steps: int = 50, octree_resolution: int = 380,
             on_status=None) -> Path:
    """
    素体の絵(views: front は必須、left・back は任意)から形だけの GLB を作り、out に保存する。left は左を向いた真横、
    back は真後ろの絵にする(歩くポーズや斜め向きは、そのまま形に写る)。1体に数分かかり、トンネルは1回の呼び出しを
    100 秒で切るので、仕事を入れて(POST /jobs)状態を聞き(GET /jobs/{id})、できたら受け取る。
    on_status(状態, 経った秒)を渡すと、聞くたびに呼ぶ。
    """
    if "front" not in views:
        raise Model3DError("正面(front)の絵が要ります")
    unknown = set(views) - set(VIEWS)
    if unknown:
        raise Model3DError(f"使えない向きです: {', '.join(sorted(unknown))}")
    base, token = _config()
    headers = {"Authorization": f"Bearer {token}"}
    with ExitStack() as stack:
        files = {k: (p.name, stack.enter_context(open(p, "rb")), "image/png") for k, p in views.items()}
        try:
            response = httpx.post(
                f"{base}/jobs", headers=headers, files=files,
                data={"seed": str(seed), "steps": str(steps), "octree_resolution": str(octree_resolution)},
                timeout=120,
            )
        except httpx.HTTPError as exc:
            raise Model3DError(f"3D のサーバーにつながりません: {exc}") from exc
    if response.status_code != 200:
        raise Model3DError(f"3D のサーバーに仕事を入れられませんでした({response.status_code}): {response.text[:300]}")
    job_id = response.json()["id"]
    started = time.time()
    failures = 0
    while True:
        if time.time() - started > TIMEOUT:
            raise Model3DError(f"3D の形が {TIMEOUT // 60} 分たってもできませんでした")
        time.sleep(POLL)
        try:
            status = httpx.get(f"{base}/jobs/{job_id}", headers=headers, timeout=30)
        except httpx.HTTPError:
            # トンネルの一時的な切れ目はやり直す
            failures += 1
            if failures > RETRIES:
                raise
            continue
        if status.status_code != 200:
            raise Model3DError(f"3D の仕事の状態が分かりません({status.status_code}): {status.text[:300]}")
        info = status.json()
        if on_status is not None:
            on_status(info["status"], round(time.time() - started))
        if info["status"] == "error":
            raise Model3DError(f"3D の形を作れませんでした: {str(info.get('detail'))[-800:]}")
        if info["status"] == "done":
            break
    result = httpx.get(f"{base}/jobs/{job_id}/result", headers=headers, timeout=300)
    if result.status_code != 200:
        raise Model3DError(f"3D の形を受け取れませんでした({result.status_code})")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(result.content)
    return out
