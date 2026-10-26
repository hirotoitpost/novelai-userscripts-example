"""
リモートの GPU(Colab など)で動かす see-through(アニメ絵を、隠れた所まで描き足した最大 23 層に分ける。
https://github.com/mitmul/see-through)を呼ぶ。素体の2Dモデルの層分けに使う。

この PC の GPU(GTX 1070 Ti)では see-through の全体(SDXL の LayerDiff 3D・Marigold)を動かせないので、
notebooks/see_through_colab.ipynb で GPU のマシンに scripts/see_through_server.py を起動し、その HTTP を呼ぶ。
接続先は .env の SEE_THROUGH_BASE_URL(cloudflared の URL)と SEE_THROUGH_TOKEN(合言葉。Bearer で送る)。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

# 1枚を分けるのにかかる時間の上限(L4 で 1280 なら数分。1回目はモデルの読み込みを含む)と、状態を聞く間隔・やり直す回数
TIMEOUT = 20 * 60
POLL = 5.0
RETRIES = 10


class SeeThroughError(RuntimeError):
    """see-through のサーバーにつながらない・失敗した。"""


def _config() -> tuple[str, str]:
    base = os.environ.get("SEE_THROUGH_BASE_URL", "").rstrip("/")
    token = os.environ.get("SEE_THROUGH_TOKEN", "")
    if not base or not token:
        raise SeeThroughError(
            "see-through のサーバーが設定されていません。notebooks/see_through_colab.ipynb を GPU のマシンで動かし、"
            ".env に SEE_THROUGH_BASE_URL と SEE_THROUGH_TOKEN を書いてください。"
        )
    return base, token


def configured() -> bool:
    return bool(os.environ.get("SEE_THROUGH_BASE_URL") and os.environ.get("SEE_THROUGH_TOKEN"))


def health() -> dict:
    """サーバーの状態(GPU の名前・処理中か)。"""
    base, token = _config()
    try:
        response = httpx.get(f"{base}/health", headers={"Authorization": f"Bearer {token}"}, timeout=20)
    except httpx.HTTPError as exc:
        raise SeeThroughError(f"see-through のサーバーにつながりません: {exc}") from exc
    if response.status_code != 200:
        raise SeeThroughError(f"see-through のサーバーの応答が {response.status_code} でした: {response.text[:200]}")
    return response.json()


def decompose(image: Path, out: Path, *, resolution: int = 1280, seed: int = 42, tblr_split: bool = True,
              on_status=None) -> Path:
    """
    image を層に分け、out(zip)に保存する。中身は layers.psd(層。名前は部位: hairf 前髪・hairb 後ろ髪・face・eyel・
    topwear・handwear-l など)と layers_depth.psd(層ごとの奥行き)。tblr_split は髪を前後に、腕・目を左右に分ける。
    1枚に数分かかり、トンネルは1回の呼び出しを 100 秒で切るので、仕事を入れて(POST /jobs)状態を聞き(GET /jobs/{id})、
    できたら受け取る。on_status(状態, 経った秒)を渡すと、聞くたびに呼ぶ。
    """
    base, token = _config()
    headers = {"Authorization": f"Bearer {token}"}
    with open(image, "rb") as f:
        try:
            response = httpx.post(
                f"{base}/jobs", headers=headers, files={"image": (image.name, f, "image/png")},
                data={"resolution": str(resolution), "seed": str(seed), "tblr_split": str(tblr_split).lower()},
                timeout=120,
            )
        except httpx.HTTPError as exc:
            raise SeeThroughError(f"see-through のサーバーにつながりません: {exc}") from exc
    if response.status_code != 200:
        raise SeeThroughError(f"see-through に仕事を入れられませんでした({response.status_code}): {response.text[:300]}")
    job_id = response.json()["id"]
    started = time.time()
    failures = 0
    while True:
        if time.time() - started > TIMEOUT:
            raise SeeThroughError(f"see-through が {TIMEOUT // 60} 分たっても終わりませんでした")
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
            raise SeeThroughError(f"see-through の仕事の状態が分かりません({status.status_code}): {status.text[:300]}")
        info = status.json()
        if on_status is not None:
            on_status(info["status"], round(time.time() - started))
        if info["status"] == "error":
            raise SeeThroughError(f"see-through で分けられませんでした: {str(info.get('detail'))[-800:]}")
        if info["status"] == "done":
            break
    result = httpx.get(f"{base}/jobs/{job_id}/result", headers=headers, timeout=300)
    if result.status_code != 200:
        raise SeeThroughError(f"see-through のできあがりを受け取れませんでした({result.status_code})")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(result.content)
    return out
