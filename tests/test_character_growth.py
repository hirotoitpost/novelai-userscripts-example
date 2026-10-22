"""
キャラの成長段階(同じ人物の年齢ごとのキャラシートを、元のキャラにつなぐ)のテスト: つなぎ方の検査、
元のキャラが未成年なら段階も未成年になること、元のキャラからの段階の作成(別名保存)。DB は一時フォルダに差し替える。

実行方法:
  uv run pytest tests/test_character_growth.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import db  # noqa: E402
from python.routes import story as story_routes  # noqa: E402


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    app = FastAPI()
    app.include_router(story_routes.router)
    return TestClient(app)


def _new(client: TestClient, **body: object) -> dict:
    res = client.post("/api/story/characters/new", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def test_stage_links_to_base_and_inherits_minor(client: TestClient) -> None:
    base = _new(client, name="ひなた", appearance_tags="baby girl, black hair", is_minor=True)
    # 段階は未成年を送らなくても未成年になる
    stage = _new(client, name="ひなた(1歳)", appearance_tags="toddler, black hair", growth_of=base["id"],
                 growth_label=" 1歳 ", growth_order=12)
    assert (stage["growth_of"], stage["growth_label"], stage["growth_order"]) == (base["id"], "1歳", 12)
    assert stage["is_minor"] and not stage["is_adult"]
    # 未成年の人物の段階を成人にはできない
    res = client.post("/api/story/characters/new",
                      json={"name": "ひなた(大人)", "appearance_tags": "1girl", "growth_of": base["id"], "is_adult": True})
    assert res.status_code == 422 and "成人にはできません" in res.json()["detail"]
    res = client.put(f"/api/story/characters/{stage['id']}/sheet", json={"is_adult": True})
    assert res.status_code == 422


def test_links_are_one_level_and_checked(client: TestClient) -> None:
    base = _new(client, name="そうた", appearance_tags="1boy")
    stage = _new(client, name="そうた(5歳)", appearance_tags="1boy, child", growth_of=base["id"])
    other = _new(client, name="ほか", appearance_tags="1girl")
    put = lambda cid, body: client.put(f"/api/story/characters/{cid}/sheet", json=body)  # noqa: E731
    # 段階を元のキャラにはできない・段階を持つキャラは段階にできない・自分自身にはつなげない・無いキャラにはつなげない
    assert "元のキャラにできません" in put(other["id"], {"growth_of": stage["id"]}).json()["detail"]
    assert "他のキャラの成長段階にはできません" in put(base["id"], {"growth_of": other["id"]}).json()["detail"]
    assert "自分自身" in put(other["id"], {"growth_of": other["id"]}).json()["detail"]
    assert put(other["id"], {"growth_of": 9999}).status_code == 422
    # null で段階から外せる
    res = put(stage["id"], {"growth_of": None})
    assert res.status_code == 200 and res.json()["growth_of"] is None


def test_making_the_base_minor_makes_its_stages_minor(client: TestClient) -> None:
    base = _new(client, name="みお", appearance_tags="1girl")
    stage = _new(client, name="みお(3歳)", appearance_tags="1girl, child", growth_of=base["id"])
    assert not stage["is_minor"]
    res = client.put(f"/api/story/characters/{base['id']}/sheet", json={"is_minor": True})
    assert res.status_code == 200, res.text
    stages = [c for c in client.get("/api/story/characters").json() if c["id"] == stage["id"]]
    assert stages[0]["is_minor"]


def test_age_decides_minor_or_adult(client: TestClient) -> None:
    child = _new(client, name="こども", appearance_tags="1girl, child", age=7)
    assert child["age"] == 7 and child["is_minor"] and not child["is_adult"]
    grown = _new(client, name="おとな", appearance_tags="1girl, adult woman", age=30)
    assert grown["is_adult"] and not grown["is_minor"]
    # 年齢が入っていれば、年齢区分は年齢で決まる(未成年の年齢で成人にはできない)
    res = client.put(f"/api/story/characters/{child['id']}/sheet", json={"is_adult": True})
    assert res.status_code == 200 and not res.json()["is_adult"]
    # 未成年から年齢を上げて成人にするのは守りを緩める変更なので、パスワードの合言葉が要る
    res = client.put(f"/api/story/characters/{child['id']}/sheet", json={"age": 20, "appearance_tags": "1girl"})
    assert res.status_code == 403
    after = next(c for c in client.get("/api/story/characters").json() if c["id"] == child["id"])
    assert after["age"] == 7 and after["is_minor"]
    # 18 歳以上でも、未成年を示すタグがあれば成人にはできない
    res = client.post("/api/story/characters/new", json={"name": "だめ", "appearance_tags": "toddler", "age": 20})
    assert res.status_code == 422


def test_grown_up_stage_of_a_minor_person(client: TestClient) -> None:
    base = _new(client, name="ひなた", appearance_tags="baby girl, black hair", is_minor=True)
    # 年齢の入った段階は、元のキャラが未成年でも年齢で決まる(成人した姿を別の段階として作れる)
    adult = _new(client, name="ひなた(20歳)", appearance_tags="1girl, adult woman, black hair", growth_of=base["id"],
                 growth_label="20歳", age=20)
    assert adult["is_adult"] and not adult["is_minor"] and adult["growth_of"] == base["id"]
    # 元のキャラを写して作るとき(別名保存)は、未成年を外すことになるのでパスワードの合言葉が要る
    res = client.post(f"/api/story/characters/{base['id']}/duplicate",
                      json={"name": "ひなた(18歳)", "sheet": {"growth_of": base["id"], "age": 18,
                                                             "appearance_tags": "1girl, black hair"}})
    assert res.status_code == 403


def test_stage_is_made_from_the_base_by_duplicate(client: TestClient) -> None:
    base = _new(client, name="ひなた", appearance_tags="baby girl, black hair, grey-green eyes", is_minor=True,
                negative_tags="adult")
    res = client.post(f"/api/story/characters/{base['id']}/duplicate",
                      json={"name": "ひなた(新生児)", "sheet": {"growth_of": base["id"], "growth_label": "新生児",
                                                              "growth_order": 0}})
    assert res.status_code == 200, res.text
    stage = res.json()
    assert stage["appearance_tags"] == "baby girl, black hair, grey-green eyes" and stage["negative_tags"] == "adult"
    assert (stage["growth_of"], stage["growth_label"], stage["is_minor"]) == (base["id"], "新生児", True)
    # 段階を別名保存すると、同じ人物の別の段階になる
    res = client.post(f"/api/story/characters/{stage['id']}/duplicate",
                      json={"name": "ひなた(1歳)", "sheet": {"growth_label": "1歳", "growth_order": 12}})
    assert res.status_code == 200, res.text
    assert (res.json()["growth_of"], res.json()["growth_label"]) == (base["id"], "1歳")
