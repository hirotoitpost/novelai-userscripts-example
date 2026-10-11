"""
novelai-sdk の食い違いを、このアプリの側で埋める(SDK 自体は書き換えない)。

UC プリセット「なし」: SDK は uc_preset="none" を受け付け、ネガティブプロンプトには何も足さず、API へは
ucPreset=4 を送ろうとする。ところが API の型(types/api/image.py の ImageParameters.ucPreset)は 0〜3 までしか
通さないので、「なし」を選ぶとリクエストを組み立てるところで検証エラーになる(novelai-sdk 0.8.1 と 0.14.1 で確認)。
NovelAI の画面は V4.5 で「なし」を 4 として送るので、上限の検証だけを外す。

「なし」は、NovelAI で生成した画像から設定を読み込んだときに使う(埋め込みのネガティブプロンプトは UC プリセットを
足した後の最終形なので、もう一度足さないため)。
"""

from __future__ import annotations

_done = False


def allow_uc_preset_none() -> None:
    """ImageParameters.ucPreset の上限(3)の検証を外す。何度呼んでも1回だけ行う。"""
    global _done
    if _done:
        return
    from novelai.types.api.image import ImageParameters

    # ストリーミング用の型(ImageStreamParameters)など、継承した型も同じ検証を持っている
    pending = [ImageParameters]
    while pending:
        model = pending.pop()
        pending.extend(model.__subclasses__())
        field = model.model_fields.get("ucPreset")
        if field is not None and any(type(m).__name__ == "Le" for m in field.metadata):
            field.metadata = [m for m in field.metadata if type(m).__name__ != "Le"]
            model.model_rebuild(force=True)
    _done = True
