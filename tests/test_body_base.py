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

from python import body_base, body_check, db  # noqa: E402
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
    # 自動チェック(タグの判定のモデル)は呼ばず、いつも合格にする。描き直しのテストでは差し替える
    monkeypatch.setattr(body_check, "check", lambda part, image: body_check.CheckResult(True, 1.0))
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
    assert "brown hair" in prompt and "pale skin" in prompt and "slender" in prompt and "bike shorts" in prompt
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
    # 成人の裸の素体: 乳首は描き、性器は描かない
    assert "nipples" in prompt and "featureless crotch" in prompt and "adult" in prompt
    assert "pussy" in negative and "nipples" not in negative and "loli" in negative
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
    # 成人フラグがあっても、高校生の服装のキャラは控えめなスーツで描く。体型(胸・曲線の設定)はキャラのまま
    # (少年誌の一般的なキャラの体型)で、寄り・構図での強調だけを抑える
    from python import content_guard

    student = _character(client, "student", is_adult=1, outfit_tags="school uniform")
    base = _base(client, chest="large", build="curvy", skin_tone="fair")
    info = client.put(f"/api/lora-dataset/character/{student['id']}/body-base", json={"body_base_id": base["id"]}).json()
    assert info["modest"] is True and "large breasts" in info["body_tags"] and "fair skin" in info["body_tags"]
    client.post(f"/api/lora-dataset/character/{student['id']}/body-base", json={"keys": ["back", "hand_palm"]})
    (back_prompt, back_negative), (_, hand_negative) = client.calls[-2:]  # type: ignore[attr-defined]
    assert "curvy" in back_prompt and "skin-tight" not in back_prompt
    assert "ass focus" in back_negative and "large breasts" not in back_negative
    # 外す設定は開発管理者のページで足せる
    content_guard.set_value("body_base.modest_skip", ["chest"])
    info = client.get(f"/api/lora-dataset/character/{student['id']}/body-base").json()
    assert "large breasts" not in info["body_tags"] and "curvy" in info["body_tags"]
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
    assert "featureless crotch" in body_base.prompt(adult, {"kind": "nude"}, part, _defaults())


def test_rear_parts_describe_the_chest_from_behind() -> None:
    # 真後ろの部位では、正面の胸のタグ(large breasts)の代わりに、後ろから見える形のタグにする
    base = {"kind": "suit", "chest": "large", "build": "curvy"}
    character = {"appearance_tags": "1girl, brown hair"}
    back = body_base.prompt(character, base, body_base.ITEMS["back"], _defaults())
    assert "sideboob" in back and "breasts peek" in back and "large breasts" not in back
    assert "large breasts" in body_base.prompt(character, base, body_base.ITEMS["front"], _defaults())
    # 控えめに描くキャラでも、体型の設定はそのまま使う
    assert "breasts peek" in body_base.prompt(character, base, body_base.ITEMS["back"], _defaults(), modest=True)


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
    assert "navy skirt" in prompt and "a-pose" in prompt and "bike shorts" not in prompt
    assert "bike shorts" in negative and "nude" in negative


