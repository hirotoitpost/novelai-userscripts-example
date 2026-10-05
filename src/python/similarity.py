"""生成画像がキャラシートの参照画像にどれだけ似ているかを測る(ガチャの当たり判定)。

- color: 色の分布(HSVヒストグラム)の近さ。ローカルで一瞬・無料。髪色/目の色/服の色の
         ブレは拾えるが、髪型や顔立ちの違いは分からない。背景の影響を減らすため、画像の
         中央寄り(キャラが写りやすい範囲)だけを比べる。
- vlm:   画像を読めるLLM(.env の VLLM_VISION_*)に、参照画像と並べて同じキャラかを
         0〜10点で採点させる。髪型やアクセサリーまで見られるが遅く、採点もぶれる。

どちらも 0.0〜1.0 で返し、1.0 が「そっくり」。
"""

from __future__ import annotations

import base64
import io
import json
import re
from typing import Literal

import cv2
import httpx
import numpy as np
from PIL.Image import Image

from .llm_client import get_vision_base_url, get_vision_client, get_vision_model, is_ollama_vision

ScorerLiteral = Literal["color", "vlm"]

# 中央の何割を比べるか(左右・上下の端は背景になりやすい)
_CENTER_RATIO = 0.6


def _center_hsv(image: Image) -> np.ndarray:
    rgb = np.asarray(image.convert("RGB"))
    h, w = rgb.shape[:2]
    dy, dx = int(h * (1 - _CENTER_RATIO) / 2), int(w * (1 - _CENTER_RATIO) / 2)
    crop = rgb[dy : h - dy, dx : w - dx]
    return cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)


def _hist(hsv: np.ndarray) -> np.ndarray:
    # 色相と彩度で比べる(明度は照明で大きく変わるので使わない)
    hist = cv2.calcHist([hsv], [0, 1], None, [30, 16], [0, 180, 0, 256])
    return cv2.normalize(hist, hist).flatten()


def color_similarity(image: Image, reference: Image) -> float:
    score = cv2.compareHist(_hist(_center_hsv(image)), _hist(_center_hsv(reference)), cv2.HISTCMP_CORREL)
    return float(max(0.0, min(1.0, score)))


_VLM_PROMPT = (
    "1枚目は基準のキャラクター、2枚目は新しく生成した画像です。"
    "服装・ポーズ・表情・背景の違いは無視し、髪の色・髪型・髪飾り・目の色・目つき・顔立ちだけを見て、"
    "同じキャラクターに見えるかを0〜10の整数で採点してください。"
    '出力はJSONのみ: {"score": <0-10>, "reason": "<短い理由>"}'
)


def _png_b64(image: Image) -> str:
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _parse_score(text: str) -> float:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return float(json.loads(match.group(0))["score"]) / 10
        except (ValueError, KeyError, TypeError):
            pass
    number = re.search(r"\d+(\.\d+)?", text)
    if number is None:
        raise ValueError(f"採点を読み取れませんでした: {text[:100]}")
    return float(number.group(0)) / 10


async def vlm_similarity(image: Image, reference: Image) -> float:
    ref_b64, img_b64 = _png_b64(reference), _png_b64(image)
    if is_ollama_vision():
        base = get_vision_base_url().rstrip("/").removesuffix("/v1")
        async with httpx.AsyncClient(timeout=120) as http:
            res = await http.post(
                f"{base}/api/chat",
                json={
                    "model": get_vision_model(),
                    "stream": False,
                    "messages": [{"role": "user", "content": _VLM_PROMPT, "images": [ref_b64, img_b64]}],
                },
            )
            res.raise_for_status()
            text = res.json()["message"]["content"]
    else:
        completion = await get_vision_client().chat.completions.create(
            model=get_vision_model(),
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{ref_b64}"}},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                    {"type": "text", "text": _VLM_PROMPT},
                ],
            }],
        )
        text = completion.choices[0].message.content or ""
    return max(0.0, min(1.0, _parse_score(text)))


async def similarity(image: Image, reference: Image, scorer: ScorerLiteral) -> float:
    if scorer == "vlm":
        return await vlm_similarity(image, reference)
    return color_similarity(image, reference)
