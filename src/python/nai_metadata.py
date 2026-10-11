"""
NovelAI で生成した画像に埋め込まれた設定を読み、このアプリの画像生成ページの設定にする。

埋め込みは2か所にある: PNG のテキスト(Comment。保存し直すと消えることがある)と、画素に隠した埋め込み(Stealth。
アルファチャンネルに入っている)。両方を読む(read_comment)。

generation_settings() は、読んだ設定を画像生成のリクエスト(models.GenerateImageRequest)に入れられる値に直す。
埋め込みのプロンプトは NovelAI に送られた最終形(品質タグ・UC プリセットを足した後)なので、読み込んだときは
品質タグと UC プリセットを切る(二重に足さないため)。最終形の取り方は final_prompt() を参照。このページで再現できない設定は notes に書く。
"""

from __future__ import annotations

import ast
import io
import json
import re
from typing import Any, get_args

from PIL import Image

from .models import ImageSizePresetLiteral, NoiseScheduleLiteral, SamplerLiteral

# サイズのプリセット(models.ImageSizePresetLiteral と同じ名前)
SIZE_PRESETS: dict[str, tuple[int, int]] = {
    "portrait": (832, 1216),
    "landscape": (1216, 832),
    "square": (1024, 1024),
    "large_portrait": (1024, 1536),
    "large_landscape": (1536, 1024),
}
assert set(SIZE_PRESETS) == set(get_args(ImageSizePresetLiteral))

# 埋め込みの Source の末尾の印(モデルごとに決まった値)。Full と Curated を見分けるのに使う
_SOURCE_MODELS = {
    "0ADF9AB7": "nai-diffusion-5-full",
    "4BDE2A90": "nai-diffusion-4-5-full",
    "C02D4F98": "nai-diffusion-4-5-curated",
    "37442FCA": "nai-diffusion-4-full",
    "7ABFFA2A": "nai-diffusion-4-curated",
}
_SAMPLER_ALIASES = {"ddim_v3": "ddim", "k_dpmpp_2m_sde": "k_dpmpp_sde"}
_MAX_SIDE = 2048
_MAX_SEED = 4294967295


