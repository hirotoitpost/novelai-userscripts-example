"""
衣装(着せ替え。wardrobe.py と /api/wardrobe)のテスト。NovelAI は呼ばず、生成の関数を差し替える。

実行方法:
  uv run pytest tests/test_wardrobe.py -v
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

from python import body_check, db, wardrobe  # noqa: E402
from python.client import get_client  # noqa: E402
from python.routes import lora_dataset as lora_routes  # noqa: E402
from python.routes import story as story_routes  # noqa: E402
from python.routes import wardrobe as wardrobe_routes  # noqa: E402


def _catalog() -> wardrobe.Catalog:
    rows = [
        {"slot": slot, "value": v, "label": label, "tags": tags, "colorable": int(colorable)}
        for slot, choices in wardrobe.DEFAULT_PARTS.items()
        for v, label, tags, colorable in choices
    ]
    return wardrobe.catalog_map(rows)


def test_compose_puts_color_and_pattern_before_the_first_tag() -> None:
    catalog = _catalog()
    parts = [
        {"slot": "footwear", "value": "loafers", "color": "brown"},
        {"slot": "bottom", "value": "pleated_skirt", "color": "navy", "pattern": "plaid"},
        {"slot": "top", "value": "blouse", "color": "white"},
        # 色を付けられない部品・表に無い部品
        {"slot": "dress", "value": "gym_uniform", "color": "red"},
        {"slot": "top", "value": "nope"},
    ]
    tags = wardrobe.compose(parts, "red neck ribbon", catalog)
    # 部位の並び順(一式 → トップス → ボトムス → 靴)、自由入力は最後
    assert tags == "gym uniform, gym shirt, shorts, white blouse, navy blue plaid pleated skirt, brown loafers, red neck ribbon"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(lora_routes, "_PROJECT_ROOT", tmp_path)
    calls: list[str] = []

    async def fake_generate(client, prompt, size, cfg, seed=None, inpaint=None):  # noqa: ANN001
        calls.append(prompt)
        return Image.new("RGB", size, (200, 200, 200))

    async def noop(*_args) -> None:
        return None

    monkeypatch.setattr(lora_routes, "generate_one", fake_generate)
    monkeypatch.setattr(lora_routes.asyncio, "sleep", noop)
    monkeypatch.setattr(body_check, "check", lambda part, image: body_check.CheckResult(True, 1.0))
    app = FastAPI()
    for router in (story_routes.router, lora_routes.router, wardrobe_routes.router):
        app.include_router(router)
    app.dependency_overrides[get_client] = lambda: object()
    test_client = TestClient(app)
    test_client.calls = calls  # type: ignore[attr-defined]
    return test_client


def _character(client: TestClient, name: str, **sheet) -> dict:
    character = client.post("/api/story/characters/new", json={"name": name, "appearance_tags": "1girl", "trigger_word": name}).json()
    if sheet:
        conn = db.get_connection()
        try:
            db.update_character_sheet(conn, character["id"], sheet)
        finally:
            conn.close()
    return character


def _sheet(client: TestClient, character_id: int) -> dict:
    return next(c for c in client.get("/api/story/characters").json() if c["id"] == character_id)


def test_parts_are_seeded_and_checked(client: TestClient) -> None:
    data = client.get("/api/wardrobe/parts").json()
    assert [s["key"] for s in data["slots"]][:3] == ["dress", "outer", "top"]
    assert any(p["value"] == "bikini" for p in data["parts"]["swimwear"])
    assert any(p["value"] == "navy" for p in data["parts"]["color"])
    # 全年齢で止めるタグ・未成年を示すタグ・性的な場面のタグは部品にできない
    for tags in ("lingerie", "nude", "school uniform"):
        assert client.post("/api/wardrobe/parts", json={"slot": "top", "label": "x", "tags": tags}).status_code == 422, tags
    assert client.post("/api/wardrobe/parts", json={"slot": "hair", "label": "x", "tags": "hat"}).status_code == 422
    made = client.post("/api/wardrobe/parts", json={"slot": "top", "label": "パーカー", "tags": "hoodie"}).json()
    assert made["value"] == "part1" and made["colorable"] == 1


def test_outfits_dress_up_the_character(client: TestClient) -> None:
    character = _character(client, "a", is_adult=1, age=25, outfit_tags="white shirt, jeans")
    url = f"/api/wardrobe/characters/{character['id']}/outfits"
    # 衣装が無ければ、普段の服装から1着目を作り、それを普段の衣装にする
    data = client.get(url).json()
    assert [o["name"] for o in data["outfits"]] == ["普段の服装"]
    first = data["outfits"][0]
    assert first["tags"] == "white shirt, jeans" and data["outfit_id"] == first["id"]

    swim = {"name": "水着", "parts": [{"slot": "swimwear", "value": "bikini", "color": "red", "pattern": "polka_dot"}],
            "extra_tags": "sun hat"}
    data = client.post(url, json=swim).json()
    second = data["outfits"][1]
    assert second["tags"] == "red polka dot bikini, sun hat"
    # 着せ替え: 普段の服装のタグも変わる
    data = client.put(f"/api/wardrobe/characters/{character['id']}/outfit", json={"outfit_id": second["id"]}).json()
    assert data["outfit_id"] == second["id"]
    sheet = _sheet(client, character["id"])
    assert sheet["outfit_tags"] == "red polka dot bikini, sun hat" and sheet["outfit_id"] == second["id"]
    # 普段の衣装を書き換えると、普段の服装のタグも書き換わる
    client.put(f"{url}/{second['id']}", json={**swim, "extra_tags": "straw hat"})
    assert _sheet(client, character["id"])["outfit_tags"] == "red polka dot bikini, straw hat"
    # 表に無い部品・ほかのキャラの衣装は扱わない
    assert client.post(url, json={"name": "x", "parts": [{"slot": "top", "value": "nope"}]}).status_code == 422
    other = _character(client, "b")
    assert client.put(f"/api/wardrobe/characters/{other['id']}/outfit", json={"outfit_id": second["id"]}).status_code == 404
    # 全員の衣装(キャラ一覧・漫画の場面の候補)
    listed = client.get("/api/wardrobe/outfits").json()
    assert [(o["character_id"], o["name"], o["tags"]) for o in listed] == [
        (character["id"], "普段の服装", "white shirt, jeans"), (character["id"], "水着", "red polka dot bikini, straw hat"),
    ]
    # まだ衣装の無いキャラも、普段の服装から1着目ができる
    _character(client, "c", outfit_tags="hoodie")
    assert client.get("/api/wardrobe/outfits").json()[-1]["tags"] == "hoodie"
    # 消すと普段の衣装なし(服装のタグは残す)
    data = client.delete(f"{url}/{second['id']}").json()
    assert data["outfit_id"] is None and data["outfit_tags"] == "red polka dot bikini, straw hat"


def test_outfit_tags_follow_the_age_rules(client: TestClient) -> None:
    student = _character(client, "s", outfit_tags="school uniform")
    url = f"/api/wardrobe/characters/{student['id']}/outfits"
    # 未成年の手がかりがあるキャラは、全年齢で止めるタグを入れられない。水着は入れられる
    assert client.post(url, json={"name": "x", "extra_tags": "lingerie"}).status_code == 422
    assert client.post(url, json={"name": "水着", "parts": [{"slot": "swimwear", "value": "tankini"}]}).status_code == 200
    # 成人キャラは、未成年を示すタグを入れられない。性的な場面のタグはだれにも入れられない
    adult = _character(client, "a", is_adult=1, age=30)
    adult_url = f"/api/wardrobe/characters/{adult['id']}/outfits"
    assert client.post(adult_url, json={"name": "x", "extra_tags": "school uniform"}).status_code == 422
    assert client.post(adult_url, json={"name": "x", "extra_tags": "sex"}).status_code == 422


def test_outfit_is_dressed_on_the_body_base(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from python import body_rig

    character = _character(client, "a", is_adult=1, age=25)
    base = client.post("/api/lora-dataset/body-bases", json={"name": "suit", "kind": "suit"}).json()
    client.put(f"/api/lora-dataset/character/{character['id']}/body-base", json={"body_base_id": base["id"]})
    client.post(f"/api/lora-dataset/character/{character['id']}/body-base", json={"keys": ["a_pose"]})
    points = {"left_shoulder": np.array([500.0, 260.0]), "right_shoulder": np.array([330.0, 260.0])}
    monkeypatch.setattr(body_rig, "outfit_inpaint_mask", lambda img: (Image.new("RGB", img.size, "white"), points))
    monkeypatch.setattr(body_rig, "detect", lambda img: (None, points))

    url = f"/api/wardrobe/characters/{character['id']}/outfits"
    outfits = []
    for name, value in (("夏", "sundress"), ("浴衣", "yukata")):
        outfits = client.post(url, json={"name": name, "parts": [{"slot": "dress", "value": value, "color": "white"}]}).json()["outfits"]
    summer, yukata = outfits
    assert summer["dressed"] is None
    dress = f"/api/lora-dataset/character/{character['id']}/body-outfit"
    for outfit in (summer, yukata):
        res = client.post(dress, json={"outfit_id": outfit["id"]})
        assert res.status_code == 200, res.text
        assert "white " + outfit["tags"].split("white ")[1] in client.calls[-1]  # type: ignore[attr-defined]
    # いま2Dモデルに使う絵は、最後に着せた衣装
    assert client.get(f"/api/lora-dataset/character/{character['id']}/body-rig").json()["outfit"]["outfit_id"] == yukata["id"]
    listed = client.get(url).json()["outfits"]
    assert all(o["dressed"] and (tmp_path / o["dressed"]["path"]).is_file() for o in listed)
    # 前に着せた衣装の絵に戻す(描き直さない)
    count = len(client.calls)  # type: ignore[attr-defined]
    res = client.post(f"{dress}/{summer['id']}/use")
    assert res.status_code == 200 and res.json()["outfit"]["outfit_id"] == summer["id"]
    assert len(client.calls) == count  # type: ignore[attr-defined]
    assert client.post(f"{dress}/999/use").status_code == 404
    assert client.post(dress, json={"outfit_id": 999}).status_code == 404
