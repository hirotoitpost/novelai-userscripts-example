"""
キャラ別データセットのシチュエーション(軸をひとまとまりにしたもの)と状態(容姿を一時的に変えるもの)のテスト。
NovelAI は使わない(生成のリクエストは検査で止まるところまでを見る)。

実行方法:
  uv run pytest tests/test_dataset_scenes.py -v
"""

from __future__ import annotations

import random
import sys
from collections import Counter
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.character_sheet import adult_only_tags, body_tags, build_variation_shots, look_tags, split_body  # noqa: E402
from python.routes import lora_dataset as routes  # noqa: E402

YURA = {
    "id": 1,
    "name": "ゆら",
    "trigger_word": "yura",
    "appearance_tags": "1girl, brown hair, slim, {flat stomach}",
    "outfit_tags": "hoodie",
    "style_tags": "best quality",
}
GYM = {
    "name": "ジム",
    "framings": [],
    "poses": ["lifting dumbbell", "stretching"],
    "outfits": ["sportswear"],
    "expressions": [],
    "locations": ["gym"],
    "extra_tags": "",
}
NAP = {
    "name": "昼寝",
    "framings": ["from above"],
    "poses": ["sleeping, lying on bed"],
    "outfits": [],
    "expressions": ["closed eyes"],
    "locations": ["bedroom"],
    "extra_tags": "daytime",
}
FULL = {"name": "食べ過ぎ", "add_tags": "round belly, plump", "remove_tags": "slim, flat stomach"}


def test_state_swaps_body_tags_and_leaves_the_rest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 成人キャラだけのタグはコンテンツガードの設定(DB)から読むので、はじめの値の入った別の DB で確かめる
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    assert body_tags(YURA["appearance_tags"], FULL) == "1girl, brown hair, round belly, plump"
    assert body_tags(YURA["appearance_tags"], None) == YURA["appearance_tags"]
    assert adult_only_tags("pregnant, big belly") == ["pregnant"] and adult_only_tags("round belly") == []


def test_situation_keeps_its_axes_together() -> None:
    shots = build_variation_shots(
        YURA,
        framings=["full body", "upper body"],
        poses=["waving", "reading book"],
        outfits=["yukata"],
        expressions=["smile"],
        locations=["park", "cafe"],
        count=12,
        rng=random.Random(1),
        situations=[GYM, NAP],
    )
    assert len(shots) == 12 and Counter(s.situation for s in shots) == {"ジム": 6, "昼寝": 6}
    for shot in shots:
        tags = shot.prompt.split(", ")
        if shot.situation == "ジム":
            # 決めている軸はジムのものだけ。決めていない軸(構図・表情)は、ふつうのバリエーションから
            assert "gym" in tags and "sportswear" in tags and ("lifting dumbbell" in tags or "stretching" in tags)
            assert not {"park", "cafe", "yukata", "waving", "reading book"} & set(tags)
            assert "smile" in tags and ("full body" in tags or "upper body" in tags)
        else:
            assert {"bedroom", "from above", "sleeping", "lying on bed", "closed eyes", "daytime"} <= set(tags)
            # 服装を決めていないので、選んだ服装(浴衣)
            assert "yukata" in tags
    # 容姿・画風はいつも入る
    assert all(s.prompt.startswith("yura, 1girl, brown hair") and s.prompt.endswith("best quality") for s in shots)


def test_states_and_plain_body_are_spread_evenly() -> None:
    shots = build_variation_shots(
        YURA, framings=[], poses=["standing", "sitting"], outfits=[], expressions=[], locations=[], count=7,
        rng=random.Random(2), situations=[GYM], states=[FULL, None],
    )
    assert sorted(Counter(s.state for s in shots).values()) == [3, 4]
    for shot in shots:
        if shot.state:
            assert "round belly" in shot.prompt and "slim" not in shot.prompt.split(", ")
        else:
            assert "slim" in shot.prompt.split(", ") and "round belly" not in shot.prompt
        # 服装はシチュエーションのもの(状態はそれを変えない)
        assert "sportswear" in shot.prompt
    # 何も選ばなければ、これまでと同じ
    plain = build_variation_shots(YURA, poses=[], outfits=[], expressions=[], locations=[], count=1)[0]
    assert plain.prompt == "yura, 1girl, brown hair, slim, {flat stomach}, hoodie, best quality"
    assert plain.situation == "" and plain.state == ""


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    db.get_connection().close()
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as test_client:
        yield test_client


