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
    seed: Optional[int] = Field(None, ge=0, le=4294967295)


class InpaintRequest(BaseModel):
    image: str
    mask: str
    strength: float = Field(1.0, ge=0.01, le=1.0)
    seed: Optional[int] = Field(None, ge=0, le=4294967295)


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
    seed: Optional[int] = Field(None, ge=0, le=4294967295)
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


class LoraDatasetRequest(BaseModel):
    character_id: str = Field(..., min_length=1, description="管理用キャラクターID（フォルダ名に使用）")
    trigger_word: str = Field(..., min_length=1, description="LoRA学習用トリガーワード")
    base_tags: str = Field("", description="外見タグなど（カンマ区切り）")
    extra_tags: str = Field("", description="追加タグ（カンマ区切り）")
    outfit_tag: str = Field("white dress", description="上半身/全身カットの衣装タグ。空文字で無効化")
    root_name: str = Field("training_data", description="outputs/ 配下のルートフォルダ名")
    model: ImageModelLiteral = "nai-diffusion-3"
    steps: int = Field(23, ge=1, le=50)
    scale: float = Field(5.0, ge=0.0, le=10.0)
    sampler: SamplerLiteral = "k_euler_ancestral"
    noise_schedule: NoiseScheduleLiteral = "karras"
    cfg_rescale: float = Field(0.0, ge=0.0, le=1.0)
    negative_prompt: str = Field(
        "worst quality, low quality, blurry, bad anatomy, "
        "extra limbs, missing fingers, ugly, duplicate"
    )
    seed: Optional[int] = Field(
        None, ge=0, le=4294967295,
        description="ベースシード値。未指定でランダム",
    )
    seed_offset: bool = Field(False, description="画像ごとにseed+indexを加算する。構図が大きく変わる")
    micro_variation_tags: bool = Field(
        False, description="シードは固定したまま末尾に光源/雰囲気タグを1つランダム追加し、微小な差分のみ出す"
    )
    shuffle_tags: bool = Field(False, description="トリガーワードを先頭固定したまま残りのタグ順序をランダムに入れ替える")
    character_reference: Optional[CharacterReferenceRequest] = Field(
        None, description="精密参照画像（Precise Character Reference）。V4.5系モデル限定"
    )
    vibe_transfer: Optional[ControlNetRequest] = Field(None, description="Vibe Transfer（雰囲気転送）")


class LoraDatasetProgressEvent(BaseModel):
    current: int
    total: int
    category: str
    file: str
    status: Literal["ok", "error"]
    message: Optional[str] = None


class LoraDatasetCompleteEvent(BaseModel):
    total: int
    succeeded: int
    failed: int
    output_path: str


class EncryptionKeyRequest(BaseModel):
    email: str
    password: str


class EncryptionKeyResponse(BaseModel):
    encryption_key: str = Field(description="base64エンコードされた32byte鍵")


class SituationCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class SituationResponse(BaseModel):
    id: int
    name: str


class SetChunkSituationsRequest(BaseModel):
    situation_ids: list[int]


class ExclusiveGroupCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ExclusiveGroupResponse(BaseModel):
    id: int
    name: str


class SetChunkExclusiveGroupsRequest(BaseModel):
    group_ids: list[int]


class ConflictCheckRequest(BaseModel):
    chunk_ids: list[str]


class ScenarioSelectRequest(BaseModel):
    situation_id: int


class RandomSelectRequest(BaseModel):
    situation_id: Optional[int] = None
    count: Optional[int] = Field(None, ge=1)


class SimilarSelectRequest(BaseModel):
    chunk_id: str
    limit: int = Field(10, ge=1, le=100)


class PresetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    chunk_ids: list[str]


class PresetUpdateRequest(BaseModel):
    chunk_ids: list[str]


class PresetSummary(BaseModel):
    id: int
    name: str
    created_at: str


class WordSelectionGenerateRequest(BaseModel):
    chunk_ids: list[str] = Field(default_factory=list)
    generation: GenerateImageRequest
    based_on: Optional[int] = Field(
        None, description="この生成の元にした generation_history.id（再生成の系譜を遡るため）"
    )


class GenerationHistoryEntry(BaseModel):
    id: int
    prompt: str
    negative_prompt: Optional[str] = None
    model: str
    size: str
    steps: int
    scale: float
    seed: Optional[int] = None
    chunk_ids: list[str]
    image_paths: list[str]
    created_at: str


class ImportImagesRequest(BaseModel):
    images: list[str] = Field(min_length=1, description="base64エンコードされたNovelAI生成画像(複数可)")


class ImportImageResult(BaseModel):
    success: bool
    id: Optional[int] = None
    error: Optional[str] = None


class MetadataExtractRequest(BaseModel):
    image: str


class MetadataExtractResponse(BaseModel):
    metadata: dict[str, Any]


class MetadataEraseRequest(BaseModel):
    image: str
    target: Literal["alpha", "png_info", "both"] = "both"


class MetadataEraseResponse(BaseModel):
    image: str


class StoryDraftCreateRequest(BaseModel):
    premise: str
    n_scenes: int = Field(4, ge=1, le=20)
    panels_per_page: int = Field(4, ge=1, le=8)


class StoryImportRequest(BaseModel):
    text: str = Field(min_length=1)
    n_scenes: int = Field(4, ge=1, le=20)
    panels_per_page: int = Field(4, ge=1, le=8)


class StorySceneResponse(BaseModel):
    id: int
    story_id: int
    scene_index: int
    page_index: int
    draft_title: str | None
    draft_text: str
    draft_prompt_tags: str
    seed_cue: str | None
    novelai_text: str | None


class StoryResponse(BaseModel):
    id: int
    premise: str
    title: str | None
    n_scenes: int
    panels_per_page: int
    status: str
    created_at: str
    final_image_path: str | None = None
    raw_text: str | None = None
    scenes: list[StorySceneResponse]


class StorySummary(BaseModel):
    id: int
    premise: str
    title: str | None
    status: str
    created_at: str
    final_image_path: str | None = None


class MangaPageResponse(BaseModel):
    id: int
    story_id: int
    page_index: int
    image_path: str
    scene_ids: list[int]
    created_at: str
