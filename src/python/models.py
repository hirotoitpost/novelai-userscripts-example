from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field

ImageModelLiteral = Literal[
    "nai-diffusion-4-5-full",
    "nai-diffusion-4-5-curated",
    "nai-diffusion-4-full",
    "nai-diffusion-4-curated",
    "nai-diffusion-3",
    "nai-diffusion-3-furry",
]

SamplerLiteral = Literal[
    "k_euler",
    "k_euler_ancestral",
    "k_dpm_2",
    "k_dpm_2_ancestral",
    "k_dpmpp_2m",
    "k_dpmpp_2s_ancestral",
    "k_dpmpp_sde",
    "ddim",
]

NoiseScheduleLiteral = Literal["karras", "exponential", "polyexponential"]

UCPresetLiteral = Literal["strong", "light", "furry_focus", "human_focus", "none"]

ImageSizePresetLiteral = Literal[
    "portrait", "landscape", "square", "large_portrait", "large_landscape"
]


class I2iRequest(BaseModel):
    image: str
    strength: float = Field(0.7, ge=0.01, le=0.99)
    noise: float = Field(0.0, ge=0.0, le=0.99)
    seed: Optional[int] = Field(None, ge=0, le=999999999)


class InpaintRequest(BaseModel):
    image: str
    mask: str
    strength: float = Field(1.0, ge=0.01, le=1.0)
    seed: Optional[int] = Field(None, ge=0, le=999999999)


class ControlNetImageRequest(BaseModel):
    image: str
    info_extracted: float = Field(0.7, ge=0.01, le=1.0)
    strength: float = Field(0.6, ge=0.01, le=1.0)
    controlnet_model: ImageModelLiteral = "nai-diffusion-4-5-full"


class ControlNetRequest(BaseModel):
    images: list[ControlNetImageRequest]
    strength: float = Field(1.0, ge=0.0, le=1.0)


class CharacterReferenceRequest(BaseModel):
    image: str
    type: Literal["character", "style", "character&style"] = "character&style"
    fidelity: float = Field(1.0, ge=0.0, le=1.0)
    strength: float = Field(1.0, ge=0.0, le=1.0)


class CharacterRequest(BaseModel):
    prompt: str
    negative_prompt: str = ""
    position: Union[str, list[float]] = Field(default_factory=lambda: [0.5, 0.5])
    enabled: bool = True


class GenerateImageRequest(BaseModel):
    prompt: str
    model: ImageModelLiteral = "nai-diffusion-4-5-full"
    size: Union[ImageSizePresetLiteral, list[int]] = "portrait"
    negative_prompt: Optional[str] = None
    quality: bool = True
    uc_preset: UCPresetLiteral = "light"
    steps: int = Field(23, ge=1, le=50)
    scale: float = Field(5.0, ge=0.0, le=10.0)
    sampler: SamplerLiteral = "k_euler_ancestral"
    noise_schedule: NoiseScheduleLiteral = "karras"
    seed: Optional[int] = Field(None, ge=0, le=999999999)
    n_samples: int = Field(1, ge=1, le=8)
    cfg_rescale: float = Field(0.0, ge=0.0, le=1.0)
    variety_boost: bool = False
    image_format: Optional[Literal["webp", "png"]] = None
    i2i: Optional[I2iRequest] = None
    inpaint: Optional[InpaintRequest] = None
    controlnet: Optional[ControlNetRequest] = None
    character_references: Optional[list[CharacterReferenceRequest]] = None
    characters: Optional[list[CharacterRequest]] = None


class GenerateImageResponse(BaseModel):
    images: list[str]
    format: str = "png"


class StreamChunk(BaseModel):
    event_type: Literal["intermediate", "final"]
    samp_ix: int
    step_ix: int
    gen_id: int
    sigma: float
    image: str


class AnlasEstimateRequest(BaseModel):
    params: GenerateImageRequest
    is_opus: bool = False


class AnlasEstimateResponse(BaseModel):
    model: str
    total_anlas: int
    base_anlas: int
    character_reference_anlas: int
    vibe_encoding_anlas: int
    vibe_reference_anlas: int
    per_image_anlas: int
    requested_samples: int
    billable_samples: int
    strength_factor: float
    opus_discount_applied: bool


class MetadataExtractRequest(BaseModel):
    image: str


class MetadataExtractResponse(BaseModel):
    metadata: dict[str, Any]


class MetadataEraseRequest(BaseModel):
    image: str
    target: Literal["alpha", "png_info", "both"] = "both"


class MetadataEraseResponse(BaseModel):
    image: str
