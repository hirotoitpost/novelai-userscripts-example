from __future__ import annotations

from pydantic import BaseModel, Field

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
