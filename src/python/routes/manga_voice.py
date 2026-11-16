"""
漫画の読み上げ(/api/manga-voice)。VOICEVOX でセリフ・ナレーションを読む台本と、声の割り当て。

- 台本: シーンの順に、ナレーション → セリフ(話し手の声)→(選べば)描き文字。合成したページの吹き出し(要素の key)と
  対応させるため、各項目に scene_id と要素の種類・番号(narration:0 / bubble:N / sfx:N)を付ける
- コマにいない人のセリフは、合成でナレーションの枠に入る(名前「…」)。読むときは話し手の声で読む
- 声: キャラシートの声(全部の物語で共通)。決めていなければキャラごとの既定の声。ナレーションは物語ごとに選べる
- 心の声(（…）)は、同じ話者のささやき系のスタイルがあればそれで、無ければ少しゆっくり小さめに読む
- 話し手の分からないセリフは、コマのキャラが1人ならその人、そうでなければナレーションの声で読む
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import voice
from ..db import (
    get_character,
    get_connection,
    get_story_narrator,
    list_characters,
    list_story_scenes,
    set_character_voice,
    set_story_narrator,
)
from ..manga_v2.compose import _MAX_BUBBLES
from .manga_v2 import _edited_lines, _lettering, _offpanel_lines, _scene_speakers

router = APIRouter(prefix="/api/manga-voice", tags=["manga-voice"])

_QUOTES = re.compile(r"^[「『（(]+|[」』）)]+$")


def _is_thought(line: str) -> bool:
    return line.startswith(("（", "("))


def _read_text(line: str) -> str:
    """読む文: かっこを外し、「……」は間(、)にする。"""
    text = _QUOTES.sub("", line.strip())
    text = re.sub(r"[「」『』]", "", text)
    text = re.sub(r"[…‥]{2,}", "、", text)
    # 文頭・文末の間(、)は読まない
    text = text.strip(" 　、")
    return text or line.strip()


def _voice_of(character: dict[str, Any], voices: list[dict[str, Any]]) -> dict[str, Any]:
    speaker = character.get("voice_speaker")
    return {
        "speaker": int(speaker) if speaker is not None else voice.default_voice(character, voices),
        "speed": float(character.get("voice_speed") or 1.0),
        "pitch": float(character.get("voice_pitch") or 0.0),
        "assigned": speaker is not None,
    }


def build_readout(story_id: int, *, sfx: bool, voices: list[dict[str, Any]]) -> dict[str, Any]:
    """読み上げの台本(項目の並び)・登場キャラの声・ナレーションの声・クレジット。"""
    conn = get_connection()
    try:
        scenes = list_story_scenes(conn, story_id)
        everyone = {c["id"]: c for c in list_characters(conn)}
        chosen_narrator = get_story_narrator(conn, story_id)
    finally:
        conn.close()
    if not scenes:
        raise HTTPException(status_code=404, detail="story not found")
    narrator = int(chosen_narrator) if chosen_narrator is not None else voice.default_narrator(voices)
    narrator_voice = {"speaker": narrator, "speed": 1.0, "pitch": 0.0}
    items: list[dict[str, Any]] = []
    cast_ids: list[int] = []

    def character_voice(cid: int | None, thought: bool) -> dict[str, Any]:
        if cid is None or cid not in everyone:
            base = dict(narrator_voice)
        else:
            base = _voice_of(everyone[cid], voices)
        result = {"speaker": base["speaker"], "speed": base["speed"], "pitch": base["pitch"], "volume": 1.0}
        if thought:
            whisper = voice.whisper_style(result["speaker"], voices)
            if whisper is not None:
                result["speaker"] = whisper
            else:
                result["speed"] = round(result["speed"] * 0.92, 2)
                result["volume"] = 0.8
        return result

    for scene in scenes:
        sid = scene["id"]
        present = [c["id"] for c in scene.get("characters") or []]
        for cid in present:
            if cid not in cast_ids:
                cast_ids.append(cid)
        base = {"scene_id": sid, "scene_index": scene["scene_index"]}
        narration = (scene.get("narration") or "").strip()
        if narration:
            items.append({**base, "kind": "narration", "element": "narration:0", "text": narration,
                          "read": _read_text(narration), "character_id": None, "character_name": None,
                          **character_voice(None, False)})
        lines, sfx_words = _lettering(scene)
        speakers, _ = _scene_speakers(scene)
        offpanel = dict(_offpanel_lines(scene))
        edited = _edited_lines(scene) if offpanel else []
        bubble = 0
        for index, line in enumerate(lines):
            if index in offpanel:
                cid = edited[index]["speaker_id"]
                element = "narration:0"
                if cid not in cast_ids:
                    cast_ids.append(cid)
            else:
                cid = speakers[index] if speakers and index < len(speakers) else None
                if cid is None and len(present) == 1:
                    cid = present[0]
                element = f"bubble:{min(bubble, _MAX_BUBBLES - 1)}"
                bubble += 1
            thought = _is_thought(line)
            items.append({**base, "kind": "thought" if thought else "line", "element": element, "text": line,
                          "read": _read_text(line), "character_id": cid,
                          "character_name": everyone[cid]["name"] if cid in everyone else None,
                          **character_voice(cid, thought)})
        if sfx:
            for index, word in enumerate(sfx_words):
                items.append({**base, "kind": "sfx", "element": f"sfx:{index}", "text": word, "read": _read_text(word),
                              "character_id": None, "character_name": None, **character_voice(None, False)})

    cast = [
        {"id": cid, "name": everyone[cid]["name"], **_voice_of(everyone[cid], voices),
         "speaker_name": voice.speaker_name(_voice_of(everyone[cid], voices)["speaker"], voices)}
        for cid in cast_ids if cid in everyone
    ]
    used = {item["speaker"] for item in items}
    names = sorted({name for sid in used if (name := voice.speaker_name(sid, voices))})
    return {
        "items": items,
        "cast": cast,
        "narrator": {"speaker": narrator, "assigned": chosen_narrator is not None,
                     "speaker_name": voice.speaker_name(narrator, voices)},
        "credits": [f"VOICEVOX:{name}" for name in names],
        "engine_ready": bool(voices),
    }


@router.get("/{story_id}")
async def get_readout(story_id: int, sfx: bool = False) -> dict[str, Any]:
    """読み上げの台本。sfx=true なら描き文字(効果音)もナレーションの声で読む。"""
    voices = await asyncio.to_thread(voice.safe_speakers)
    return await asyncio.to_thread(build_readout, story_id, sfx=sfx, voices=voices)


class NarratorRequest(BaseModel):
    # VOICEVOX のスタイル ID(null で既定の声)
    speaker: Optional[int] = Field(None, ge=0, le=10000)


@router.put("/{story_id}/narrator")
async def put_narrator(story_id: int, req: NarratorRequest) -> dict[str, Any]:
    conn = get_connection()
    try:
        set_story_narrator(conn, story_id, req.speaker)
    finally:
        conn.close()
    return {"speaker": req.speaker}


class CharacterVoiceRequest(BaseModel):
    # null で既定の声に戻す
    speaker: Optional[int] = Field(None, ge=0, le=10000)
    speed: Optional[float] = Field(None, ge=0.5, le=2.0)
    pitch: Optional[float] = Field(None, ge=-0.15, le=0.15)


@router.put("/characters/{character_id}")
async def put_character_voice(character_id: int, req: CharacterVoiceRequest) -> dict[str, Any]:
    """キャラの読み上げの声(キャラシートに入れる。全部の物語で共通)。"""
    conn = get_connection()
    try:
        if get_character(conn, character_id) is None:
            raise HTTPException(status_code=404, detail="character not found")
        set_character_voice(conn, character_id, req.speaker, req.speed, req.pitch)
    finally:
        conn.close()
    return {"character_id": character_id, "speaker": req.speaker, "speed": req.speed, "pitch": req.pitch}
