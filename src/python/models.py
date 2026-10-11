from __future__ import annotations

from typing import Any, Literal, Optional, Union

from novelai.constants.positions import PositionPreset
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
    position: Union[PositionPreset, tuple[float, float]] = (0.5, 0.5)
    enabled: bool = True


class GenerateImageRequest(BaseModel):
    prompt: str
    model: ImageModelLiteral = "nai-diffusion-4-5-full"
    size: Union[ImageSizePresetLiteral, list[int]] = "portrait"
    negative_prompt: Optional[str] = None
    quality: bool = True
    uc_preset: UCPresetLiteral = "light"
    steps: int = Field(27, ge=1, le=50)
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
    steps: int = Field(27, ge=1, le=50)
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


class CharacterDatasetRequest(BaseModel):
    """キャラシートを元に、ポーズ/服装/表情/場所を差し替えたデータセットを作る。"""

    root_name: str = Field("training_data", description="outputs/ 配下のルートフォルダ名")
    framings: list[str] = Field(default_factory=list, description="構図(full body, upper body など)")
    poses: list[str] = Field(default_factory=list)
    outfits: list[str] = Field(default_factory=list)
    expressions: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    count: int = Field(10, ge=1, le=200, description="生成枚数。組み合わせから重複なしで選ぶ")
    model: ImageModelLiteral = "nai-diffusion-4-5-full"
    width: int = Field(832, ge=64, le=1600)
    height: int = Field(1216, ge=64, le=1600)
    steps: int = Field(23, ge=1, le=50)
    scale: float = Field(5.0, ge=0.0, le=10.0)
    sampler: SamplerLiteral = "k_euler_ancestral"
    noise_schedule: NoiseScheduleLiteral = "karras"
    cfg_rescale: float = Field(0.0, ge=0.0, le=1.0)
    # 既定は検証結果(docs/trials/2026-10-06_character-dataset-stability.md)に合わせる:
    # 基準シード+容姿タグだけで見た目は安定し、参照画像の上乗せ効果は小さかったため既定はオフ。
    # 使うときは「キャラのみ・強さ0.7」(服装や構図が参照画像に引っぱられにくい)。
    use_reference: bool = Field(False, description="キャラシートの参照画像をCharacter Referenceとして使う")
    reference_type: Literal["character", "character&style"] = Field(
        "character", description="character は顔・髪だけを参照し、服装や構図を引きずりにくい"
    )
    reference_fidelity: float = Field(1.0, ge=0.0, le=1.0)
    reference_strength: float = Field(0.7, ge=0.0, le=1.0)
    # ガチャ対策: 参照画像との類似度がしきい値未満なら、シードを変えて引き直す。
    # 引き直しも1回ごとにAnlasを使うので、既定は1(引き直さない)。
    max_attempts: int = Field(1, ge=1, le=5, description="1枚あたりの最大試行回数")
    similarity_threshold: float = Field(0.7, ge=0.0, le=1.0)
    scorer: Literal["color", "vlm"] = "color"
    guard_profile_id: Optional[int] = Field(None, description="上乗せするガードプロファイル。未指定なら基本のガードのみ")
    rating: Literal["general", "r18"] = Field("general", description="r18 は成人フラグのあるキャラのみ")


class CharacterSheetPromptResponse(BaseModel):
    """キャラシートから組み立てた、画像生成ページに読み込む値。"""

    character_id: int
    name: str
    prompt: str
    negative_prompt: str
    seed: Optional[int] = None


class DatasetImportRequest(BaseModel):
    """手元の画像をデータセットに取り込む。caption 未指定ならキャラシートから作る。"""

    root_name: str = Field("training_data")
    image: str = Field(..., description="base64 または data URL")
    filename: Optional[str] = None
    caption: Optional[str] = None
    guard_profile_id: Optional[int] = None
    rating: Literal["general", "r18"] = "general"


class GuardProfileSaveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    blocked_tags: list[str] = Field(default_factory=list)
    negative_tags: str = ""


class GuardProfileResponse(BaseModel):
    id: int
    name: str
    blocked_tags: list[str]
    negative_tags: str
    created_at: str


class GuardCoreResponse(BaseModel):
    """基本のガード(変更不可)。"""

    blocked_tags: list[str]
    negative_tags: str


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