def _as_dict(value: Any) -> dict[str, Any]:
    """v4_prompt は JSON 文字列のことも、Python の repr の文字列のこともある(SDK の版による)。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        for load in (json.loads, ast.literal_eval):
            try:
                loaded = load(value)
            except (ValueError, SyntaxError):
                continue
            return loaded if isinstance(loaded, dict) else {}
    return {}


def read_comment(data: bytes) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """
    画像の(埋め込み全体, その中の Comment = 生成の設定)。NovelAI の埋め込みが無ければ None。
    PNG のテキストを先に見て、無ければ画素に隠した埋め込みを読む。
    """
    from novelai._utils.nai_meta import extract_image_metadata

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:  # noqa: BLE001 読めない画像は呼び出し側で扱う
        return None
    metadata: Any = dict(image.info) if image.info.get("Comment") else None
    if metadata is None:
        try:
            metadata = extract_image_metadata(image)
        except Exception:  # noqa: BLE001 埋め込みが無い・壊れている
            return None
        if not isinstance(metadata, dict):
            metadata = metadata[0]
    comment = metadata.get("Comment") if isinstance(metadata, dict) else None
    if isinstance(comment, str):
        try:
            comment = json.loads(comment)
        except json.JSONDecodeError:
            return None
    if not isinstance(comment, dict) or not comment.get("prompt"):
        return None
    return metadata, comment


def characters_of(comment: dict[str, Any]) -> list[dict[str, Any]]:
    """キャラごとのプロンプト(V4 以降)。x, y は画像に対する位置(無ければ None)。"""
    v4 = _as_dict(comment.get("v4_prompt"))
    v4_negative = _as_dict(comment.get("v4_negative_prompt"))
    captions = (v4.get("caption") or {}).get("char_captions") or []
    negatives = (v4_negative.get("caption") or {}).get("char_captions") or []
    characters = []
    for index, caption in enumerate(captions):
        if not isinstance(caption, dict):
            continue
        negative = negatives[index].get("char_caption", "") if index < len(negatives) else ""
        centers = caption.get("centers") or []
        center = centers[0] if centers and isinstance(centers[0], dict) else {}
        characters.append(
            {
                "prompt": str(caption.get("char_caption") or ""),
                "negative": str(negative or ""),
                "x": center.get("x"),
                "y": center.get("y"),
            }
        )
    return characters


def _base_caption(value: Any) -> str:
    caption = _as_dict(value).get("caption")
    return str(caption.get("base_caption") or "") if isinstance(caption, dict) else ""


def final_prompt(comment: dict[str, Any]) -> str:
    """
    NovelAI に送られた最終形のプロンプト(品質タグを足した後)。V4 以降は v4_prompt の base_caption がそれで、
    一番上の prompt には品質タグを足す前のものが入っていることがある(novelai-sdk で生成した画像。実機で確認)。
    """
    return _base_caption(comment.get("v4_prompt")) or str(comment["prompt"])


def final_negative(comment: dict[str, Any]) -> str:
    """NovelAI に送られた最終形のネガティブプロンプト(UC プリセットを足した後)。"""
    return _base_caption(comment.get("v4_negative_prompt")) or str(comment.get("uc") or "")


def uses_coords(comment: dict[str, Any]) -> bool:
    """キャラの位置を指定して生成した画像か(use_coords)。指定していなければ、位置は AI まかせ。"""
    if isinstance(comment.get("use_coords"), bool):
        return comment["use_coords"]
    return bool(_as_dict(comment.get("v4_prompt")).get("use_coords"))


def model_of(source: str) -> tuple[str | None, str | None]:
    """埋め込みの Source から(このアプリのモデル名, 注意書き)。分からなければモデル名は None。"""
    code = source.rsplit(" ", 1)[-1].upper() if source else ""
    if code in _SOURCE_MODELS:
        return _SOURCE_MODELS[code], None
    if "V5" in source:
        return "nai-diffusion-5-full", f"モデルの種類(Full / Curated)が分からないので、V5 Full にしました({source})"
    if "V4.5" in source:
        return "nai-diffusion-4-5-full", f"モデルの種類(Full / Curated)が分からないので、V4.5 Full にしました({source})"
    if re.search(r"V4(\s|$)", source):
        return "nai-diffusion-4-full", f"モデルの種類(Full / Curated)が分からないので、V4 Full にしました({source})"
    if "Stable Diffusion XL" in source or "V3" in source:
        return "nai-diffusion-3", None
    return None, f"この画像のモデル({source or '不明'})はこのページで選べないので、モデルは変えていません"


def _number(comment: dict[str, Any], key: str) -> float | None:
    try:
        value = comment[key]
        return None if value is None or isinstance(value, bool) else float(value)
    except (KeyError, TypeError, ValueError):
        return None


def generation_settings(data: bytes) -> dict[str, Any] | None:
    """画像に埋め込まれた設定を、画像生成ページの設定にしたもの。NovelAI の埋め込みが無ければ None。"""
    found = read_comment(data)
    if found is None:
        return None
    metadata, comment = found
    notes: list[str] = []
    source = str(metadata.get("Source") or "")
    model, model_note = model_of(source)
    if model_note:
        notes.append(model_note)

    width, height = _number(comment, "width"), _number(comment, "height")
    size_ok = (
        width is not None
        and height is not None
        and 64 <= width <= _MAX_SIDE
        and 64 <= height <= _MAX_SIDE
        and width % 64 == 0
        and height % 64 == 0
    )
    if not size_ok:
        notes.append("サイズを読めなかったので、サイズは変えていません")
    size_preset = (
        next((name for name, dims in SIZE_PRESETS.items() if dims == (width, height)), None) if size_ok else None
    )

    sampler = str(comment.get("sampler") or "")
    sampler = _SAMPLER_ALIASES.get(sampler, sampler)
    if sampler not in get_args(SamplerLiteral):
        if sampler:
            notes.append(f"サンプラー {sampler} はこのページで選べないので、サンプラーは変えていません")
        sampler = ""
    noise_schedule = str(comment.get("noise_schedule") or "")
    if noise_schedule not in get_args(NoiseScheduleLiteral):
        if noise_schedule:
            notes.append(f"ノイズスケジュール {noise_schedule} はこのページで選べないので、変えていません")
        noise_schedule = ""

    steps, scale, cfg_rescale, seed = (_number(comment, k) for k in ("steps", "scale", "cfg_rescale", "seed"))
    if steps is not None and not 1 <= steps <= 50:
        notes.append(f"ステップ数 {int(steps)} は範囲の外なので、50 までに収めました")
    if scale is not None and not 0 <= scale <= 10:
        notes.append(f"プロンプトガイダンス {scale} は範囲の外なので、0〜10 に収めました")
    if seed is not None and not 0 <= seed <= _MAX_SEED:
        notes.append("シード値が範囲の外なので、空(毎回ランダム)にしました")
        seed = None

    # このページで再現できない設定
    request_type = str(comment.get("request_type") or "")
    if "Img2Img" in request_type or comment.get("strength") is not None:
        notes.append("元は画像から生成(i2i)した画像です。元の画像は入っていないので、同じ絵にはなりません")
    if "Inpaint" in request_type or comment.get("add_original_image"):
        notes.append("元はインペイントで作った画像です。元の画像とマスクは入っていないので、同じ絵にはなりません")
    if comment.get("reference_strength_multiple") or comment.get("reference_image_multiple"):
        notes.append("バイブス(参照画像)を使った画像です。参照画像は入っていないので、同じ絵にはなりません")
    if comment.get("director_reference_strengths") or comment.get("director_reference_descriptions"):
        notes.append("キャラ参照を使った画像です。参照画像は入っていないので、同じ絵にはなりません")
    if comment.get("controlnet_model"):
        notes.append("ControlNet を使った画像です。その設定は読み込みません")
    if comment.get("sm") or comment.get("sm_dyn"):
        notes.append("SMEA を使った画像です(このページには無い設定なので、読み込みません)")
    if comment.get("dynamic_thresholding"):
        notes.append("Decrisper を使った画像です(このページには無い設定なので、読み込みません)")

    return {
        "prompt": final_prompt(comment),
        "negative_prompt": final_negative(comment),
        "characters": characters_of(comment),
        "use_coords": uses_coords(comment),
        "model": model,
        "width": int(width) if size_ok and width is not None else None,
        "height": int(height) if size_ok and height is not None else None,
        "size_preset": size_preset,
        "steps": int(min(max(steps, 1), 50)) if steps is not None else None,
        "scale": min(max(scale, 0.0), 10.0) if scale is not None else None,
        "sampler": sampler or None,
        "noise_schedule": noise_schedule or None,
        "cfg_rescale": min(max(cfg_rescale, 0.0), 1.0) if cfg_rescale is not None else None,
        "variety_boost": comment.get("skip_cfg_above_sigma") is not None,
        "seed": int(seed) if seed is not None else None,
        "source": source,
        "notes": notes,
    }
