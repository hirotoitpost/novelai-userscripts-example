"""
リモートの GPU(Colab など)で動かす see-through(アニメ絵を、隠れた所まで描き足した最大 23 層に分ける。
https://github.com/mitmul/see-through)を呼ぶ。素体の2Dモデルの層分けに使う。

この PC の GPU(GTX 1070 Ti)では see-through の全体(SDXL の LayerDiff 3D・Marigold)を動かせないので、
notebooks/see_through_colab.ipynb で GPU のマシンに scripts/see_through_server.py を起動し、その HTTP を呼ぶ。
接続先は .env の SEE_THROUGH_BASE_URL(cloudflared の URL)と SEE_THROUGH_TOKEN(合言葉。Bearer で送る)。
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

# 1枚を分けるのにかかる時間の上限(L4 で 1280 なら数分。1回目はモデルの読み込みを含む)
TIMEOUT = 20 * 60


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


def decompose(image: Path, out: Path, *, resolution: int = 1280, seed: int = 42, tblr_split: bool = True) -> Path:
    """
    image を層に分け、out(zip)に保存する。中身は layers.psd(層。名前は部位: hairf 前髪・hairb 後ろ髪・face・eyel・
    topwear・handwear-l など)と layers_depth.psd(層ごとの奥行き)。tblr_split は髪を前後に、腕・目を左右に分ける。
    """
    base, token = _config()
    with open(image, "rb") as f:
        try:
            response = httpx.post(
                f"{base}/decompose",
                headers={"Authorization": f"Bearer {token}"},
                files={"image": (image.name, f, "image/png")},
                data={"resolution": str(resolution), "seed": str(seed), "tblr_split": str(tblr_split).lower()},
                timeout=TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise SeeThroughError(f"see-through のサーバーにつながりません: {exc}") from exc
    if response.status_code != 200:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise SeeThroughError(f"see-through で分けられませんでした({response.status_code}): {str(detail)[:500]}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(response.content)
    return out