class ScriptSceneInput(BaseModel):
    """台本の1シーン(=1コマ)。text のセリフ(「」)と心の声(（）だけの行)が吹き出しになる。"""

    title: Optional[str] = Field(None, max_length=100)
    text: str = Field(min_length=1, description="セリフと、話し手の手がかりになる地の文")
    prompt_tags: str = Field("", description="コマの作画タグ(英語の danbooru タグ)")
    character_ids: list[int] = Field(default_factory=list, description="このコマに描くキャラ(コマでは名前順に左から並ぶ)")
    # キャラID → そのコマでのそのキャラの表情・動作(英語タグ)。キャラごとのプロンプトに入るので、
    # 仕草や表情が別のキャラに移らない。prompt_tags には人数・場所・時間帯・構図など全体のことだけを書く
    character_actions: dict[int, str] = Field(default_factory=dict)
    narration: Optional[str] = Field(None, max_length=80)
    sfx: Optional[list[str]] = Field(None, max_length=8)


class ScriptedStoryRequest(BaseModel):
    """台本(1シーン=1コマ)から物語を作る。series_id と volume_no を渡すとそのシリーズの巻にする。"""

    title: str = Field(min_length=1, max_length=200)
    premise: Optional[str] = Field(None, description="物語一覧の見出し。省略時は「[漫画] タイトル」")
    panels_per_page: int = Field(4, ge=1, le=12)
    scenes: list[ScriptSceneInput] = Field(min_length=1, max_length=200)
    series_id: Optional[int] = None
    volume_no: Optional[int] = Field(None, ge=1)


class SceneUpdateRequest(BaseModel):
    """シーンの手直し。送った項目だけ書き換える。text は本文(漫画のセリフの元)を差し替える。"""

    title: Optional[str] = Field(None, max_length=100)
    text: Optional[str] = Field(None, min_length=1)
    prompt_tags: Optional[str] = None


