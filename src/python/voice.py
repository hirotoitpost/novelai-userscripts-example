"""
読み上げ(VOICEVOX)。作業アシスタントの案内やチャットの答えを声にする。

- VOICEVOX のエンジン(既定 http://127.0.0.1:50021。.env の VOICEVOX_URL で変えられる)に、
  audio_query(読み方と抑揚を作る)→ synthesis(wav を作る)の順で頼む(../VibeCoding の voicevox_batch_generator.py と同じ)
- 長い文は時間がかかる(短い1文でも数秒)ので、画面が文ごとに分けて頼み、最初の文から流す
- 同じ文・同じ声は覚えておいて使い回す(案内は同じ文をくり返し読むことが多い)
- 話者の声を使うときは「VOICEVOX:(キャラ名)」の表記が要る(各キャラの利用規約)。画面に出す
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections import OrderedDict
from typing import Any

import httpx

DEFAULT_URL = "http://127.0.0.1:50021"
MAX_CHARS = 300
_CACHE_SIZE = 64
_cache: OrderedDict[tuple[str, int, float, float, float], bytes] = OrderedDict()
_speakers: tuple[float, list[dict[str, Any]]] | None = None
_lock = threading.Lock()


class VoiceUnavailable(RuntimeError):
    """VOICEVOX のエンジンにつながらない。"""


def engine_url() -> str:
    return (os.environ.get("VOICEVOX_URL") or DEFAULT_URL).rstrip("/")


def _client() -> httpx.Client:
    return httpx.Client(base_url=engine_url(), timeout=httpx.Timeout(60.0, connect=3.0))


def status() -> dict[str, Any]:
    try:
        with _client() as client:
            res = client.get("/version")
            res.raise_for_status()
            return {"ready": True, "version": res.json(), "url": engine_url()}
    except (httpx.HTTPError, ValueError):
        return {"ready": False, "version": None, "url": engine_url()}


def speakers() -> list[dict[str, Any]]:
    """話者と声の種類(id・キャラ名・声の名前)。10分覚えておく。"""
    global _speakers
    with _lock:
        if _speakers and time.monotonic() - _speakers[0] < 600:
            return _speakers[1]
    try:
        with _client() as client:
            res = client.get("/speakers")
            res.raise_for_status()
            data = res.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise VoiceUnavailable(f"VOICEVOX につながりません({engine_url()}): {exc}") from exc
    flat = [
        {"id": style["id"], "name": speaker["name"], "style": style["name"]}
        for speaker in data
        for style in speaker.get("styles", [])
        if style.get("type", "talk") == "talk"
    ]
    with _lock:
        _speakers = (time.monotonic(), flat)
    return flat


def clean_text(text: str) -> str:
    """Markdown の記号を読ませない形にする(リンクは文字だけ、太字・コード・見出し・箇条書きの記号を外す)。"""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)  # 画像・動画
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # リンク(文字が空のものは消える)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"(?m)^\s*(#{1,6}|>|[-*]|\d+\.)\s+", "", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


def split_sentences(text: str, limit: int = 120) -> list[str]:
    """文ごとに分ける(。!?と改行)。長すぎる文は読点で、それでも長ければ limit で切る。"""
    parts: list[str] = []
    for sentence in re.split(r"(?<=[。！？!?])|\n", clean_text(text)):
        sentence = sentence.strip()
        while len(sentence) > limit:
            cut = sentence.rfind("、", 0, limit)
            cut = cut + 1 if cut > limit // 3 else limit
            parts.append(sentence[:cut])
            sentence = sentence[cut:].strip()
        if sentence:
            parts.append(sentence)
    return parts


def synthesize(text: str, speaker: int, speed: float = 1.0, pitch: float = 0.0, volume: float = 1.0) -> bytes:
    """1つの文を wav にする。pitch は声の高さ(-0.15〜0.15。0 でそのまま)、volume は音量(0.5〜1.5)。"""
    text = clean_text(text)[:MAX_CHARS]
    if not text:
        raise ValueError("読む文がありません。")
    key = (text, speaker, round(speed, 2), round(pitch, 3), round(volume, 2))
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    try:
        with _client() as client:
            query = client.post("/audio_query", params={"text": text, "speaker": speaker})
            query.raise_for_status()
            body = query.json()
            body["speedScale"] = max(0.5, min(2.0, speed))
            body["pitchScale"] = max(-0.15, min(0.15, pitch))
            body["volumeScale"] = max(0.5, min(1.5, volume))
            audio = client.post("/synthesis", params={"speaker": speaker}, json=body)
            audio.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ValueError(f"VOICEVOX が読めませんでした({exc.response.status_code})。話者を変えてみてください。") from exc
    except httpx.HTTPError as exc:
        raise VoiceUnavailable(f"VOICEVOX につながりません({engine_url()}): {exc}") from exc
    with _lock:
        _cache[key] = audio.content
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return audio.content


# ---- 漫画の読み上げの声 ----
#
# キャラに声を決めていなければ、キャラごとに既定の声を当てる(女性・男性の話者の候補から、キャラの ID で順に選ぶ)。
# 心の声(（…）)は、同じ話者に「ささやき」「ヒソヒソ」のスタイルがあればそれで読む(無ければ少しゆっくり小さめ)。

FEMALE_VOICES = ("春日部つむぎ", "四国めたん", "冥鳴ひまり", "もち子さん", "雨晴はう", "九州そら", "WhiteCUL", "櫻歌ミコ", "小夜/SAYO")
MALE_VOICES = ("玄野武宏", "白上虎太郎", "青山龍星", "剣崎雌雄", "ちび式じい")
NARRATOR_VOICES = ("冥鳴ひまり", "青山龍星", "四国めたん")
WHISPER_STYLES = ("ささやき", "ヒソヒソ", "囁き")
FALLBACK_SPEAKER = 8


def _normal_style(voices: list[dict[str, Any]], name: str) -> int | None:
    """話者の名前の、ふつう(ノーマル)のスタイル ID(無ければ最初のスタイル)。"""
    styles = [v for v in voices if v["name"] == name]
    if not styles:
        return None
    normal = next((v for v in styles if v["style"] == "ノーマル"), styles[0])
    return int(normal["id"])


def _is_male(character: dict[str, Any]) -> bool:
    tags = {t.strip().lower() for t in str(character.get("appearance_tags") or "").split(",")}
    return bool(tags & {"1boy", "male", "male focus", "boy", "man", "old man"})


def default_voice(character: dict[str, Any], voices: list[dict[str, Any]]) -> int:
    """声を決めていないキャラの既定の声(性別の候補から、キャラの ID で順に)。"""
    pool = [sid for name in (MALE_VOICES if _is_male(character) else FEMALE_VOICES)
            if (sid := _normal_style(voices, name)) is not None]
    if not pool:
        return FALLBACK_SPEAKER
    return pool[int(character.get("id") or 0) % len(pool)]


def default_narrator(voices: list[dict[str, Any]]) -> int:
    return next((sid for name in NARRATOR_VOICES if (sid := _normal_style(voices, name)) is not None), FALLBACK_SPEAKER)


def whisper_style(speaker: int, voices: list[dict[str, Any]]) -> int | None:
    """同じ話者のささやき系のスタイル(無ければ None)。"""
    name = next((v["name"] for v in voices if v["id"] == speaker), None)
    return next((int(v["id"]) for v in voices if v["name"] == name and v["style"] in WHISPER_STYLES), None)


def speaker_name(speaker: int, voices: list[dict[str, Any]]) -> str | None:
    return next((v["name"] for v in voices if v["id"] == speaker), None)


def safe_speakers() -> list[dict[str, Any]]:
    """話者の一覧(VOICEVOX が動いていなければ空)。"""
    try:
        return speakers()
    except VoiceUnavailable:
        return []