def test_situations_and_states_can_be_edited(client: TestClient) -> None:
    situations = client.get("/api/lora-dataset/situations").json()
    states = client.get("/api/lora-dataset/states").json()
    # 初めは例が入っている。成人キャラだけのタグを持つものには印が付く
    assert [s["name"] for s in situations] == [s["name"] for s in db.DEFAULT_DATASET_SITUATIONS]
    assert [s["name"] for s in states] == [s["name"] for s in db.DEFAULT_DATASET_STATES]
    assert {s["name"]: s["adult_only"] for s in states} == {"食べ過ぎでお腹が出る": [], "妊娠してお腹が大きい": ["pregnant"]}

    made = client.post(
        "/api/lora-dataset/situations",
        json={"name": " 海で泳ぐ ", "locations": [" beach ", "beach", ""], "poses": ["swimming"], "extra_tags": " summer ,  day "},
    )
    assert made.status_code == 200, made.text
    item = made.json()
    assert item["name"] == "海で泳ぐ" and item["locations"] == ["beach"] and item["extra_tags"] == "summer, day"
    assert client.post("/api/lora-dataset/situations", json={"name": "海で泳ぐ", "poses": ["x"]}).status_code == 409
    assert client.post("/api/lora-dataset/situations", json={"name": "空っぽ"}).status_code == 422
    changed = client.put(f"/api/lora-dataset/situations/{item['id']}", json={"name": "海で遊ぶ", "poses": ["splashing"]}).json()
    assert changed["name"] == "海で遊ぶ" and changed["poses"] == ["splashing"] and changed["locations"] == []
    assert client.put("/api/lora-dataset/situations/999", json={"name": "x", "poses": ["x"]}).status_code == 404
    assert client.delete(f"/api/lora-dataset/situations/{item['id']}").status_code == 204
    assert client.delete(f"/api/lora-dataset/situations/{item['id']}").status_code == 404

    state = client.post("/api/lora-dataset/states", json={"name": "日焼け", "add_tags": "tan, tanlines", "remove_tags": "pale skin"}).json()
    assert state["add_tags"] == "tan, tanlines" and state["adult_only"] == []
    assert client.post("/api/lora-dataset/states", json={"name": "空", "add_tags": " ", "remove_tags": ""}).status_code == 422
    assert client.put(f"/api/lora-dataset/states/{state['id']}", json={"name": "日焼け2", "add_tags": "tan"}).json()["name"] == "日焼け2"
    assert client.delete(f"/api/lora-dataset/states/{state['id']}").status_code == 204
    # 消したものは、開き直しても戻らない(例も)
    client.delete(f"/api/lora-dataset/states/{states[0]['id']}")
    db.get_connection().close()
    assert [s["name"] for s in client.get("/api/lora-dataset/states").json()] == ["妊娠してお腹が大きい"]


def _character(is_adult: bool, outfit: str = "hoodie") -> int:
    conn = db.get_connection()
    try:
        created = db.save_character(conn, f"テスト{int(is_adult)}{outfit}", "1girl, brown hair", None)
        db.update_character_sheet(conn, created["id"], {"is_adult": int(is_adult), "outfit_tags": outfit})
        return created["id"]
    finally:
        conn.close()