class MangaImportFile(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    data: str = Field(min_length=1, description="base64 または data URL(PDF・PNG・JPEG・zip など)")


class MangaImportAutoRequest(BaseModel):
    """
    取り込んだ漫画に似た漫画を続けて作る: 取り込んだページの絵から舞台と人物の見た目を読み取り、新しい話を
    作って、取り込んだ作品のコマ割りで漫画にする(1ページ=1話)。
    """

    # 使う登録済みのキャラ。空なら、取り込んだページの人物の見た目から新しいキャラを作る(最大2人)
    character_ids: list[int] = Field(default_factory=list, max_length=4)
    genre: str = Field("日常コメディ", max_length=100)
    # 冒頭のこのページ数だけ漫画にする(生成の数を抑えるため)
    max_pages: int = Field(4, ge=1, le=10)
    # None なら取り込んだ作品に合わせる(白黒なら白黒)
    color: Optional[bool] = None
    # 参照画像の無いキャラに、キャラシートから自動で参照画像を作る
    references: bool = True
    # False なら台本(物語)を作るところまで。コマの絵は生成しない(セリフや割り振りを直してから生成できる)
    make_images: bool = True


class MangaImportLinesRequest(BaseModel):
    """読み取ったセリフの手直し(取り込みの使い方が rebuild のとき)。コマのセリフを、渡した並びに置き換える。"""

    lines: list[str] = Field(default_factory=list, max_length=8)


class MangaImportRequest(BaseModel):
    """漫画の取り込み。files は渡した順にページになる(PDF は全ページ、zip は中の画像と PDF)。"""

    title: str = Field(min_length=1, max_length=200)
    files: list[MangaImportFile] = Field(min_length=1, max_length=100)
    # コマの役割・感情をローカルの画像モデルで読む(1コマ十数秒)。False なら人数・構図・セリフ量だけ
    use_vision: bool = True
    # 取り込みの使い方。similar: 似た漫画を作る(場面・所作・セリフの型を読む。セリフの文面は残さない)。
    # rebuild: 自分の作品を作り直す(セリフの文面も読んで残す)
    purpose: Literal["similar", "rebuild"] = "similar"
    # 読み取りが終わったら、続けて漫画を作る(similar なら似た漫画、rebuild なら作り直し)
    auto_manga: Optional[MangaImportAutoRequest] = None


class MangaDraftOutline(BaseModel):
    """大枠シナリオの1案。episodes は1話(4コマ)ごとの内容。"""

    title: str = Field(min_length=1, max_length=200)
    logline: str = ""
    episodes: list[str] = Field(min_length=1, max_length=10)


class MangaDraftLine(BaseModel):
    speaker: str = Field("", description="話し手の呼び名(空なら不明)")
    kind: Literal["speech", "thought"] = "speech"
    text: str = Field(min_length=1, max_length=80)


class MangaDraftPanel(BaseModel):
    """画面で編集する台本の1コマ。characters と speaker はキャラの呼び名(共通の姓を除いた名前)。"""

    characters: list[str] = Field(default_factory=list)
    # 呼び名 → そのコマでのそのキャラの表情・動作(英語タグ)
    actions: dict[str, str] = Field(default_factory=dict)
    lines: list[MangaDraftLine] = Field(default_factory=list, max_length=8)
    narration: str = Field("", max_length=80)
    sfx: list[str] = Field(default_factory=list, max_length=8)
    prompt_tags: str = ""
    # 台本を検めたときに自動で直した内容(画面に出すだけ)
    fixes: list[str] = Field(default_factory=list)


class MangaDraftOutlineRequest(BaseModel):
    theme: str = Field(min_length=1, max_length=500)
    genre: str = Field("日常コメディ", max_length=100)
    character_ids: list[int] = Field(min_length=1, max_length=4)
    episodes: int = Field(5, ge=1, le=10, description="話数(1話=4コマ)")
    notes: str = Field("", max_length=2000, description="人物像や設定の補足")
    # キャラごとの人物像(キャラシートのメモより優先)
    profiles: dict[int, str] = Field(default_factory=dict)
    series_id: Optional[int] = Field(None, description="続編にするシリーズ(メモリと既刊のあらすじを前提にする)")
    # 構成の参考にする取り込み(/api/manga-import)。コマ運び(構図・人数・セリフ量・役割)だけを使う
    import_id: Optional[int] = None


class MangaDraftEpisodeRequest(BaseModel):
    outline: MangaDraftOutline
    episode_index: int = Field(ge=0)
    character_ids: list[int] = Field(min_length=1, max_length=4)
    notes: str = Field("", max_length=2000)
    profiles: dict[int, str] = Field(default_factory=dict)
    series_id: Optional[int] = None
    import_id: Optional[int] = None
    previous_panels: list[MangaDraftPanel] = Field(default_factory=list, max_length=8)


class MangaDraftCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    character_ids: list[int] = Field(min_length=1, max_length=4)
    profiles: dict[int, str] = Field(default_factory=dict)
    episodes: list[list[MangaDraftPanel]] = Field(min_length=1, max_length=10)
    panels_per_page: int = Field(4, ge=1, le=12)
    series_id: Optional[int] = None
    # 省略するとシリーズの次の巻
    volume_no: Optional[int] = Field(None, ge=1)
    # 構成の参考にした取り込み。use_import_layout なら、そのページごとのコマ割りで漫画にする
    import_id: Optional[int] = None
    use_import_layout: bool = False


class StoryImportRequest(BaseModel):
    text: str = Field(min_length=1)
    n_scenes: int = Field(4, ge=1, le=20)
    panels_per_page: int = Field(8, ge=1, le=12)


class StoryPageStats(BaseModel):
    """ページごとの中身の指標。どのページを生成する価値があるか判断するために使う。"""

    page_index: int
    scenes: int
    dialogue_lines: int
    scenes_with_characters: int
    characters: list[str]
    generated: bool
    seed: Optional[int] = None


class StoryLayoutRequest(BaseModel):
    """1ページのコマ数。変更すると既存シーンのページ割り当ても振り直す。"""

    panels_per_page: int = Field(ge=1, le=12)


class StorySplitRequest(BaseModel):
    """1シーンの上限。シーン数は本文の分量から決まる。"""

    max_paragraphs: int = Field(6, ge=1, le=100)
    max_chars: int = Field(300, ge=50, le=5000)
    # 成人向け: 露骨な英語タグ(nsfw 付き)を NovelAI の文章モデル(GLM-4.6)で付ける
    adult: bool = False
    # テストや事前確認用に、冒頭のこのシーン数だけを分割・タグ付けする。未指定なら最後まで。
    # 残りは後でもう一度 /split を呼ぶと続きから分割される。
    max_scenes: int | None = Field(None, ge=1, le=1000)
    # 1巻のシーン数の目安上限。分割の結果これを超えたら、超えた分を次の巻(シリーズ)へ切り出す。
    # 0 なら巻に分けない。
    volume_max_scenes: int = Field(20, ge=0, le=1000)


# 挿絵(コマ割りページ)生成で選べるモデル。V5はこのリポジトリ独自のリクエスト組み立てで
# 実績があるもの、4.5系はSDK側で実績があり同じv4形式のボディで動くものを載せている。
MangaModelLiteral = Literal[
    "nai-diffusion-5-full",
    "nai-diffusion-4-5-full",
    "nai-diffusion-4-5-curated",
]


class MangaImageSettings(BaseModel):
    """挿絵生成のパラメータ。既定値は従来のハードコード値と同じ。"""

    # 既定値だけで生成できる必要があるので、default= を明示して型チェッカにも
    # 省略可能だと伝える(Field の第1引数だけだと省略可能と解釈されない)。
    model: MangaModelLiteral = "nai-diffusion-5-full"
    width: int = Field(default=1216, ge=512, le=2048)
    height: int = Field(default=1728, ge=512, le=2048)
    steps: int = Field(default=27, ge=1, le=50)
    scale: float = Field(default=7.0, ge=0.0, le=10.0)
    sampler: SamplerLiteral = "k_euler_ancestral"
    noise_schedule: NoiseScheduleLiteral = "karras"
    cfg_rescale: float = Field(default=0.0, ge=0.0, le=1.0)
    negative_prompt: Optional[str] = None
    # V5の新タグ。公式は「普通に良い絵なら high complexity」を推奨している。
    complexity: Optional[Literal["low", "medium", "high", "ultra"]] = "high"
    # 未指定ならページごとに別のシードを使う(従来動作)。指定すると全ページで固定する。
    seed: Optional[int] = Field(default=None, ge=0, le=4294967295)
    # 画像生成ページとプリセットを共用するために保持する。挿絵生成(V5独自ボディ)では未使用。
    variety_boost: bool = False


class StoryIllustrateRequest(BaseModel):
    """挿絵を生成するページ範囲(0始まり)。page_to 未指定なら最後まで。"""

    page_from: int = Field(0, ge=0)
    page_to: int | None = Field(None, ge=0)
    settings: MangaImageSettings = MangaImageSettings()


class StoryJobResponse(BaseModel):
    """分割/挿絵生成のバックグラウンドジョブの進捗。"""

    story_id: int
    kind: Literal[
        "split", "illustrate", "characters", "cast", "panels", "sfx", "narration", "sfx_fonts", "manga", "auto_manga"
    ]
    status: Literal["running", "done", "error", "cancelled"]
    message: str = ""
    progress: int = 0
    total: int = 0
    detail: Optional[str] = None
    started_at: Optional[float] = None
    ended_at: Optional[float] = None


class ImagePresetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    settings: MangaImageSettings


class ImagePresetResponse(BaseModel):
    id: int
    name: str
    settings: MangaImageSettings
    created_at: str


class SceneCharacterResponse(BaseModel):
    id: int
    name: str
    appearance_tags: str
    reference_image_path: Optional[str] = None
    # そのシーンでのこのキャラの表情・動作(英語タグ)
    action_tags: Optional[str] = None


class CharacterResponse(BaseModel):
    id: int
    name: str
    appearance_tags: str
    notes: Optional[str] = None
    created_at: str
    reference_image_path: Optional[str] = None
    trigger_word: Optional[str] = None
    outfit_tags: Optional[str] = None
    style_tags: Optional[str] = None
    negative_tags: Optional[str] = None
    seed: Optional[int] = None
    is_adult: bool = False


class CharacterSheetRequest(BaseModel):
    """キャラシートの更新。送った項目だけ書き換える(未送信の項目は変えない)。"""

    appearance_tags: Optional[str] = None
    trigger_word: Optional[str] = None
    outfit_tags: Optional[str] = None
    style_tags: Optional[str] = None
    negative_tags: Optional[str] = None
    seed: Optional[int] = Field(None, ge=0, le=4294967295)
    is_adult: Optional[bool] = Field(None, description="成人キャラ。未成年を示すタグがあると付けられない")


class CharacterSaveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    appearance_tags: str = ""
    notes: Optional[str] = None


class CharacterDuplicateRequest(BaseModel):
    """別名保存。元キャラのシートを複製し、sheet の項目(書きかけの変更)で上書きする。"""

    name: str = Field(min_length=1, max_length=100)
    sheet: Optional[CharacterSheetRequest] = None


class CharacterUsageResponse(BaseModel):
    stories: int
    scenes: int
    story_titles: list[str]


class StoryCharacterUsage(BaseModel):
    id: int
    name: str
    scenes: int


class ReplaceStoryCharacterRequest(BaseModel):
    from_character_id: int
    to_character_id: int


class ReplaceStoryCharacterResponse(BaseModel):
    scenes: int


class SetSceneCharactersRequest(BaseModel):
    character_ids: list[int]
    # キャラID → そのシーンでの表情・動作(英語タグ)。省略すると、残るキャラの動作はそのまま
    actions: Optional[dict[int, str]] = None


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
    # 漫画v2の効果音(描き文字)。None は未設定。
    sfx: list[str] | None = None
    # 漫画v2のナレーション。None は未設定、空文字はなし。
    narration: str | None = None
    characters: list[SceneCharacterResponse] = []


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
    # raw_text のうち、まだシーンへ分割していない残りの文字数(冒頭だけ分割した場合に正)
    unsplit_chars: int = 0
    # 漫画v2で最後に合成したときの設定(MangaV2ComposeRequest の中身)。未合成なら None。
    manga_v2_compose_settings: Optional[dict[str, Any]] = None
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
    seed: Optional[int] = None
    created_at: str


# ---- 漫画v2 (コマ単位の生成 + 自前のコマ割り・吹き出し) ----


class MangaV2Font(BaseModel):
    id: str
    label: str


class MangaV2CatalogFont(BaseModel):
    """描き文字向けにダウンロードできるフォント(Google Fonts / SIL OFL)。"""

    id: str
    label: str
    mood: str
    installed: bool
    license: str
    source_url: str


class MangaV2SfxFontRequest(BaseModel):
    """効果音の文字列ごとのフォント。font が None なら指定を外す(既定の効果音フォントに戻る)。"""

    word: str = Field(min_length=1, max_length=20)
    font: Optional[str] = None


class MangaV2StampSource(BaseModel):
    """スタンプの取り込み元(クレジット表示用)。"""

    key: str
    title: str
    author: str
    url: str
    count: int
    adult: bool = False
    created_at: str


class MangaV2StampSourceUpdate(BaseModel):
    adult: bool


class MangaV2Stamp(BaseModel):
    id: int
    source_key: str
    sheet: int
    idx: int
    width: int
    height: int
    label: str


class MangaV2StampImportRequest(BaseModel):
    """pixiv の作品URL(素材シートを1語ずつのスタンプに切り分ける)。"""

    url: str = Field(min_length=1)


class MangaV2StampZipRequest(BaseModel):
    """1語1ファイルの素材集(透過PNGのZIP)。ファイル名(例: くちゅ1_0007.png)から読みを付ける。"""

    zip: str
    adult: bool = False
    title: str = Field("", max_length=100)
    author: str = Field("", max_length=100)
    url: str = Field("", max_length=500)


class MangaV2StampUploadRequest(BaseModel):
    """手元の素材シート(透過PNG)を取り込む。BOOTH等で入手した素材向け。"""

    image: str
    title: str = Field(min_length=1, max_length=100)
    author: str = Field("", max_length=100)
    url: str = Field("", max_length=500)


class MangaV2StampLabelRequest(BaseModel):
    label: str = Field("", max_length=30)


class MangaV2SfxStampRequest(BaseModel):
    """効果音の文字列ごとのスタンプ。stamp_id が None なら外す。"""

    word: str = Field(min_length=1, max_length=20)
    stamp_id: Optional[int] = None


class MangaV2Template(BaseModel):
    id: str
    label: str
    panels: int


class MangaV2Panel(BaseModel):
    scene_id: int
    scene_index: int
    image_path: str
    seed: Optional[int] = None
    width: int
    height: int
    created_at: str


class MangaV2PanelsRequest(BaseModel):
    """コマ画像を生成するシーン範囲(0始まり)。scene_to 未指定なら最後まで。"""

    scene_from: int = Field(0, ge=0)
    scene_to: Optional[int] = Field(None, ge=0)
    template: str = "grid4"
    # 既に絵があるシーンを飛ばす。個別に描き直すときは False にする。
    skip_existing: bool = True
    # 漫画らしいモノクロが既定。カラーにしたい場合は True。
    color: bool = False
    # 参照画像を登録したキャラが出るコマで、NovelAIのキャラ参照(Character Reference)を使う。
    # V5は未対応(実機で500)なので、そのコマはV4.5 Fullで生成する。1コマあたり+5 Anlas。
    use_character_reference: bool = False
    reference_strength: float = Field(1.0, ge=0.0, le=1.0)
    # シーンごとにシードをずらす(+シーン番号×37)。キャラの基準シードや固定シードだと、全コマが
    # 同じ構図に寄ってしまうため。
    vary_seed: bool = False
    reference_fidelity: float = Field(1.0, ge=0.0, le=1.0)
    # width/height はテンプレートのコマの形から決めるので使わない。
    settings: MangaImageSettings = MangaImageSettings()


class MangaV2CharacterReferenceRequest(BaseModel):
    """
    キャラ参照の画像。アップロード画像(base64/data URL)、生成済みのコマ(scene_id)、
    参照の候補(candidate_path)のどれか。seed を付けると、キャラシートの基準シードにも登録する。
    """

    image: Optional[str] = None
    scene_id: Optional[int] = None
    candidate_path: Optional[str] = None
    seed: Optional[int] = Field(default=None, ge=0, le=4294967295)


class MangaV2ReferenceCandidatesRequest(BaseModel):
    """キャラシートから参照画像の候補を生成する。モデルはキャラ参照と同じ V4.5 が既定。"""

    count: int = Field(default=4, ge=1, le=4)
    color: bool = True
    model: Optional[str] = None


class MangaV2ReferenceCandidate(BaseModel):
    path: str
    seed: int


class MangaV2SceneSfxRequest(BaseModel):
    """シーンの効果音を手で設定する。None で未設定に戻す(AI提案の対象になる)。"""

    sfx: Optional[list[str]] = Field(None, max_length=8)


class MangaV2SceneNarrationRequest(BaseModel):
    """シーンのナレーションを手で設定する。None で未設定に戻す(AI作成の対象になる)。"""

    narration: Optional[str] = Field(None, max_length=80)


class MangaV2SuggestSfxRequest(BaseModel):
    scene_from: int = Field(0, ge=0)
    scene_to: Optional[int] = Field(None, ge=0)
    # 描き文字の自動選択で、成人向けの素材のスタンプも候補にする
    include_adult: bool = False
    # False なら効果音を設定済み(空を含む)のシーンは飛ばす
    overwrite: bool = False


class MangaV2ComposeRequest(BaseModel):
    template: str = "grid4"
    font: Optional[str] = None
    # 効果音(描き文字)のフォント。未指定なら太い角ゴシック系。
    sfx_font: Optional[str] = None
    # 吹き出しの白い地の不透明度。0で輪郭線だけ(文字には白フチが付く)。
    bubble_opacity: float = Field(1.0, ge=0.0, le=1.0)
    # 1コマのセリフの上限。超えるシーンは同じ絵の寄りのコマを足して分ける。0で分けない。
    max_lines_per_panel: int = Field(2, ge=0, le=10)
    # 文字の大きさの全体の倍率(セリフ・ナレーション / 描き文字・スタンプ)。コマの大きさや
    # 叫び・小声による自動調整に、さらに掛ける。
    text_scale: float = Field(1.0, ge=0.5, le=2.0)
    sfx_scale: float = Field(1.0, ge=0.5, le=2.0)


# ダウンロードに入れるもの。pages=合成したページ、pages_clean=セリフ・効果音・ナレーションなしのページ、
# panels=ページに嵌める前のコマに文字を入れたもの、panels_clean=生成したコマの絵そのもの。
MangaV2DownloadContent = Literal["pages", "pages_clean", "panels", "panels_clean"]


class MangaV2PageLayoutsRequest(BaseModel):
    """取り込んだ作品のコマ割りを物語に写す。null ならテンプレートに戻す。"""

    import_id: Optional[int] = None


class MangaV2MakeRequest(BaseModel):
    """
    まだ絵の無いコマを生成してから、ページに合成するまでを1つのジョブで行う(漫画ドラフトの「漫画にする」)。
    画面を閉じたり開き直したりしても、サーバー側で最後まで進む。
    """

    panels: MangaV2PanelsRequest = MangaV2PanelsRequest()
    compose: MangaV2ComposeRequest = MangaV2ComposeRequest()


class StoryAutoMangaRequest(BaseModel):
    """
    取り込んだ物語を続けて漫画にする(シーン分割 → 登場人物をそろえる・参照画像 → コマの生成と合成)。
    長い物語でも生成が増えすぎないよう、既定では冒頭の 12 シーンだけを漫画にする。
    """

    split: StorySplitRequest = Field(default_factory=lambda: StorySplitRequest(max_scenes=12, volume_max_scenes=0))
    make: MangaV2MakeRequest = MangaV2MakeRequest()
    # 参照画像の無いキャラに、キャラシートから自動で参照画像を作る
    references: bool = True


class MangaV2DownloadRequest(MangaV2ComposeRequest):
    """合成と同じ設定で作り直してダウンロードする。PDFで複数選んだ場合はPDFをまとめたzipになる。"""

    format: Literal["pdf", "zip"] = "zip"
    contents: list[MangaV2DownloadContent] = Field(default_factory=lambda: ["pages"], min_length=1)


class MangaV2Element(BaseModel):
    """合成したページ上の吹き出し/描き文字。box と panel はページ座標 [x0, y0, x1, y1]。"""

    key: str
    kind: Literal["bubble", "sfx", "narration"]
    text: str
    box: list[int]
    panel: list[int]
    moved: bool = False
    # 個別に調整した大きさの倍率(未調整なら None)
    scale: Optional[float] = None


class MangaV2ComposeResponse(BaseModel):
    pages: list[str]
    final_image_path: str
    page_width: int
    page_height: int
    page_widths: list[int] = Field(default_factory=list)
    # pages と同じ順の、各ページに置いた要素
    elements: list[list[MangaV2Element]]


class MangaV2ScaleRequest(BaseModel):
    """吹き出し/描き文字/ナレーション1つの大きさの倍率。None で自動に戻す。"""

    key: str
    scale: Optional[float] = Field(None, ge=0.3, le=3.0)


class MangaV2OverrideRequest(BaseModel):
    """吹き出し/描き文字の手動配置。x, y はコマ内の左上位置(コマに対する割合)。None で自動配置に戻す。"""

    key: str
    x: Optional[float] = Field(None, ge=0.0, le=1.0)
    y: Optional[float] = Field(None, ge=0.0, le=1.0)


# ---- 物語エディタ(NovelAI と対話しながら物語を書く) ----


class WriterModel(BaseModel):
    id: str
    label: str
    note: str
    default: bool


class WriterSettings(BaseModel):
    model: Optional[str] = None
    max_tokens: int = Field(200, ge=10, le=1000)
    temperature: float = Field(1.0, ge=0.0, le=2.0)
    top_p: float = Field(0.95, ge=0.0, le=1.0)


class WriterDraftSummary(BaseModel):
    id: int
    title: str
    story_id: Optional[int] = None
    length: int
    preview: str
    updated_at: str


class WriterDraft(BaseModel):
    id: int
    title: str
    memory: str
    author_note: str
    text: str
    settings: WriterSettings
    story_id: Optional[int] = None
    # シリーズの次の巻として書いている下書きなら、そのシリーズと巻番号
    series_id: Optional[int] = None
    volume_no: Optional[int] = None
    created_at: str
    updated_at: str


class WriterDraftUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    memory: Optional[str] = None
    author_note: Optional[str] = None
    text: Optional[str] = None
    settings: Optional[WriterSettings] = None


class WriterGenerateRequest(BaseModel):
    """生成に使う本文・メモリ・作者メモ。保存を待たずに今の入力内容で生成できるよう、毎回送る。"""

    text: str = ""
    memory: str = ""
    author_note: str = ""
    settings: WriterSettings = WriterSettings()


class WriterDuplicateRequest(BaseModel):
    """別名で保存。title を省略すると「元のタイトル (コピー)」。"""

    title: Optional[str] = Field(None, max_length=200)


class WriterToStoryRequest(BaseModel):
    panels_per_page: int = Field(4, ge=1, le=12)
