"""コマ1つ分の絵を NovelAI に描かせるためのプロンプト。"""

from __future__ import annotations

_QUALITY_NEGATIVE = (
    "lowres, artistic error, scan artifacts, worst quality, bad quality, jpeg artifacts, "
    "very displeasing, watermark, signature"
)
# コマ割りと吹き出しはこちらで描くので、絵に含まれると二重になる。ユーザーが
# ネガティブを指定した場合も必ず足す。
# 実機で「あいさつ・感謝」のような場面に看板や張り紙風の偽の文字が描かれたので、文字を連想させる語も入れる。
_NO_TEXT_NEGATIVE = (
    ", text, english text, japanese text, speech bubble, comic, multiple views, panels, border, frame"
    ", typography, letters, writing, signage, sign, title, logo, poster, caption, watermark text"
)
_MONOCHROME_NEGATIVE = ", sepia, colored, watercolor"


# 性的な場面のネガティブ。未成年に見える絵にならないようにする
_ADULT_SAFETY_NEGATIVE = ", child, loli, shota, young, petite, flat chest, school uniform, student"


def is_sexual(tags: str) -> bool:
    from ..routes.story import _SEXUAL_HINT  # 循環 import を避けて遅延で読む

    return bool(_SEXUAL_HINT.search(tags))


def build_panel_prompt(scene_tags: str, *, color: bool, complexity: str | None) -> str:
    from ..routes.story import sanitize_scene_tags

    parts = ["manga style" if color else "manga style, monochrome, greyscale, screentone"]
    tags = sanitize_scene_tags(scene_tags.strip().strip(","), adult=False)
    if tags:
        parts.append(tags)
    if complexity:
        parts.append(f"{complexity} complexity")
    parts.append("very aesthetic, masterpiece")
    return ", ".join(parts)


def build_panel_negative(custom: str | None, *, color: bool, sexual: bool = False) -> str:
    from .. import app_settings

    # 既定の品質のネガティブは、開発管理者のページで変えられる(manga.quality_negative)
    quality = custom.strip() if custom and custom.strip() else str(app_settings.get("manga.quality_negative"))
    negative = quality + _NO_TEXT_NEGATIVE
    if not color:
        negative += _MONOCHROME_NEGATIVE
    if sexual:
        negative += _ADULT_SAFETY_NEGATIVE
    return negative
