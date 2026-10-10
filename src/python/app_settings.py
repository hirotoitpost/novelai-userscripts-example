"""
アプリの既定値(開発管理者のページで見て、変えられるもの)。

既定値はコードに書いてあり(下の SETTINGS)、変えた値だけを DB(app_settings)に持つ。「既定に戻す」は DB の値を消す。
使う側は get(key) で読む(変更は次の呼び出しから効く。再起動は要らない)。

ここに載せるのは、変えても安全なものだけ。コンテンツガードの基本(未成年を示すタグなどの禁止)や、全年齢の
ネガティブは載せない。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from . import db
from .db import get_connection


@dataclass(frozen=True)
class Setting:
    key: str
    group: str
    label: str
    description: str
    # "text"(タグなどの文字列)| "int" | "float"
    kind: str
    default: Any
    minimum: float | None = None
    maximum: float | None = None


SETTINGS: tuple[Setting, ...] = (
    Setting(
        "reference.candidate_tags",
        "キャラの参照画像",
        "候補の構図のタグ",
        "キャラシートから参照画像の候補を作るときの、構図・背景のタグ(人物の容姿はキャラシートから入る)。",
        "text",
        "solo, cowboy shot, standing, looking at viewer, smile, simple background, white background",
    ),
    Setting(
        "reference.auto_count",
        "キャラの参照画像",
        "自動で選ぶときの候補の枚数",
        "「登場人物をそろえる」「似た漫画を作る」で、参照画像を自動で決めるときに生成する候補の枚数。",
        "int",
        4,
        1,
        8,
    ),
    Setting(
        "manga.quality_negative",
        "漫画のコマ",
        "コマのネガティブ(品質)",
        "コマの生成で、ネガティブを指定しなかったときに使う品質のネガティブ(文字を消す分などは別に足される)。",
        "text",
        "lowres, artistic error, scan artifacts, worst quality, bad quality, jpeg artifacts, "
        "very displeasing, watermark, signature",
    ),
    Setting(
        "sheet.default_negative",
        "キャラシート・逆引き",
        "基本のネガティブ",
        "キャラ別データセットの生成と、リバースプロンプト(推定)の結果に付けるネガティブ。",
        "text",
        "lowres, worst quality, low quality, blurry, bad anatomy, bad hands, "
        "extra fingers, missing fingers, text, watermark",
    ),
    Setting(
        "reverse.general_threshold",
        "キャラシート・逆引き",
        "逆引き: 一般タグのしきい値",
        "リバースプロンプトで、この確率以上のタグをプロンプトに入れる(下げるとタグが増える)。",
        "float",
        0.5,
        0.2,
        0.9,
    ),
    Setting(
        "reverse.character_threshold",
        "キャラシート・逆引き",
        "逆引き: キャラ名のしきい値",
        "リバースプロンプトで、この確率以上のキャラ名のタグを入れる(誤りが目立つので高め)。",
        "float",
        0.85,
        0.5,
        0.99,
    ),
)

_BY_KEY = {s.key: s for s in SETTINGS}
# (DB の場所, 読み込んだ値)。DB の場所が変わったら(テストなど)読み直す
_cache: tuple[Any, dict[str, Any]] | None = None


def _load() -> dict[str, Any]:
    global _cache
    if _cache is None or _cache[0] != db._DB_PATH:
        conn = get_connection()
        try:
            rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
        finally:
            conn.close()
        values = {}
        for row in rows:
            try:
                values[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                continue
        _cache = (db._DB_PATH, values)
    return _cache[1]


def reset_cache() -> None:
    """DB を差し替えたとき(テストなど)に、読み込んだ値を捨てる。"""
    global _cache
    _cache = None


def get(key: str) -> Any:
    """今の値(変えていなければコードの既定値)。"""
    setting = _BY_KEY[key]
    return _load().get(key, setting.default)


def coerce(setting: Setting, value: Any) -> Any:
    """画面から来た値を、その設定の型と範囲に合わせる。合わなければ ValueError。"""
    if setting.kind == "text":
        text = str(value).strip()
        if not text:
            raise ValueError("空にはできません(既定に戻すなら「既定に戻す」)")
        if len(text) > 2000:
            raise ValueError("長すぎます(2000文字まで)")
        return text
    number = float(value)
    if setting.minimum is not None and number < setting.minimum:
        raise ValueError(f"{setting.minimum} 以上にしてください")
    if setting.maximum is not None and number > setting.maximum:
        raise ValueError(f"{setting.maximum} 以下にしてください")
    return int(number) if setting.kind == "int" else number


def set_value(key: str, value: Any | None) -> Any:
    """値を変える(None なら既定に戻す)。変えた後の値を返す。"""
    setting = _BY_KEY.get(key)
    if setting is None:
        raise KeyError(key)
    conn = get_connection()
    try:
        if value is None:
            conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
        else:
            stored = json.dumps(coerce(setting, value), ensure_ascii=False)
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, stored),
            )
        conn.commit()
    finally:
        conn.close()
    reset_cache()
    return get(key)


def describe() -> list[dict[str, Any]]:
    """画面に出す一覧(既定値・今の値・変えてあるか)。"""
    values = _load()
    return [
        {
            "key": s.key,
            "group": s.group,
            "label": s.label,
            "description": s.description,
            "kind": s.kind,
            "default": s.default,
            "value": values.get(s.key, s.default),
            "overridden": s.key in values,
            "minimum": s.minimum,
            "maximum": s.maximum,
        }
        for s in SETTINGS
    ]
