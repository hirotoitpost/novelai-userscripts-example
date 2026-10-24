"""
キャラシートの素体(body_base.py と /api/lora-dataset/body-bases・.../body-base)のテスト。NovelAI は呼ばず、
生成の関数を差し替える。DB と出力先は一時フォルダに差し替える。

実行方法:
  uv run pytest tests/test_body_base.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import body_base, db  # noqa: E402
from python.client import get_client  # noqa: E402
from python.routes import library as library_routes  # noqa: E402
from python.routes import lora_dataset as lora_routes  # noqa: E402
from python.routes import story as story_routes  # noqa: E402


def _defaults() -> body_base.Options:
    """はじめの選択肢(DB に入れる前の db.DEFAULT_BODY_BASE_OPTIONS)。"""
    rows = [
        {"field": field, "value": v, "label": label, "tags": tags, "rear_tags": rear}
        for field, choices in db.DEFAULT_BODY_BASE_OPTIONS.items()
        for v, label, tags, rear in choices
    ]
    return body_base.options_map(rows)


def test_parts_follow_anatomical_regions() -> None:
    groups = {key for key, _ in body_base.GROUPS}
    assert {p.group for p in body_base.PARTS} == groups
    assert len(body_base.ITEMS) == len(body_base.PARTS)  # キーが重ならない
    assert all(p.check for p in body_base.PARTS)
    # 裸の素体では、胸・腹・骨盤・大腿の寄りを描かない
    assert {p.key for p in body_base.PARTS if not p.nude} == {"neck_shoulder", "chest", "abdomen", "pelvis", "thigh_knee"}


def test_face_tags_drop_body_and_skin() -> None:
    character = {"appearance_tags": "1girl, brown hair, large breasts, pale skin, yellow eyes"}
    assert body_base.face_tags(character, {"skin_tone": "tan"}) == "1girl, brown hair, yellow eyes"
    # 素体で肌の色を決めなければ、キャラの肌の色を残す
    assert "pale skin" in body_base.face_tags(character, {"skin_tone": ""})


def test_nude_block_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    adult = {"id": 1, "name": "栄子", "is_adult": 1, "age": 28, "appearance_tags": "1girl, adult woman"}
    assert body_base.nude_block_reason(adult, [adult]) is None
    assert body_base.nude_block_reason({**adult, "is_adult": 0}, []) is not None
    assert body_base.nude_block_reason({**adult, "age": 17}, []) is not None
    # 成人フラグだけで年齢の無いキャラには当てない
    assert "年齢" in (body_base.nude_block_reason({**adult, "age": None}, []) or "")
    assert body_base.nude_block_reason({**adult, "outfit_tags": "school uniform"}, []) is not None
    # キャラ別データセットの未成年タグ(dataset.minor_tags)が空でも、素体の未成年タグ(body_base.minor_tags)で止める
    monkeypatch.setattr(body_base, "character_minor_tags", lambda _c: [])
    assert "school uniform" in (body_base.nude_block_reason({**adult, "outfit_tags": "school uniform, cardigan"}, []) or "")
    assert body_base.nude_block_reason({**adult, "appearance_tags": "1girl, young woman"}, []) is not None
    # 成長段階に未成年の段階があれば、成人の段階でも当てない
    child = {"id": 2, "name": "5歳", "is_minor": 1, "age": 5}
    assert "5歳" in (body_base.nude_block_reason(adult, [adult, child]) or "")


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(lora_routes, "_PROJECT_ROOT", tmp_path)
    calls: list[tuple[str, str]] = []

    async def fake_generate(client, prompt, size, cfg, seed=None, inpaint=None):  # noqa: ANN001
        calls.append((prompt, cfg.negative_prompt))
        return Image.new("RGB", size, (200, 200, 200))

    monkeypatch.setattr(lora_routes, "generate_one", fake_generate)
    monkeypatch.setattr(lora_routes.asyncio, "sleep", lambda *_: _noop())
    app = FastAPI()
    app.include_router(story_routes.router)
    app.include_router(lora_routes.router)
    app.dependency_overrides[get_client] = lambda: object()
    test_client = TestClient(app)
    test_client.calls = calls  # type: ignore[attr-defined]
    return test_client


async def _noop() -> None:
    return None


def _character(client: TestClient, name: str, **sheet) -> dict:
    character = client.post(
        "/api/story/characters/new", json={"name": name, "appearance_tags": "1girl, brown hair, large breasts", "trigger_word": name}
    ).json()
    if sheet:
        conn = db.get_connection()
        try:
            db.update_character_sheet(conn, character["id"], sheet)
        finally:
            conn.close()
    return character


def _base(client: TestClient, **fields) -> dict:
    res = client.post("/api/lora-dataset/body-bases", json={"name": "素体", **fields})
    assert res.status_code == 200, res.text
    return res.json()


def test_suit_base_is_edited_assigned_and_drawn(client: TestClient, tmp_path: Path) -> None:
    character = _character(client, "test")
    base = _base(client, skin_tone="tan", build="athletic", chest="medium")
    edited = client.put(f"/api/lora-dataset/body-bases/{base['id']}", json={"name": "素体A", "skin_tone": "pale", "build": "slender"})
    assert edited.json()["skin_tone"] == "pale" and edited.json()["name"] == "素体A"

    assigned = client.put(f"/api/lora-dataset/character/{character['id']}/body-base", json={"body_base_id": base["id"]}).json()
    assert assigned["base"]["id"] == base["id"] and "pale skin" in assigned["body_tags"]

    res = client.post(f"/api/lora-dataset/character/{character['id']}/body-base", json={"keys": ["front", "chest", "hand_palm"]})
    assert res.status_code == 200
    assert res.text.count('"status": "ok"') == 3
    prompt, negative = client.calls[0]  # type: ignore[attr-defined]
    assert "brown hair" in prompt and "pale skin" in prompt and "slender" in prompt and "bodysuit" in prompt
    # キャラの体型(large breasts)は素体で置き換える
    assert "large breasts" not in prompt and "nude" not in prompt
    assert "nude" in negative  # 全年齢のネガティブ

    entries = {p["key"]: p for p in client.get(f"/api/lora-dataset/character/{character['id']}/body-base").json()["parts"]}
    assert entries["front"]["path"] == f"outputs/training_data/test/body_base/{base['id']}_front.png"
    assert entries["side"]["path"] is None and entries["chest"]["available"]
    assert client.post(f"/api/lora-dataset/character/{character['id']}/body-base", json={"keys": ["nope"]}).status_code == 422


def test_bad_values_and_sexual_extra_tags_are_refused(client: TestClient) -> None:
    assert client.post("/api/lora-dataset/body-bases", json={"name": "x", "build": "huge"}).status_code == 422
    assert client.post("/api/lora-dataset/body-bases", json={"name": "x", "extra_tags": "nipples"}).status_code == 422
    assert client.post("/api/lora-dataset/body-bases", json={"name": "x", "kind": "nude", "extra_tags": "pussy"}).status_code == 422
    _base(client)
    assert client.post("/api/lora-dataset/body-bases", json={"name": "素体"}).status_code == 409


def test_nude_base_only_for_adults(client: TestClient, tmp_path: Path) -> None:
    adult = _character(client, "eiko", is_adult=1, age=28)
    no_age = _character(client, "noage", is_adult=1)
    minor = _character(client, "yura", is_minor=1, age=17)
    base = _base(client, kind="nude", skin_tone="fair")

    assert client.put(f"/api/lora-dataset/character/{minor['id']}/body-base", json={"body_base_id": base["id"]}).status_code == 403
    assert client.put(f"/api/lora-dataset/character/{no_age['id']}/body-base", json={"body_base_id": base["id"]}).status_code == 403
    assert client.post("/api/lora-dataset/body-bases", json={"name": "y", "kind": "nude", "extra_tags": "young"}).status_code == 422
    info = client.get(f"/api/lora-dataset/character/{minor['id']}/body-base").json()
    assert info["nude_allowed"] is False and info["nude_reason"]

    assigned = client.put(f"/api/lora-dataset/character/{adult['id']}/body-base", json={"body_base_id": base["id"]}).json()
    assert assigned["nude_allowed"] is True
    assert not {p["key"]: p for p in assigned["parts"]}["chest"]["available"]
    # 胸などの寄りは描かない
    assert client.post(f"/api/lora-dataset/character/{adult['id']}/body-base", json={"keys": ["chest"]}).status_code == 422

    res = client.post(f"/api/lora-dataset/character/{adult['id']}/body-base", json={"keys": ["front"]})
    assert res.text.count('"status": "ok"') == 1
    prompt, negative = client.calls[-1]  # type: ignore[attr-defined]
    assert "barbie doll anatomy" in prompt and "adult" in prompt
    assert "nipples" in negative and "loli" in negative
    assert (tmp_path / f"outputs/training_data/eiko/body_base_r18/{base['id']}_front.png").is_file()


def test_growth_family_with_minor_stage_blocks_nude(client: TestClient) -> None:
    root = _character(client, "yura_base")
    _character(client, "yura_10", is_minor=1, age=10, growth_of=root["id"])
    grown = _character(client, "yura_22", is_adult=1, age=22, growth_of=root["id"])
    base = _base(client, kind="nude")
    res = client.put(f"/api/lora-dataset/character/{grown['id']}/body-base", json={"body_base_id": base["id"]})
    assert res.status_code == 403 and "yura_10" in res.json()["detail"]


def test_switching_a_used_base_to_nude_checks_its_characters(client: TestClient) -> None:
    minor = _character(client, "kid", is_minor=1, age=10)
    base = _base(client)
    client.put(f"/api/lora-dataset/character/{minor['id']}/body-base", json={"body_base_id": base["id"]})
    res = client.put(f"/api/lora-dataset/body-bases/{base['id']}", json={"name": "素体", "kind": "nude"})
    assert res.status_code == 403
    # 消すと、当てていたキャラは素体なしに戻る
    assert client.delete(f"/api/lora-dataset/body-bases/{base['id']}").status_code == 200
    assert client.get(f"/api/lora-dataset/character/{minor['id']}/body-base").json()["base"] is None


def test_body_base_image_is_served(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library_routes, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(library_routes, "_OUTPUTS", tmp_path / "outputs")
    client.app.include_router(library_routes.router)  # type: ignore[attr-defined]
    character = _character(client, "test")
    base = _base(client)
    client.put(f"/api/lora-dataset/character/{character['id']}/body-base", json={"body_base_id": base["id"]})
    client.post(f"/api/lora-dataset/character/{character['id']}/body-base", json={"keys": ["front"]})
    path = client.get(f"/api/lora-dataset/character/{character['id']}/body-base").json()["parts"][0]["path"]
    assert client.get("/api/library/file", params={"path": path}).status_code == 200


def test_suit_is_modest_for_minor_coded_characters(client: TestClient) -> None:
    # 成人フラグがあっても、高校生の服装のキャラは控えめに描く(胸・曲線の設定を使わない)
    student = _character(client, "student", is_adult=1, outfit_tags="school uniform")
    base = _base(client, chest="large", build="curvy", skin_tone="fair")
    info = client.put(f"/api/lora-dataset/character/{student['id']}/body-base", json={"body_base_id": base["id"]}).json()
    assert info["modest"] is True and "large breasts" not in info["body_tags"] and "fair skin" in info["body_tags"]
    client.post(f"/api/lora-dataset/character/{student['id']}/body-base", json={"keys": ["back", "hand_palm"]})
    (back_prompt, back_negative), (_, hand_negative) = client.calls[-2:]  # type: ignore[attr-defined]
    assert "curvy" not in back_prompt and "skin-tight" not in back_prompt
    assert "ass focus" in back_negative
    # 部位ごとのネガティブ(手を突き出さない)
    assert "foreshortening" in hand_negative and "foreshortening" not in back_negative


def test_guard_values_come_from_the_db(client: TestClient) -> None:
    # 素体の未成年タグ・裸の素体のタグは DB(コンテンツガード)の値を使う。変えれば次の呼び出しから効く
    from python import content_guard

    adult = {"id": 1, "name": "a", "is_adult": 1, "age": 30, "outfit_tags": "maid outfit"}
    assert body_base.nude_block_reason(adult, []) is None
    content_guard.set_value("body_base.minor_tags", ["maid outfit"])
    assert "maid outfit" in (body_base.nude_block_reason(adult, []) or "")
    content_guard.set_value("body_base.nude_tags", "nude, mannequin")
    part = body_base.ITEMS["front"]
    assert "mannequin" in body_base.prompt(adult, {"kind": "nude"}, part, _defaults())
    content_guard.reset("body_base.nude_tags")
    assert "barbie doll anatomy" in body_base.prompt(adult, {"kind": "nude"}, part, _defaults())


def test_rear_parts_describe_the_chest_from_behind() -> None:
    # 真後ろの部位では、正面の胸のタグ(large breasts)の代わりに、後ろから見える形のタグにする
    base = {"kind": "suit", "chest": "large", "build": "curvy"}
    character = {"appearance_tags": "1girl, brown hair"}
    back = body_base.prompt(character, base, body_base.ITEMS["back"], _defaults())
    assert "sideboob" in back and "breasts peek" in back and "large breasts" not in back
    assert "large breasts" in body_base.prompt(character, base, body_base.ITEMS["front"], _defaults())
    # 控えめに描くキャラは、真後ろでも胸の設定を使わない
    assert "sideboob" not in body_base.prompt(character, base, body_base.ITEMS["back"], _defaults(), modest=True)


def test_options_are_edited_in_the_db(client: TestClient) -> None:
    data = client.get("/api/lora-dataset/body-bases/options").json()
    assert [f["key"] for f in data["fields"]][:2] == ["sex", "skin_tone"]
    skin = {o["value"]: o for o in data["options"]["skin_tone"]}
    assert skin["tan"]["label"] == "小麦色"

    # 足す(値は振る)・名前とタグを直す・並べ替える
    added = client.post("/api/lora-dataset/body-base-options", json={"field": "skin_tone", "label": "灰色", "tags": "grey skin"}).json()
    assert added["value"] == "opt1"
    edited = client.put(f"/api/lora-dataset/body-base-options/{added['id']}",
                        json={"field": "skin_tone", "label": "灰色の肌", "tags": "grey skin, colored skin"}).json()
    assert edited["label"] == "灰色の肌" and edited["value"] == "opt1"
    ids = [o["id"] for o in data["options"]["skin_tone"]]
    client.put("/api/lora-dataset/body-base-options/order", json={"field": "skin_tone", "ids": [added["id"], *ids]})
    first = client.get("/api/lora-dataset/body-bases/options").json()["options"]["skin_tone"][0]
    assert first["id"] == added["id"]

    # 素体で使うと、その選択肢のタグが入る。使っている選択肢は消せない
    character = _character(client, "test")
    base = _base(client, skin_tone="opt1")
    info = client.put(f"/api/lora-dataset/character/{character['id']}/body-base", json={"body_base_id": base["id"]}).json()
    assert "colored skin" in info["body_tags"]
    assert client.delete(f"/api/lora-dataset/body-base-options/{added['id']}").status_code == 409
    client.put(f"/api/lora-dataset/body-bases/{base['id']}", json={"name": "素体", "skin_tone": ""})
    assert client.delete(f"/api/lora-dataset/body-base-options/{added['id']}").status_code == 200
    # 選択肢に無い値の素体は作れない
    assert client.post("/api/lora-dataset/body-bases", json={"name": "x", "skin_tone": "opt1"}).status_code == 422


def test_option_tags_are_checked(client: TestClient) -> None:
    for tags in ("nude", "nipples", "school uniform"):
        res = client.post("/api/lora-dataset/body-base-options", json={"field": "build", "label": "x", "tags": tags})
        assert res.status_code == 422, tags
    res = client.post("/api/lora-dataset/body-base-options", json={"field": "build", "label": "x", "tags": "slim", "rear_tags": "nude"})
    assert res.status_code == 422
    assert client.post("/api/lora-dataset/body-base-options", json={"field": "hair", "label": "x"}).status_code == 422


def test_outfit_is_drawn_over_the_a_pose(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from python import body_rig

    character = _character(client, "test", is_adult=1, age=25)
    base = _base(client)
    client.put(f"/api/lora-dataset/character/{character['id']}/body-base", json={"body_base_id": base["id"]})
    url = f"/api/lora-dataset/character/{character['id']}/body-outfit"
    # Aポーズを描く前は着せられない
    assert client.post(url, json={"outfit_tags": "white blouse"}).status_code == 422
    client.post(f"/api/lora-dataset/character/{character['id']}/body-base", json={"keys": ["a_pose"]})

    points = {"left_shoulder": np.array([500.0, 260.0]), "right_shoulder": np.array([330.0, 260.0])}
    monkeypatch.setattr(body_rig, "outfit_inpaint_mask", lambda img: (Image.new("RGB", img.size, "white"), points))
    monkeypatch.setattr(body_rig, "detect", lambda img: (None, points))
    # 全年齢で止めるタグ・未成年を示すタグは着せられない
    assert client.post(url, json={"outfit_tags": "bikini"}).status_code == 422
    assert client.post(url, json={"outfit_tags": "school uniform"}).status_code == 422
    res = client.post(url, json={"outfit_tags": "white long-sleeved blouse, navy skirt"})
    assert res.status_code == 200, res.text
    outfit = res.json()["outfit"]
    assert outfit["outfit_tags"] == "white long-sleeved blouse, navy skirt" and outfit["drift"] == 0
    assert (tmp_path / outfit["path"]).is_file()
    prompt, negative = client.calls[-1]  # type: ignore[attr-defined]
    assert "navy skirt" in prompt and "a-pose" in prompt and "bodysuit" not in prompt
    assert "bodysuit" in negative and "nude" in negative
