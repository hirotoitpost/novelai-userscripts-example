from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional

from .models import ImageModelLiteral, ImageSizePresetLiteral


class PromptFormatRequest(BaseModel):
    rough_prompt: str
    target_model: ImageModelLiteral = "nai-diffusion-4-5-full"


class CharGenRequest(BaseModel):
    concept: str
    style: str = "anime"


class StoryDraftRequest(BaseModel):
    premise: str
    n_scenes: int = Field(3, ge=1, le=6)


class AuxTextRequest(BaseModel):
    concept: str
    target_model: ImageModelLiteral = "nai-diffusion-4-5-full"


class MetadataGenRequest(BaseModel):
    concept: str
    target_model: ImageModelLiteral = "nai-diffusion-4-5-full"
    size: ImageSizePresetLiteral = "portrait"


class ReversePromptRequest(BaseModel):
    image: str  # base64 data URL


class ReverseTag(BaseModel):
    tag: str
    probability: float
    category: str  # general | character


class ReverseCharacter(BaseModel):
    prompt: str
    negative: str = ""
    # キャラの位置(画像に対する割合)。推定のときだけ
    x: Optional[float] = None
    y: Optional[float] = None


class ReverseSettings(BaseModel):
    seed: Optional[int] = None
    steps: Optional[int] = None
    scale: Optional[float] = None
    sampler: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None


class ReverseTagsResponse(BaseModel):
    """
    画像からの逆引きの結果。source が "metadata" なら画像に埋め込まれた生成時のプロンプト(完全に
    再現できる)、"tagger" なら WD Tagger の推定。tags は推定のときの候補で、画面でしきい値を変えて選び直せる。
    """

    source: str
    positive: str
    negative: str
    characters: list[ReverseCharacter] = []
    settings: Optional[ReverseSettings] = None
    software: Optional[str] = None
    tags: list[ReverseTag] = []
    # キャラごとのプロンプトに移したタグ(全体のプロンプトには入れない)
    character_tags: list[str] = []
    rating: Optional[str] = None
    general_threshold: float
    character_threshold: float
    # 絵柄・色のタグ(しきい値を低くして拾う)
    style_threshold: float
    style_tags: list[str] = []
    model: str