def test_adult_only_states_need_an_adult_character_without_minor_tags(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image

    prompts: list[str] = []

    async def fake_generate(client, prompt, size, cfg, *, seed=None):
        prompts.append(prompt)
        return Image.new("RGB", (8, 8), "white")

    async def no_wait(_seconds):
        return None

    # NovelAI には送らない。保存先も一時フォルダに
    monkeypatch.setattr(routes, "generate_one", fake_generate)
    monkeypatch.setattr(routes.asyncio, "sleep", no_wait)
    monkeypatch.setattr(routes, "_character_root", lambda root, character: tmp_path / "dataset")
    states = {s["name"]: s["id"] for s in client.get("/api/lora-dataset/states").json()}
    situations = {s["name"]: s["id"] for s in client.get("/api/lora-dataset/situations").json()}
    pregnant = states["妊娠してお腹が大きい"]
    headers = {"Authorization": "Bearer t"}

    def generate(character_id: int, **body):
        return client.post(f"/api/lora-dataset/character/{character_id}/generate", json={"count": 1, **body}, headers=headers)

    child = _character(False)
    res = generate(child, state_ids=[pregnant])
    assert res.status_code == 403 and "成人キャラ" in res.json()["detail"]
    adult = _character(True)
    # 未成年を示すタグ(体育の授業の gym uniform)と一緒には使えない
    res = generate(adult, state_ids=[pregnant], situation_ids=[situations["体育の授業で立位体前屈"]])
    assert res.status_code == 422 and "gym uniform" in res.json()["detail"]
    # 無いIDは 404
    assert generate(adult, state_ids=[999]).status_code == 404
    # ふつうの状態は、成人でなくても使える(検査を通って、生成の段階に進む)
    full = states["食べ過ぎでお腹が出る"]
    res = generate(child, state_ids=[full], situation_ids=[situations["カフェで休憩"]], include_plain_state=False)
    assert res.status_code == 200
    assert prompts and "round belly" in prompts[0] and "cafe" in prompts[0]
    assert '"situation": "カフェで休憩"' in res.text and '"state": "食べ過ぎでお腹が出る"' in res.text


def test_body_field_is_replaced_by_states() -> None:
    face, body = split_body("1girl, black hair, long hair, large eyes, pale skin, slim, flat stomach, medium breasts, tall")
    assert face == "1girl, black hair, long hair, large eyes, pale skin" and body == "slim, flat stomach, medium breasts, tall"
    character = {**YURA, "appearance_tags": "1girl, brown hair", "body_tags": "slim, flat stomach, medium breasts"}
    assert look_tags(character) == "1girl, brown hair, slim, flat stomach, medium breasts"
    # 体型の欄があれば、状態はそれを丸ごと置き換える(外すタグを書かなくても、元の体型とぶつからない)
    assert look_tags(character, {"name": "x", "add_tags": "plump, round belly", "remove_tags": ""}) == "1girl, brown hair, plump, round belly"
    # 体型の欄が無いキャラは、これまでどおり容姿から外して足す
    assert look_tags(YURA, FULL) == body_tags(YURA["appearance_tags"], FULL)
    shot = build_variation_shots(character, poses=[], outfits=[], expressions=[], locations=[], count=1)[0]
    assert shot.prompt == "yura, 1girl, brown hair, slim, flat stomach, medium breasts, hoodie, best quality"


# ---- 部位(インペイントで作る状態)・顔の切り抜き ----


def test_part_box_is_measured_in_head_units() -> None:
    from python.body_parts import PARTS, part_box

    head = (300.0, 100.0, 500.0, 300.0)  # 幅200・高さ200
    top, bottom, half, _ = PARTS["belly"]
    assert part_box(head, "belly", (832, 1216)) == (int(400 - half * 200), int(300 + top * 200), int(400 + half * 200), int(300 + bottom * 200))
    # 画面の外に出る分は切る。範囲が無くなれば None。知らない部位も None
    assert part_box((300.0, 1000.0, 500.0, 1200.0), "belly", (832, 1216)) is None
    assert part_box(head, "tail", (832, 1216)) is None
    assert part_box((0.0, 0.0, 400.0, 400.0), "belly", (832, 1216))[0] == 0


def test_face_crop_skips_small_faces(monkeypatch: pytest.MonkeyPatch) -> None:
    from PIL import Image

    from python import body_parts

    image = Image.new("RGB", (832, 1216), "white")
    monkeypatch.setattr(body_parts, "find_head", lambda image: (300.0, 100.0, 550.0, 350.0))
    crop = body_parts.face_crop(image)
    # 頭の枠(250px)に、まわり 12% ずつの余白
    assert crop is not None and crop.size == (310, 310)
    monkeypatch.setattr(body_parts, "find_head", lambda image: (300.0, 100.0, 380.0, 180.0))
    assert body_parts.face_crop(image) is None
    monkeypatch.setattr(body_parts, "find_head", lambda image: None)
    assert body_parts.face_crop(image) is None


def test_inpaint_method_redraws_the_part_of_each_plain_image(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image

    calls: list[tuple[str, bool]] = []

    async def fake_generate(client, prompt, size, cfg, *, seed=None, inpaint=None):
        calls.append((prompt, inpaint is not None))
        return Image.new("RGB", (8, 8), "white")

    async def no_wait(_seconds):
        return None

    heads = iter([True, False])
    monkeypatch.setattr(routes, "generate_one", fake_generate)
    monkeypatch.setattr(routes.asyncio, "sleep", no_wait)
    monkeypatch.setattr(routes, "_character_root", lambda root, character: tmp_path / "dataset")
    # 1枚目は頭が見つかって描き直す。2枚目は見つからず、状態のタグで生成し直す
    monkeypatch.setattr(
        routes, "part_mask", lambda image, part: (Image.new("RGB", (8, 8), "white"), (0, 0, 4, 4)) if next(heads) else None
    )
    monkeypatch.setattr(routes, "face_crop", lambda image: Image.new("RGB", (300, 300), "white"))
    states = {s["name"]: s for s in client.get("/api/lora-dataset/states").json()}
    full = states["食べ過ぎでお腹が出る"]
    assert full["part"] == "belly"
    conn = db.get_connection()
    try:
        created = db.save_character(conn, "描き直しの確認", "1girl, brown hair", None)
        db.update_character_sheet(conn, created["id"], {"body_tags": "slim, flat stomach"})
    finally:
        conn.close()
    res = client.post(
        f"/api/lora-dataset/character/{created['id']}/generate",
        json={"count": 2, "state_ids": [full["id"]], "state_method": "inpaint", "include_plain_state": False, "save_face_crops": True},
        headers={"Authorization": "Bearer t"},
    )
    assert res.status_code == 200, res.text
    # ふだんの体2枚(描き直しの元なので、ふだんの体の枚を作らない設定でも作る)+ 状態2枚
    assert [inpainted for _, inpainted in calls] == [False, True, False, False]
    assert "slim" in calls[0][0] and "round belly" in calls[1][0] and "slim" not in calls[1][0]
    assert '"method": "inpaint"' in res.text and '"method": "prompt"' in res.text and '"base": "0001.png"' in res.text
    folder = tmp_path / "dataset"
    assert sorted(p.name for p in (folder / "generated").glob("*.png")) == ["0001.png", "0002.png", "0003.png", "0004.png"]
    assert "round belly" in (folder / "generated" / "0002.txt").read_text(encoding="utf-8")
    assert sorted(p.name for p in (folder / "faces").glob("*.txt")) == ["0001.txt", "0002.txt", "0003.txt", "0004.txt"]
    assert (folder / "faces" / "0001.txt").read_text(encoding="utf-8").endswith("portrait, close-up")
    # 部位は API で変えられる
    changed = client.put(f"/api/lora-dataset/states/{full['id']}", json={"name": full["name"], "add_tags": "plump", "part": "torso"}).json()
    assert changed["part"] == "torso"
    assert client.put(f"/api/lora-dataset/states/{full['id']}", json={"name": "x", "add_tags": "plump", "part": "tail"}).status_code == 422
    assert [p["key"] for p in client.get("/api/lora-dataset/body-parts").json()] == ["belly", "chest", "torso", "legs"]