def test_auto_check_redraws_until_it_passes(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    character = _character(client, "test")
    base = _base(client)
    client.put(f"/api/lora-dataset/character/{character['id']}/body-base", json={"body_base_id": base["id"]})
    results = iter([
        body_check.CheckResult(False, -0.5, ["振り返っている"]),
        body_check.CheckResult(True, 0.9),
    ])
    monkeypatch.setattr(body_check, "check", lambda part, image: next(results))
    url = f"/api/lora-dataset/character/{character['id']}/body-base"
    res = client.post(url, json={"keys": ["back"], "seed": 100})
    assert '"status": "retry"' in res.text and "振り返っている" in res.text
    assert len(client.calls) == 2  # type: ignore[attr-defined]
    entry = {p["key"]: p for p in client.get(url).json()["parts"]}["back"]
    assert entry["auto_check"]["passed"] is True and entry["auto_check"]["attempts"] == 2
    assert "肩甲骨" in entry["check"]  # 確かめる点(人体の構造)はそのまま


def test_auto_check_keeps_the_best_when_none_pass(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    character = _character(client, "test")
    base = _base(client)
    client.put(f"/api/lora-dataset/character/{character['id']}/body-base", json={"body_base_id": base["id"]})
    scores = iter([-1.0, 0.4, -0.2])
    monkeypatch.setattr(body_check, "check", lambda part, image: body_check.CheckResult(False, next(scores), ["x"]))
    url = f"/api/lora-dataset/character/{character['id']}/body-base"
    client.post(url, json={"keys": ["hand_palm"]})
    entry = {p["key"]: p for p in client.get(url).json()["parts"]}["hand_palm"]
    assert entry["auto_check"]["passed"] is False and entry["auto_check"]["score"] == 0.4 and entry["auto_check"]["attempts"] == 3
    # 自動チェックを切れば1回だけ描き、結果も残さない
    calls = len(client.calls)  # type: ignore[attr-defined]
    client.post(url, json={"keys": ["hand_palm"], "auto_check": False})
    assert len(client.calls) == calls + 1  # type: ignore[attr-defined]
    entry = {p["key"]: p for p in client.get(url).json()["parts"]}["hand_palm"]
    assert entry["auto_check"] is None


def test_outfit_retries_while_the_pose_drifts(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from python import body_rig

    character = _character(client, "test", is_adult=1, age=25)
    base = _base(client)
    client.put(f"/api/lora-dataset/character/{character['id']}/body-base", json={"body_base_id": base["id"]})
    client.post(f"/api/lora-dataset/character/{character['id']}/body-base", json={"keys": ["a_pose"]})
    points = {"left_shoulder": np.array([500.0, 260.0]), "right_shoulder": np.array([300.0, 260.0])}
    moved = {k: v + np.array([40.0, 0.0]) for k, v in points.items()}  # 肩幅 200 の 20% ずれ
    detected = iter([moved, points])
    monkeypatch.setattr(body_rig, "outfit_inpaint_mask", lambda img: (Image.new("RGB", img.size, "white"), points))
    monkeypatch.setattr(body_rig, "detect", lambda img: (None, next(detected)))
    calls = len(client.calls)  # type: ignore[attr-defined]
    res = client.post(f"/api/lora-dataset/character/{character['id']}/body-outfit", json={"outfit_tags": "white blouse"})
    outfit = res.json()["outfit"]
    # 1回目はずれが大きいので描き直し、2回目(強さを下げた)を残す
    assert len(client.calls) == calls + 2  # type: ignore[attr-defined]
    assert outfit["drift"] == 0 and outfit["attempts"] == 2 and not outfit["drift_warn"]


def test_breast_physics_only_for_adults(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from python import body_rig

    seen: list[dict] = []

    def fake_build(source, folder, outfit=None, **kwargs):  # noqa: ANN001, ANN003
        seen.append(kwargs)
        raise body_rig.RigError("止める")

    monkeypatch.setattr(body_rig, "build", fake_build)
    monkeypatch.setattr(body_rig, "find_blender", lambda *_: "blender.exe")
    base = _base(client)
    for name, sheet in (("adult", {"is_adult": 1, "age": 25}), ("student", {"is_adult": 1, "outfit_tags": "school uniform"}),
                        ("kid", {"is_minor": 1, "age": 10})):
        character = _character(client, name, **sheet)
        client.put(f"/api/lora-dataset/character/{character['id']}/body-base", json={"body_base_id": base["id"]})
        client.post(f"/api/lora-dataset/character/{character['id']}/body-base", json={"keys": ["a_pose"]})
        res = client.post(f"/api/lora-dataset/character/{character['id']}/body-rig",
                          json={"breast_physics": True, "wind": 0.5})
        assert res.status_code == 422
    assert [s["breasts"] for s in seen] == [True, False, False]
    assert all(s["hair"] and s["wind"] == 0.5 for s in seen)


def test_behind_hair_is_redrawn_without_long_hair(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from python import body_rig

    character = _character(client, "test", is_adult=1, age=25, appearance_tags="1girl, long hair, brown hair")
    base = _base(client)
    client.put(f"/api/lora-dataset/character/{character['id']}/body-base", json={"body_base_id": base["id"]})
    client.post(f"/api/lora-dataset/character/{character['id']}/body-base", json={"keys": ["a_pose"]})
    hanging = np.zeros((1216, 832), bool)
    hanging[300:600, 200:260] = True
    split = body_rig.HairSplit(hanging=hanging, back=np.zeros_like(hanging), chains=[], core=None,
                               filled=Image.new("RGB", (832, 1216), (90, 90, 90)), person=np.ones_like(hanging))
    seen = {}
    monkeypatch.setattr(body_rig, "detect", lambda img: (None, {}))
    monkeypatch.setattr(body_rig, "split_hair", lambda image, mask, points, prompts=None, region=None: seen.update(prompts=prompts) or split)
    points = [{"pos": [[230, 400]], "neg": [[400, 400]]}]
    res = client.post(f"/api/lora-dataset/character/{character['id']}/body-hair-under", json={"points": points, "strength": 0.5})
    assert res.status_code == 200, res.text
    assert seen["prompts"] == [{"pos": [(230.0, 400.0)], "neg": [(400.0, 400.0)]}]
    prompt, negative = client.calls[-1]  # type: ignore[attr-defined]
    assert "long hair" not in prompt and "short hair" in prompt and "long hair" in negative
    entry = res.json()["behind_hair"]
    assert entry["path"].endswith("_a_pose_nohair.png") and entry["region"].endswith("_a_pose_hairmask.png")
    assert (tmp_path / entry["region"]).is_file()


def test_swimsuit_base_is_all_ages_for_every_character(client: TestClient) -> None:
    # 水着素体は全年齢: 未成年の手がかりがあるキャラにも当てられ、控えめな水着のタグで描く。裸のタグは入らない
    student = _character(client, "student", is_adult=0, outfit_tags="school uniform")
    base = _base(client, kind="swimsuit", chest="large")
    info = client.put(f"/api/lora-dataset/character/{student['id']}/body-base", json={"body_base_id": base["id"]}).json()
    assert info["base"]["kind"] == "swimsuit" and info["modest"] is True
    client.post(f"/api/lora-dataset/character/{student['id']}/body-base", json={"keys": ["front"]})
    prompt, negative = client.calls[-1]  # type: ignore[attr-defined]
    assert "tankini" in prompt and "swim shorts" in prompt and "nude" not in prompt and "bike shorts" not in prompt
    assert "ass focus" in negative
    adult = {"appearance_tags": "1girl"}
    assert "bikini" in body_base.prompt(adult, {"kind": "swimsuit"}, body_base.ITEMS["front"], _defaults())
    # 全年齢の素体なので、全年齢で止めるタグは足すタグに入れられない
    assert client.post("/api/lora-dataset/body-bases", json={"name": "z", "kind": "swimsuit", "extra_tags": "nsfw"}).status_code == 422
