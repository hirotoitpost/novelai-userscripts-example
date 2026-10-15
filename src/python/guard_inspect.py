"""
今あるデータの点検: 今のコンテンツガードの設定(未成年キャラの安全対策)で、キャラ・物語・画像を見直す。

生成の前に止める仕組み(minor_guard)は、これから作るものにしか効かない。前に作ったもの・取り込んだものに、今の設定で
止まるはずのものが残っていないかを一覧にする。点検はデータを変えない(画像をごみ箱へ移すのは、画面で選んでから)。

- キャラ: 年齢区分と、未成年の手がかり(キャラシート・メモ・出ている物語の本文)。成人と定義しているのに手がかりがある、
  未定義なのに手がかりがある、を見つける
- 物語: 今の設定なら止める・全年齢にする場面と、その場面に残っているコマ・挿絵。本文に未成年を示す語があるのに
  性的なタグの付いた場面がある物語(成人向けのタグ付けをした物語)
- 画像: 生成履歴・データセットの画像で、プロンプト(キャプション)が性的で未成年を思わせるタグがあるもの。
  データセットは、成人と定義していないキャラの性的なキャプションも入れる
判定モデル(画像そのもの)は使わない(全部の画像を読むと時間がかかるため)。
"""

from __future__ import annotations

from typing import Any

from . import content_guard, minor_guard
from .db import get_connection, get_story, list_characters, list_manga_panels


def _image(item: Any, reasons: list[str]) -> dict[str, Any]:
    return {
        "key": item.key, "source": item.source, "path": item.path, "label": item.label or item.story_title or "",
        "story_id": item.story_id, "prompt": (item.prompt or "")[:200], "reasons": reasons,
    }


def inspect() -> dict[str, Any]:
    from .routes.library import _gallery_images

    conn = get_connection()
    try:
        characters = list_characters(conn)
        story_ids = [r["id"] for r in conn.execute("SELECT id FROM stories ORDER BY id")]
        stories = [s for s in (get_story(conn, sid) for sid in story_ids) if s]
        panels = {sid: {p["scene_id"]: p for p in list_manga_panels(conn, sid)} for sid in story_ids}
        pages = {
            sid: [dict(r) for r in conn.execute("SELECT id, page_index, scene_ids FROM manga_pages WHERE story_id = ?", (sid,))]
            for sid in story_ids
        }
    finally:
        conn.close()

    # ---- 物語 ----
    story_cues: dict[int, list[str]] = {}
    story_rows = []
    blocked_keys: set[str] = set()
    minor_keys: set[str] = set()
    for story in stories:
        text = minor_guard.story_text(story)
        title = str(story.get("title") or story.get("premise") or f"物語{story['id']}").splitlines()[0][:60]
        scenes = []
        sexual_scenes = 0
        for scene in story["scenes"]:
            members = scene.get("characters") or []
            for member in members:
                cues = [c for c in minor_guard.minor_cues(member, text) if c.startswith("本文")]
                if cues:
                    story_cues.setdefault(member["id"], []).extend(f"物語{story['id']}「{title}」の{c}" for c in cues[:2])
            if minor_guard.is_sexual_scene(scene.get("draft_prompt_tags") or "", members):
                sexual_scenes += 1
            decision = minor_guard.decide(scene.get("draft_prompt_tags") or "", members, text)
            if decision.action == minor_guard.OK:
                continue
            images = []
            panel = panels[story["id"]].get(scene["id"])
            if panel:
                images.append({"key": f"panel:{panel['id']}", "path": panel["image_path"]})
            for page in pages[story["id"]]:
                if scene["id"] in (minor_guard_json(page["scene_ids"])):
                    images.append({"key": f"illustration:{page['id']}", "path": None})
            # 未成年がいるから止める(minor)か、年齢区分が未定義だから扱いの設定に従う(undefined)か
            kind = "minor" if decision.reasons and decision.reasons[0].startswith("性的な場面に未成年") else "undefined"
            if decision.action == minor_guard.BLOCK:
                blocked_keys.update(i["key"] for i in images)
                if kind == "minor":
                    minor_keys.update(i["key"] for i in images)
            scenes.append({
                "scene_index": scene["scene_index"], "title": scene.get("draft_title") or "", "action": decision.action,
                "kind": kind, "reasons": decision.reasons, "images": images,
            })
        words = minor_guard.minor_words(text)
        adult_tagging = bool(words) and sexual_scenes > 0
        if scenes or adult_tagging:
            story_rows.append({
                "id": story["id"], "title": title, "scenes": scenes, "minor_words": words[:8],
                "sexual_scenes": sexual_scenes, "adult_tagging_with_minor_words": adult_tagging,
            })

    # ---- キャラ ----
    character_rows = []
    for character in characters:
        age = minor_guard.character_age(character)
        cues = [r for r in age.reasons if r != "キャラシートで未成年と定義" and r != "キャラシートで年齢区分が未定義"]
        cues += story_cues.get(character["id"], [])[:4]
        defined = "minor" if character.get("is_minor") else "adult" if character.get("is_adult") else "undefined"
        problem = None
        if defined == "adult" and cues:
            problem = "成人と定義しているが、未成年の手がかりがある(未成年として扱われる)"
        elif defined == "undefined" and cues:
            problem = "未定義だが、未成年の手がかりがある(未成年と定義するのがよい)"
        if problem or defined == "minor":
            character_rows.append({
                "id": character["id"], "name": character["name"], "defined": defined,
                "state": "minor" if cues or defined == "minor" else age.state, "problem": problem, "cues": cues[:6],
            })

    # ---- 画像(生成履歴・データセット。コマ・挿絵は物語の場面で見る) ----
    ages = {c["name"]: c for c in characters}
    image_rows = []
    for item in _gallery_images():
        if item.source in ("panel", "illustration"):
            continue
        decision = minor_guard.image_decision(item.prompt or "", [])
        reasons = list(decision.reasons)
        if item.source == "dataset" and not reasons and content_guard.is_sexual(item.prompt or ""):
            name = (item.label or "").split(" / ")[0]
            character = ages.get(name)
            if character is not None and minor_guard.character_age(character).state != minor_guard.ADULT:
                reasons.append(f"成人と定義していないキャラ({name})のデータセットに、性的なキャプション")
        if reasons:
            image_rows.append(_image(item, reasons))

    return {
        "settings": {"undefined_age": content_guard.get("safety.undefined_age")},
        "summary": {
            "characters": len([c for c in character_rows if c["problem"]]),
            "minor_characters": len([c for c in character_rows if c["defined"] == "minor"]),
            "stories": len(story_rows),
            "blocked_scene_images": len(blocked_keys),
            "minor_scene_images": len(minor_keys),
            "minor_scenes": sum(1 for st in story_rows for sc in st["scenes"] if sc["kind"] == "minor"),
            "undefined_scenes": sum(1 for st in story_rows for sc in st["scenes"] if sc["kind"] == "undefined"),
            "adult_tagging_with_minor_words": sum(1 for st in story_rows if st["adult_tagging_with_minor_words"]),
            "images": len(image_rows),
        },
        "characters": character_rows,
        "stories": story_rows,
        "images": image_rows,
    }


def minor_guard_json(raw: str | None) -> list[int]:
    import json

    try:
        value = json.loads(raw or "[]")
    except ValueError:
        return []
    return [v for v in value if isinstance(v, int)]
