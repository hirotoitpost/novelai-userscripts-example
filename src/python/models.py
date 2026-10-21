from __future__ import annotations

from typing import Any, Literal, Optional, Union

from novelai.constants.positions import PositionPreset
from pydantic import BaseModel, Field

ImageModelLiteral = Literal[
    "nai-diffusion-5-full",
    "nai-diffusion-5-curated",
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
    # キャラの位置(画像に対する割合)。None なら位置は AI にまかせる。1人でも位置を指定すると、指定の無い
    # キャラは中央になる(novelai-sdk 0.14.1 以降。それより前の SDK は位置を送っても NovelAI に無視されていた)
    position: Optional[Union[PositionPreset, tuple[float, float]]] = None
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


EmotionLiteral = Literal[
    "neutral", "happy", "sad", "angry", "scared", "surprised", "tired", "excited", "nervous", "thinking",
    "confused", "shy", "disgusted", "smug", "bored", "laughing", "irritated", "aroused", "embarrassed",
    "worried", "love", "determined", "hurt", "playful",
]  # fmt: skip


class EmotionToolRequest(BaseModel):
    """絵はそのままで、人物の表情だけを変える(NovelAI のディレクターツール)。"""

    image: str = Field(min_length=1, description="base64 または data URL")
    emotion: EmotionLiteral
    # 変えたい内容の補足(任意。英語のタグ)
    prompt: str = Field("", max_length=500)
    # 元の絵からどれだけ離れてよいか(0 が最も元に近い)
    defry: int = Field(0, ge=0, le=5)


class UpscaleToolRequest(BaseModel):
    """画像を縦横2倍に拡大する(NovelAI の拡大)。"""

    image: str = Field(min_length=1, description="base64 または data URL")


class RegionRedrawRequest(BaseModel):
    """ギャラリーの画像の範囲を、高解像度で描き直す(切り出して img2img)。"""

    key: str = Field(min_length=1, description="ギャラリーの画像のキー")
    # redraw: AI 拡大してから img2img で描き直す / upscale: AI 拡大だけ(NovelAI を使わない)
    mode: Literal["redraw", "upscale"] = "redraw"
    # 元の画像のピクセルでの範囲 [x, y, 幅, 高さ]
    box: list[int] = Field(min_length=4, max_length=4)
    # 弱いほど元の絵に近い(線と細部だけ描き直す)
    strength: float = Field(0.35, ge=0.1, le=0.8)
    # 無ければ元の画像のシード
    seed: Optional[int] = Field(None, ge=0, le=4294967295)


class EnhanceRequest(BaseModel):
    """ギャラリーの画像全体の画質を上げる(whole_enhance.py)。"""

    key: str = Field(min_length=1, description="ギャラリーの画像のキー")
    # upscale: AI 拡大だけ(NovelAI を使わない)/ redraw: 1MP 以内で img2img で描き直してから AI 拡大
    mode: Literal["upscale", "redraw"] = "upscale"
    # 縦横の倍率(長い辺は 4096px まで)
    scale: Literal[2, 4] = 2
    strength: float = Field(0.35, ge=0.1, le=0.8)
    seed: Optional[int] = Field(None, ge=0, le=4294967295)


class RegionRedrawResponse(BaseModel):
    key: str
    path: str
    width: int
    height: int
    box: list[int]
    mode: Literal["redraw", "upscale"]
    # img2img の元をどう大きくしたか(ai: AI 拡大 / simple: モデルが無く単純な拡大)
    enlarged_by: Literal["ai", "simple"]
    prompt: str
    # 引き継いだキャラごとのプロンプトの数
    characters: int


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


class DatasetVariationsAddRequest(BaseModel):
    """キャラ別データセットのバリエーションの候補を足す。axis は framings / poses / outfits / expressions / locations。"""

    axis: Literal["framings", "poses", "outfits", "expressions", "locations"]
    values: list[str] = Field(min_length=1, max_length=100)


class DatasetVariationsExtractRequest(BaseModel):
    """プロンプト(画像生成で使ったもの・リバースプロンプトの結果など)から、バリエーションの候補を取り出す。"""

    prompts: list[str] = Field(min_length=1, max_length=400)
    # プロンプトごとの軸の見当(チャンクの分類から付けたもの。無ければ null)。軸が分からなかったタグに使う
    hints: Optional[list[Optional[str]]] = Field(None, max_length=400)


class DatasetSituationRequest(BaseModel):
    """
    キャラ別データセットのシチュエーション。軸(構図・ポーズ・服装・表情・場所)ごとに、このシチュエーションで
    使うものを並べる(空の軸は、ふつうのバリエーションから選ぶ)。extra_tags は、いつも足すタグ(時間帯など)。
    """

    name: str = Field(min_length=1, max_length=100)
    framings: list[str] = Field(default_factory=list, max_length=30)
    poses: list[str] = Field(default_factory=list, max_length=30)
    outfits: list[str] = Field(default_factory=list, max_length=30)
    expressions: list[str] = Field(default_factory=list, max_length=30)
    locations: list[str] = Field(default_factory=list, max_length=30)
    extra_tags: str = Field("", max_length=500)


class DatasetStateRequest(BaseModel):
    """キャラ別データセットの状態。容姿から remove_tags を外して add_tags を足す(例: 食べ過ぎでお腹が出る)。"""

    name: str = Field(min_length=1, max_length=100)
    add_tags: str = Field("", max_length=500)
    remove_tags: str = Field("", max_length=500)
    # ふだんの絵から描き直す部位(インペイントで作るとき)。none はプロンプトだけで作る
    part: Literal["none", "belly", "chest", "torso", "legs"] = "none"


class CharacterDatasetRequest(BaseModel):
    """キャラシートを元に、ポーズ/服装/表情/場所を差し替えたデータセットを作る。"""

    root_name: str = Field("training_data", description="outputs/ 配下のルートフォルダ名")
    framings: list[str] = Field(default_factory=list, description="構図(full body, upper body など)")
    poses: list[str] = Field(default_factory=list)
    outfits: list[str] = Field(default_factory=list)
    expressions: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    # シチュエーション(1枚ごとにどれか1つ。決めている軸はその中からだけ選ぶ)と状態(容姿を一時的に変える)
    situation_ids: list[int] = Field(default_factory=list, max_length=50)
    state_ids: list[int] = Field(default_factory=list, max_length=20)
    # 状態を選んだとき、状態なし(ふだんの体)の枚も作るか
    include_plain_state: bool = True
    # 状態の作り方。prompt: 状態のタグで生成する / inpaint: ふだんの体で生成した絵から、状態の部位だけを描き直す
    # (顔・服・背景が同じで体だけが違う組ができる。部位の無い状態はプロンプトで作る)。inpaint のときの count は、
    # ふだんの体の絵の枚数(1枚ごとに、選んだ状態の数だけ描き直しの絵が増える)
    state_method: Literal["prompt", "inpaint"] = "prompt"
    inpaint_strength: float = Field(1.0, ge=0.3, le=1.0)
    # 顔の切り抜きも保存する(頭の検出で切り、faces/ に置く。小さすぎる顔は飛ばす)
    save_face_crops: bool = False
    count: int = Field(10, ge=1, le=200, description="生成枚数。組み合わせから重複なしで選ぶ")
    model: ImageModelLiteral = "nai-diffusion-4-5-full"
    width: int = Field(832, ge=64, le=1600)
    height: int = Field(1216, ge=64, le=1600)
    steps: int = Field(23, ge=1, le=50)
    scale: float = Field(5.0, ge=0.0, le=10.0)
    sampler: SamplerLiteral = "k_euler_ancestral"
    noise_schedule: NoiseScheduleLiteral = "karras"
    cfg_rescale: float = Field(0.0, ge=0.0, le=1.0)
    quality: bool = True
    uc_preset: UCPresetLiteral = "light"
    variety_boost: bool = False
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


class MetadataGenerationCharacter(BaseModel):
    prompt: str
    negative: str = ""
    x: Optional[float] = None
    y: Optional[float] = None


class MetadataGenerationResponse(BaseModel):
    """
    画像に埋め込まれた NovelAI の設定を、画像生成ページの設定にしたもの。読めなかった・このページで選べない
    項目は None(その項目は今の設定のままにする)。prompt / negative_prompt は NovelAI に送られた最終形なので、
    読み込んだら品質タグと UC プリセットは切る。
    """

    prompt: str
    negative_prompt: str = ""
    characters: list[MetadataGenerationCharacter] = Field(default_factory=list)
    # キャラの位置を指定して生成した画像か(False なら位置は AI まかせ)
    use_coords: bool = False
    model: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    # width × height がサイズのプリセットと同じなら、その名前
    size_preset: Optional[str] = None
    steps: Optional[int] = None
    scale: Optional[float] = None
    sampler: Optional[str] = None
    noise_schedule: Optional[str] = None
    cfg_rescale: Optional[float] = None
    variety_boost: bool = False
    seed: Optional[int] = None
    source: str = ""
    # 再現できない設定や、収めた値の説明(画面に出す)
    notes: list[str] = Field(default_factory=list)


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
    # キャラID → そのコマでの服装(英語タグ)。あれば、キャラシートのふだんの服装の代わりに使う(水着・浴衣など)
    character_outfits: dict[int, str] = Field(default_factory=dict)
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
    # None なら、取り込みに割り当てたジャンル(無ければ 日常コメディ)
    genre: Optional[str] = Field(None, max_length=100)
    # 冒頭のこのページ数だけ漫画にする(生成の数を抑えるため)
    max_pages: int = Field(4, ge=1, le=10)
    # None なら取り込んだ作品に合わせる(白黒なら白黒)
    color: Optional[bool] = None
    # 参照画像の無いキャラに、キャラシートから自動で参照画像を作る
    references: bool = True
    # False なら台本(物語)を作るところまで。コマの絵は生成しない(セリフや割り振りを直してから生成できる)
    make_images: bool = True


class CastPresetRequest(BaseModel):
    """キャストのセット(よく使うキャラの組み合わせ)。同じ名前なら上書き。"""

    name: str = Field(min_length=1, max_length=100)
    character_ids: list[int] = Field(min_length=1, max_length=8)


class QuickMangaRequest(BaseModel):
    """おまかせ漫画: テーマから、登場人物(選ばなければ新しく考える)→ 大枠 → 台本 → 漫画まで一気に作る。"""

    theme: str = Field(min_length=1, max_length=500)
    genre: str = Field("日常コメディ", max_length=100)
    # 話数(1話=4コマ。1ページ=4コマ)。dynamic_panels なら 1話=1ページ(ページ数)
    episodes: int = Field(2, ge=1, le=10)
    # ページごとにコマ数を変える(min_panels〜max_panels。内容に合わせて大枠を作るときに決める)。コマ割りもコマ数に合わせる
    dynamic_panels: bool = False
    min_panels: int = Field(3, ge=1, le=6)
    max_panels: int = Field(6, ge=1, le=6)
    # シリーズの続き(次の巻)にする。シリーズの設定と既刊のあらすじを大枠に渡す
    series_id: Optional[int] = None
    notes: str = Field("", max_length=1000)
    # 使うキャラ。空なら、テーマに合う登場人物を cast_count 人考えて、キャラシートを作る
    character_ids: list[int] = Field(default_factory=list, max_length=4)
    cast_count: int = Field(2, ge=1, le=4)
    # 参照画像の無いキャラに参照画像を作る(候補を reference_candidates 枚生成して、一番キャラシートに近いもの)
    references: bool = True
    reference_candidates: int = Field(2, ge=1, le=4)
    template: str = "vertical4"
    color: bool = True
    # コマの生成でキャラ参照を使う(1コマ +5 Anlas。V4.5 で生成)
    use_character_reference: bool = False
    # 台本(物語)を作るところまでにする(コマの絵は、物語のページで確かめてから生成)
    stop_after_script: bool = False
    # 台本を見直して、セリフと筋に要る物・状態を作画タグに足す(生成の前)
    review_script: bool = True
    # コマの絵を確かめて、外れたコマを描き直す(生成の後)
    check_panels: bool = True
    # 状態(透明 など)の人物がいるコマを、その人物の範囲だけ描き直す
    redraw_states: bool = True


class GalleryMangaRequest(BaseModel):
    """ギャラリーで選んだ画像から漫画を作る(1枚=1コマ。選んだ順)。"""

    image_keys: list[str] = Field(min_length=1, max_length=100)
    # 漫画ドラフトのジャンル(空なら 日常コメディ)。話・セリフ・描き文字と、絵の手直しをこれに合わせる
    genre: str = Field("", max_length=100)
    # 話の方向や補足(任意)
    theme: str = Field("", max_length=500)
    # 空なら、文章モデルが付けたタイトル
    title: str = Field("", max_length=200)
    color: bool = True
    # False なら台本(物語)を作るところまでで、コマの絵は生成しない
    make_images: bool = True


class MangaGenreRequest(BaseModel):
    """漫画のジャンルを足す。"""

    name: str = Field(min_length=1, max_length=100)


class MangaImportUpdateRequest(BaseModel):
    """取り込みの手直し。genre は漫画ドラフトのジャンルへの割り当て(空文字で外す)。"""

    genre: Optional[str] = Field(None, max_length=100)


class MangaImportLinesRequest(BaseModel):
    """読み取ったセリフの手直し(取り込みの使い方が rebuild のとき)。コマのセリフを、渡した並びに置き換える。"""

    lines: list[str] = Field(default_factory=list, max_length=8)


class MangaImportSfxItem(BaseModel):
    """描き文字1つ。box はページ上の位置(x0, y0, x1, y1)。見落とされたものを手で足すときは box なし。"""

    box: Optional[list[float]] = Field(None, min_length=4, max_length=4)
    reading: str = Field("", max_length=20)


class MangaImportSfxRequest(BaseModel):
    """コマの描き文字の手直し。渡した並びに置き換える(候補を外す・読みを入れる・手で足す)。"""

    sfx: list[MangaImportSfxItem] = Field(default_factory=list, max_length=20)


class MangaImportRequest(BaseModel):
    """漫画の取り込み。files は渡した順にページになる(PDF は全ページ、zip は中の画像と PDF)。"""

    title: str = Field(min_length=1, max_length=200)
    files: list[MangaImportFile] = Field(min_length=1, max_length=100)
    # コマの役割・感情をローカルの画像モデルで読む(1コマ十数秒)。False なら人数・構図・セリフ量だけ
    use_vision: bool = True
    # 取り込みの使い方。similar: 似た漫画を作る(場面・所作・セリフの型を読む。セリフの文面は残さない)。
    # rebuild: 自分の作品を作り直す(セリフの文面も読んで残す)
    purpose: Literal["similar", "rebuild"] = "similar"
    # 漫画ドラフトのジャンルへの割り当て(日常コメディ など。空なら割り当てない)。漫画ドラフトで同じジャンルを
    # 選ぶと構成の参考の先頭に出て、似た漫画を作るときのジャンルにもなる
    genre: str = Field("", max_length=100)
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
    # 呼び名 → そのコマでの服装(水着・浴衣など、ふだんと違うときだけ。キャラシートのふだんの服装の代わりに使う)
    outfits: dict[str, str] = Field(default_factory=dict)
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
        "split", "illustrate", "characters", "cast", "panels", "sfx", "narration", "sfx_fonts", "manga", "auto_manga",
        "check_panels", "redraw_states"
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
    # そのシーンでの服装(空ならキャラシートのふだんの服装)
    scene_outfit_tags: Optional[str] = None
    # そのシーンでの状態(キャラ別データセットの状態)。appearance_tags は状態を当てたあとの見た目
    state_id: Optional[int] = None
    state_name: Optional[str] = None


class CharacterResponse(BaseModel):
    id: int
    name: str
    appearance_tags: str
    notes: Optional[str] = None
    created_at: str
    reference_image_path: Optional[str] = None
    # 標準の体型(顔・髪・目は appearance_tags)
    body_tags: Optional[str] = None
    trigger_word: Optional[str] = None
    outfit_tags: Optional[str] = None
    style_tags: Optional[str] = None
    negative_tags: Optional[str] = None
    seed: Optional[int] = None
    is_adult: bool = False
    # 未成年キャラ(定義してあれば、性的な場面には出さない)
    is_minor: bool = False


class CharacterSheetRequest(BaseModel):
    """キャラシートの更新。送った項目だけ書き換える(未送信の項目は変えない)。"""

    appearance_tags: Optional[str] = None
    # 標準の体型(キャラ別データセットの状態は、ここを置き換える)
    body_tags: Optional[str] = None
    trigger_word: Optional[str] = None
    outfit_tags: Optional[str] = None
    style_tags: Optional[str] = None
    negative_tags: Optional[str] = None
    seed: Optional[int] = Field(None, ge=0, le=4294967295)
    is_adult: Optional[bool] = Field(None, description="成人キャラ。未成年を示すタグがあると付けられない")
    is_minor: Optional[bool] = Field(None, description="未成年キャラ。外すにはコンテンツガードのパスワードが要る")


class CharacterSplitRequest(BaseModel):
    prompt: str = Field("", max_length=8000)


class CharacterSplitResponse(BaseModel):
    """プロンプトをキャラシートの項目に分けたもの。other はキャラシートに入れないタグ(表情・ポーズ・場所など)。"""

    appearance: str
    outfit: str
    style: str
    other: str


class CharacterNewRequest(CharacterSheetRequest):
    """キャラを新しく作る(同じ名前があれば作らない)。キャラシートの項目も一緒に入れる。"""

    name: str = Field(min_length=1, max_length=100)
    notes: Optional[str] = None


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
    # キャラID → そのシーンでの服装(英語タグ。空ならふだんの服装)。省略すると、残るキャラの服装はそのまま
    outfits: Optional[dict[int, str]] = None
    # キャラID → そのシーンでの状態(dataset_states の id。null で状態なし)。省略すると、残るキャラの状態はそのまま
    states: Optional[dict[int, Optional[int]]] = None


class ApplySceneSituationRequest(BaseModel):
    """シチュエーションのタグを、シーンのタグとキャラごとの服装・動作に書き込む(書き込んだあとは手で直せる)。"""

    situation_id: int
    # 軸(framings / poses / outfits / expressions / locations) → 使う値。省略した軸は最初の値
    choices: dict[str, str] = Field(default_factory=dict)


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
    id: int
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
    # manga: 漫画のコマと同じ組み立て(manga style)。illustration: キャラ別データセットと同じ組み立て(キャラシートの画風)
    style: Literal["manga", "illustration"] = "manga"


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


class MangaV2CheckRequest(BaseModel):
    """
    コマの絵を確かめて、外れたコマを描き直す(panel_check.py)。作画タグのうち判定モデルで見分けられるものと、描く人数を
    確かめ、外れたコマは、描かれなかったタグを強めて(NovelAI の 1.5::タグ::)シードを変えて描き直す。描き直しの方が
    よくなったときだけ置き換える。終わったらページに合成し直す。
    """

    panels: MangaV2PanelsRequest = Field(default_factory=lambda: MangaV2PanelsRequest())
    compose: MangaV2ComposeRequest = Field(default_factory=lambda: MangaV2ComposeRequest())
    # 1コマを描き直す回数の上限
    max_redraws: int = Field(2, ge=1, le=3)


class PanelRedrawStateRequest(BaseModel):
    """描き直しで作る人物の状態。match_tags に当たる動作のタグの人物を、コマを描いたあとに redraw_tags で描き直す。"""

    name: str = Field(min_length=1, max_length=100)
    match_tags: str = Field(min_length=1, max_length=500)
    redraw_tags: str = Field(min_length=1, max_length=500)
    # 描き直しに入れる、その人物の見た目(none: 入れない / outfit: 服装だけ / full: 容姿・体型・服装)
    look: Literal["none", "outfit", "full"] = "none"


class MangaV2RedrawStatesRequest(BaseModel):
    """物語のコマのうち、状態(透明 など)の人物がいるコマを描き直して、ページに合成し直す。"""

    panels: MangaV2PanelsRequest = Field(default_factory=lambda: MangaV2PanelsRequest())
    compose: MangaV2ComposeRequest = Field(default_factory=lambda: MangaV2ComposeRequest())


class MangaV2MakeRequest(BaseModel):
    """
    まだ絵の無いコマを生成してから、ページに合成するまでを1つのジョブで行う(漫画ドラフトの「漫画にする」)。
    画面を閉じたり開き直したりしても、サーバー側で最後まで進む。
    """

    panels: MangaV2PanelsRequest = MangaV2PanelsRequest()
    compose: MangaV2ComposeRequest = MangaV2ComposeRequest()
    # コマを生成したあとに絵を確かめて、外れたコマを描き直す(MangaV2CheckRequest と同じ)
    check_panels: bool = False
    max_redraws: int = Field(2, ge=1, le=3)
    # 状態(透明 など)の人物がいるコマを、その人物の範囲だけ描き直す
    redraw_states: bool = True


class StoryAutoMangaRequest(BaseModel):
    """
    取り込んだ物語を続けて漫画にする(シーン分割 → 登場人物をそろえる・参照画像 → コマの生成と合成)。
    長い物語でも生成が増えすぎないよう、既定では冒頭の 12 シーンだけを漫画にする。
    """

    split: StorySplitRequest = Field(default_factory=lambda: StorySplitRequest(max_scenes=12, volume_max_scenes=0))
    make: MangaV2MakeRequest = MangaV2MakeRequest()
    # 参照画像の無いキャラに、キャラシートから自動で参照画像を作る
    references: bool = True
    # コマの生成でキャラ参照を使うか(1コマ +5 Anlas。V4.5 で生成する)。None / True なら参照画像のあるキャラが
    # いるとき使う、False なら使わない(見た目はキャラシートと基準シードでそろえる)
    use_character_reference: Optional[bool] = None


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
    # pages と同じ順の、動くページのアニメ WebP(動くコマが無いページは None)
    animated_pages: list[Optional[str]] = Field(default_factory=list)


class MangaV2UgoiraLink(BaseModel):
    """シーンのコマを動かすうごイラ。project_id が None なら動かさない(静止画に戻す)。"""

    scene_id: int
    project_id: Optional[int] = None


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


# ---- 物語の編集画面(コマ割り・セリフ・タグをまとめて直し、上書き保存/別名保存する) ----


class StoryEditLine(BaseModel):
    """吹き出し1つのセリフ。speaker_id はシーンの登場キャラ(null は話し手なし)。（…）で囲むと心の声。"""

    text: str = Field(max_length=500)
    speaker_id: Optional[int] = None


class StoryEditCharacter(BaseModel):
    """シーンの登場キャラ。outfit_tags は空ならキャラシートのふだんの服装、state_id は状態(null はふだん)。"""

    id: int
    action_tags: str = ""
    outfit_tags: str = ""
    state_id: Optional[int] = None


class StoryEditScene(BaseModel):
    """1コマ(シーン)。id が null なら新しく足したシーン。"""

    id: Optional[int] = None
    title: Optional[str] = Field(None, max_length=200)
    text: str = Field("", max_length=20000)
    prompt_tags: str = Field("", max_length=4000)
    narration: Optional[str] = Field(None, max_length=1000)
    sfx: Optional[list[str]] = None
    # null なら本文の「」から取り出す(話し手も推定する)。並びを送ると、それを吹き出しにする
    dialogue: Optional[list[StoryEditLine]] = None
    # custom_pages のときだけ使う。前のシーンより小さい番号は前のシーンと同じページになる
    page_index: int = Field(0, ge=0)
    characters: list[StoryEditCharacter] = Field(default_factory=list, max_length=12)


class StoryEditRequest(BaseModel):
    """編集画面の内容。scenes の並びがコマの順番になる(並べ替え・追加・削除をまとめて反映する)。"""

    title: Optional[str] = Field(None, max_length=200)
    panels_per_page: int = Field(4, ge=1, le=12)
    # True ならシーンごとの page_index でページを区切る。False なら panels_per_page ずつ
    custom_pages: bool = False
    scenes: list[StoryEditScene] = Field(min_length=1, max_length=500)


class StorySaveAsRequest(StoryEditRequest):
    """別名保存。title が新しい物語の題名になる。"""

    title: str = Field(min_length=1, max_length=200)
