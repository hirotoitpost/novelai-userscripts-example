"""
下着・水着のコレクション/からかいコレクションのテンプレート(collection_templates.py)と、
好きなキャラで物語にする API(/api/collections)のテスト。NovelAI には接続しない。

実行方法:
  uv run pytest tests/test_collections.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.collection_templates import TEASE_POSES, CollectionOptions, build_collection  # noqa: E402
from python.models import ScriptedStoryRequest  # noqa: E402
from python.routes import collections as collection_routes  # noqa: E402

PAIR = [{"id": 1, "name": "しらね"}, {"id": 2, "name": "あかね"}]


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    app = FastAPI()
    app.include_router(collection_routes.router)
    with TestClient(app) as test_client:
        yield test_client


def _character(name: str, age: int | None) -> int:
    conn = db.get_connection()
    try:
        character = db.save_character(conn, name, "1girl, silver hair")
        if age is not None:
            db.update_character_sheet(conn, character["id"], {"age": age})
        return character["id"]
    finally:
        conn.close()


def test_tease_collection_gives_every_pose_to_every_character() -> None:
    script = build_collection(PAIR, CollectionOptions(kind="tease_lingerie"))
    scenes = script["scenes"]
    # 最初と最後のみんなのコマ + ポーズ × キャラ
    assert len(scenes) == 2 + len(TEASE_POSES) * 2
    solo = scenes[1:-1]
    for pose in TEASE_POSES:
        cast = [s["character_ids"][0] for s in solo if s["narration"].startswith(pose.label + "　")]
        assert cast == [1, 2], pose.key
    # 台本の形として通る(セリフの話し手はコマにいるキャラ)
    ScriptedStoryRequest(title=script["title"], scenes=scenes)
    for s in solo:
        assert all(line["speaker_id"] in s["character_ids"] for line in s["dialogue"])


def test_wedgie_only_in_wedgie_poses_and_content_negatives_everywhere() -> None:
    scenes = build_collection(PAIR, CollectionOptions(kind="tease_swimsuit"))["scenes"]
    for s in scenes:
        negative = s["negative_prompt"]
        assert "nipples" in negative and "cameltoe" in negative and "genitals" in negative
        assert "nsfw" not in negative
        wedgie_pose = "wedgie" in ",".join(s["character_actions"].values())
        assert ("wedgie" in negative) is not wedgie_pose
        assert "explicit" in s["prompt_tags"]
    # 顔なしの後ろ姿は、振り向きを打ち消し、頭は枠の外
    away = next(s for s in scenes if (s["narration"] or "").startswith("四つんばい(背面・顔なし)"))
    assert "looking back" in away["negative_prompt"] and "head out of frame" in away["character_actions"][1]
    # 脚の間の頭は、股のぞきのポーズだけ
    for s in scenes[1:-1]:
        through = (s["narration"] or "").startswith("股のぞき")
        assert ("head between legs" in s["negative_prompt"]) is not through


def test_collection_options_pick_poses_themes_body_and_style() -> None:
    script = build_collection(PAIR[:1], CollectionOptions(
        kind="tease_lingerie", poses=["squat_away", "upside_down"], body="sheet", style_tags="flat color", lines=False,
    ))
    scenes = script["scenes"]
    assert len(scenes) == 4 and all("flat color" in s["prompt_tags"] for s in scenes)
    assert all(not s["dialogue"] for s in scenes)
    assert "curvy" not in scenes[1]["character_outfits"][1]
    normal = build_collection(PAIR, CollectionOptions(kind="swimsuit", themes=["ハイレグ"]))["scenes"]
    # みんな + テーマの見出し + 2人×(正面・背面) + みんな。体つきの既定はすらっと
    assert len(normal) == 7 and normal[1]["character_ids"] == [] and "slender" in normal[2]["character_outfits"][1]
    assert "front view" in normal[2]["character_actions"][1] and "from behind" in normal[3]["character_actions"][1]
    assert normal[3]["narration"].startswith("ハイレグ・後ろ")
    # 背面のポーズは順に回る。立位体前屈は顔を見せず、脚の間の頭も打ち消す
    backs = [s for s in build_collection(PAIR[:1], CollectionOptions(kind="lingerie"))["scenes"]
             if "・後ろ" in (s["narration"] or "")]
    bend = [s for s in backs if "standing forward bend" in s["character_actions"][1]]
    assert len(bend) == 1 and "head between legs" in bend[0]["negative_prompt"] and "looking back" in bend[0]["negative_prompt"]
    assert "three-quarter view" in bend[0]["negative_prompt"] and "directly behind" in bend[0]["character_actions"][1]
    # 頭がお尻の上に出る絵を打ち消す。まとめ髪でないキャラは容姿をそのまま使う
    assert "head above hips" in bend[0]["negative_prompt"] and "character_appearances" not in bend[0]
    # 前屈・股のぞきは正方形で描く(縦長だと脚が伸びる)。ほかのコマは枠の形から自動
    assert bend[0]["panel_size"] == "square" and backs[0]["panel_size"] is None
    tease = build_collection(PAIR[:1], CollectionOptions(kind="tease_swimsuit", poses=["through_legs", "lean_lips"]))["scenes"]
    assert [s["panel_size"] for s in tease[1:-1]] == ["square", None]
    # お尻を突き出す系は全身で描く(脚・足先が切れない)
    butt = build_collection(PAIR[:1], CollectionOptions(
        kind="tease_lingerie", poses=["bent_over", "all_fours_back", "hand_butt", "wedgie_bent", "wedgie_squat"]))["scenes"]
    assert all("full body" in s["prompt_tags"] and "cowboy shot" not in s["prompt_tags"] for s in butt[1:-1])
    # 足先まで入るよう強調し、足の切れを打ち消す(しゃがみはもともと全身)
    assert all("1.4::full body::" in s["prompt_tags"] and "feet out of frame" in s["negative_prompt"] for s in butt[1:-2])
    # キャラごとに違う衣装
    assert normal[2]["character_outfits"][1] != normal[4]["character_outfits"][2]
    with pytest.raises(ValueError):
        build_collection(PAIR, CollectionOptions(kind="tease_lingerie", poses=["nope"]))


def test_forward_bend_lets_tied_hair_down_for_that_panel_only() -> None:
    akane = {"id": 3, "name": "あかね", "appearance_tags": "1girl, silver hair, very long hair, high ponytail, yellow eyes"}
    scenes = build_collection([akane], CollectionOptions(kind="swimsuit"))["scenes"]
    bend = next(s for s in scenes if "standing forward bend" in ",".join(s["character_actions"].values()))
    assert bend["character_appearances"][3] == "1girl, silver hair, very long hair, yellow eyes, hair down, hair hanging down"
    assert "hair tie" in bend["negative_prompt"]
    # ほかのコマはキャラシートの容姿のまま
    assert sum("character_appearances" in s for s in scenes) == 1
    ScriptedStoryRequest(title="x", scenes=scenes)
    # 床へ垂れる髪は長い髪のキャラだけ(ボブに付けるとロングヘアになる)
    assert "hair hanging down to floor" in bend["character_actions"][3]
    bob = {"id": 4, "name": "こはく", "appearance_tags": "1girl, white hair, short hair, bob cut"}
    bob_bend = next(s for s in build_collection([bob], CollectionOptions(kind="lingerie"))["scenes"]
                    if "standing forward bend" in ",".join(s["character_actions"].values()))
    assert "hair hanging down" not in bob_bend["character_actions"][4] and "character_appearances" not in bob_bend


def test_api_makes_a_story_only_with_adult_characters(client: TestClient) -> None:
    adult = _character("しらね", 22)
    assert client.get("/api/collections").json()["kinds"][0]["id"] == "lingerie"
    res = client.post("/api/collections", json={"kind": "lingerie", "character_ids": [adult], "themes": ["白レース"]})
    assert res.status_code == 200, res.text
    story = res.json()
    assert story["n_scenes"] == 5 and story["title"] == "しらねの下着コレクション"
    # 台本の生成サイズはシーンに入る
    conn = db.get_connection()
    try:
        sizes = [s["panel_size"] for s in db.list_story_scenes(conn, story["id"])]
    finally:
        conn.close()
    assert sizes == [None] * 5
    res = client.post("/api/collections", json={"kind": "tease_swimsuit", "character_ids": [adult], "poses": ["through_legs"]})
    conn = db.get_connection()
    try:
        sizes = [s["panel_size"] for s in db.list_story_scenes(conn, res.json()["id"])]
    finally:
        conn.close()
    assert sizes == [None, "square", None]
    # 年齢を入れていないキャラ・未成年は作れない
    unknown_age = _character("だれか", None)
    res = client.post("/api/collections", json={"kind": "swimsuit", "character_ids": [adult, unknown_age]})
    assert res.status_code == 422 and "だれか" in res.json()["detail"]
    minor = _character("こども", 12)
    assert client.post("/api/collections", json={"kind": "tease_swimsuit", "character_ids": [minor]}).status_code == 422


def solo_scenes(scenes: list[dict]) -> list[dict]:
    """最初と最後のみんなのコマ・見出しの風景のコマを除いた、一人ずつのコマ。"""
    return [s for s in scenes[1:-1] if s["character_ids"]]


def test_nude_photobook_sets_poses_and_content_line() -> None:
    from python.collection_templates import NUDE_SETS

    script = build_collection(PAIR, CollectionOptions(kind="nude"))
    scenes = script["scenes"]
    n_poses = sum(len(s.poses) for s in NUDE_SETS)
    # みんな + 場所ごとの見出し + ポーズ × 2人 + みんな
    assert len(scenes) == 2 + len(NUDE_SETS) + n_poses * 2
    assert script["title"] == "しらね・あかね ヌード写真集"
    ScriptedStoryRequest(title=script["title"], scenes=scenes)
    for s in scenes:
        negative = s["negative_prompt"]
        # 乳首・性器はどのコマも打ち消し、湯気・モザイクで隠さない。explicit は足さない
        assert all(word in negative for word in ("nipples", "pussy", "genitals", "anus", "loli", "steam", "mosaic censoring"))
        assert "explicit" not in s["prompt_tags"]
        # 参照画像の服の色が肌に移る(赤・橙・黒の一色の肌)のを打ち消す
        assert "orange skin" in negative and "black skin" in negative
    # 腹筋の割れを抑え、なめらかなお腹にする(キャラシートの体型は外せないので)
    for s in solo_scenes(scenes):
        cid = s["character_ids"][0]
        assert "1.3::abs::" in s["negative_prompt"] and "smooth stomach" in s["character_outfits"][cid]
    # 滑らかでハリのあるツヤ肌(キャラシートに shiny skin が無いキャラもマットにしない)。最後のタオルのコマも
    for s in scenes:
        for outfit in s["character_outfits"].values():
            assert "shiny skin" in outfit and "firm skin" in outfit
        assert "matte skin" in s["negative_prompt"]
    # 下半身の影を打ち消し、肌を均一に明るく。逆光・シルエットのコマは影がわざと付くので足さない
    for s in solo_scenes(scenes):
        backlit = "silhouette" in s["prompt_tags"] or "backlighting" in s["prompt_tags"]
        assert ("even lighting" in s["prompt_tags"]) is not backlit
        assert ("shaded legs" in s["negative_prompt"]) is not backlit
    assert any("silhouette" in s["prompt_tags"] for s in solo_scenes(scenes))
    # 寝室の見出しは散らかった部屋にしない
    bed_heading = next(s for s in scenes if s["narration"] == "朝のベッド")
    assert "messy bed" not in bed_heading["prompt_tags"] and "clutter" in bed_heading["negative_prompt"]
    solo = [s for s in scenes[1:-1] if s["character_ids"]]
    for s in solo:
        outfit = s["character_outfits"][s["character_ids"][0]]
        assert "completely nude" in outfit
        # 前を向くコマは featureless、後ろ姿は背中
        assert "featureless" in outfit or "bare back" in outfit or "bare shoulders" in outfit
    # 脚を開くポーズだけ spread legs を打ち消さない
    def pose(label: str) -> dict:
        return next(s for s in solo if s["character_ids"] == [1] and label in s["character_actions"][1])
    assert "spread legs" not in pose("m legs")["negative_prompt"]
    assert "spread legs" in pose("hugging own knees")["negative_prompt"]
    # 顔なしの四つんばいは振り向きを打ち消し、膝が浮く絵も打ち消す
    away = pose("back of head")
    assert "looking back" in away["negative_prompt"] and "straight legs" in away["negative_prompt"]
    # 小物(シーツ)のコマはタオル・服を打ち消さない。ほかは打ち消す
    assert "towel" not in pose("holding white sheet")["negative_prompt"]
    assert "towel" in pose("hugging own knees")["negative_prompt"]
    # バスタブは濡れた肌で、泡を打ち消す
    bath = pose("one leg raised out of water")
    assert "wet skin" in bath["character_outfits"][1] and "foam" in bath["negative_prompt"]


def test_nude_photobook_options_and_hair_down_in_bed() -> None:
    yura = {"id": 5, "name": "ゆら", "appearance_tags": "1girl, pink hair, long hair, high ponytail, blue eyes"}
    scenes = build_collection([yura], CollectionOptions(
        kind="nude", themes=["スタジオ", "朝のベッド"], poses=["m_legs", "all_fours_away", "bed_back"], lines=False))["scenes"]
    # みんな + 見出し(スタジオ) + M字・四つんばい + 見出し(ベッド) + 二度寝 + みんな
    assert len(scenes) == 7 and all(not s["dialogue"] for s in scenes)
    assert [s["narration"] for s in scenes if not s["character_ids"]] == ["スタジオ", "朝のベッド"]
    # ベッドではまとめ髪を下ろす。ほかの場所はキャラシートの容姿のまま
    bed = scenes[5]
    assert bed["character_appearances"][5] == "1girl, pink hair, long hair, blue eyes, hair down, hair hanging down, messy hair"
    assert sum("character_appearances" in s for s in scenes) == 1
    with pytest.raises(ValueError):
        build_collection([yura], CollectionOptions(kind="nude", poses=["bent_over"]))
    with pytest.raises(ValueError):
        build_collection([yura], CollectionOptions(kind="nude", themes=["スタジオ"], poses=["bed_back"]))


def test_api_makes_nude_photobook_only_with_adults(client: TestClient) -> None:
    catalog = client.get("/api/collections").json()
    assert {"id": "nude", "label": "ヌード写真集"} in catalog["kinds"]
    assert catalog["themes"]["nude"][0] == "スタジオ" and catalog["nude_poses"][0]["set"] == "スタジオ"
    adult = _character("しらね", 22)
    res = client.post("/api/collections", json={"kind": "nude", "character_ids": [adult], "themes": ["窓辺の光"]})
    assert res.status_code == 200, res.text
    assert res.json()["n_scenes"] == 2 + 1 + 3
    minor = _character("こども", 12)
    assert client.post("/api/collections", json={"kind": "nude", "character_ids": [minor]}).status_code == 422


def test_nude2_travel_photobook_has_low_angles_hips_back_and_expressions() -> None:
    from python.collection_templates import NUDE2_SETS

    script = build_collection(PAIR[:1], CollectionOptions(kind="nude2"))
    scenes = script["scenes"]
    assert script["title"] == "しらね ヌード写真集 〜旅する素肌〜"
    assert [s.name for s in NUDE2_SETS] == ["和室の朝", "森の泉", "アトリエ", "夜景のペントハウス", "夜明けの砂丘"]
    assert len(scenes) == 2 + len(NUDE2_SETS) + sum(len(s.poses) for s in NUDE2_SETS)
    ScriptedStoryRequest(title=script["title"], scenes=scenes)
    solo = solo_scenes(scenes)
    actions = [s["character_actions"][1] for s in solo]
    # 下からの構図・お尻を突き出す・がに股スクワット・I字バランス・いろいろな表情
    assert sum("from below" in s["prompt_tags"] for s in solo) >= 5
    assert sum("pushing hips back" in a for a in actions) >= 4
    assert any("sumo squat" in a for a in actions) and any("standing split" in a for a in actions)
    for face in ("embarrassed", "excited", "upturned eyes", "rolling eyes"):
        assert any(face in a for a in actions), face
    # 1冊目と同じ線引き: 乳首・性器を打ち消し、ツヤ肌。脚を開くポーズは股の赤い印・筋も打ち消す
    for s in solo:
        assert "nipples" in s["negative_prompt"] and "genitals" in s["negative_prompt"]
        assert "shiny skin" in s["character_outfits"][1]
        if "spread legs" not in s["negative_prompt"]:
            assert "cleft of venus" in s["negative_prompt"] or "pushing hips back" in s["character_actions"][1]
    # 種類ごとのポーズだけ選べる
    with pytest.raises(ValueError):
        build_collection(PAIR[:1], CollectionOptions(kind="nude2", poses=["studio_stand"]))
    one = build_collection(PAIR[:1], CollectionOptions(kind="nude2", poses=["atelier_i_balance"]))["scenes"]
    assert len(one) == 4 and one[1]["narration"] == "アトリエ"


def test_catalog_lists_nude2_sets_and_poses_by_kind(client: TestClient) -> None:
    catalog = client.get("/api/collections").json()
    assert {"id": "nude2", "label": "ヌード写真集 〜旅する素肌〜"} in catalog["kinds"]
    assert catalog["themes"]["nude2"][0] == "和室の朝"
    kinds = {p["key"]: p["kind"] for p in catalog["nude_poses"]}
    assert kinds["studio_stand"] == "nude" and kinds["washitsu_squat"] == "nude2"
    adult = _character("しらね", 22)
    res = client.post("/api/collections", json={"kind": "nude2", "character_ids": [adult], "themes": ["夜明けの砂丘"]})
    assert res.status_code == 200, res.text


def test_nude2_squat_and_balance_from_every_angle() -> None:
    keys = {
        "washitsu_squat": "from front", "washitsu_squat_front_low": "from below", "washitsu_squat_back": "from behind",
        "washitsu_squat_back_low": "from below", "washitsu_squat_side": "from side",
        "atelier_i_balance": "from side", "atelier_i_balance_front_low": "from below",
        "atelier_i_balance_back": "from behind", "atelier_i_balance_back_low": "from below",
    }
    scenes = solo_scenes(build_collection(PAIR[:1], CollectionOptions(kind="nude2", poses=list(keys)))["scenes"])
    assert len(scenes) == len(keys)
    for s, view in zip(scenes, keys.values()):
        action = s["character_actions"][1]
        assert "sumo squat" in action or "standing split" in action
        assert view in s["prompt_tags"]
        # 脚を開くポーズ: 股の赤い印・筋を打ち消し、背面は振り向かない
        assert "cleft of venus" in s["negative_prompt"] and "spread legs" not in s["negative_prompt"]
        if "facing away" in action:
            assert "looking back" in s["negative_prompt"] and "from behind" in s["prompt_tags"]
    # 背面下からは、背面かつ下から
    back_low = [s for s in scenes if "from behind" in s["prompt_tags"] and "from below" in s["prompt_tags"]]
    assert len(back_low) == 2
