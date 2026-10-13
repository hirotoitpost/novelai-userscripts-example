"""
キャラ別データセットのバリエーションの候補(DB で持つ)と、プロンプトからのキーワードの取り出しのテスト。

実行方法:
  uv run pytest tests/test_dataset_variations.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import character_sheet, db  # noqa: E402
from python.dataset_variations import axis_of, clean_value, extract, hint_axis, prompt_tags  # noqa: E402
from python.routes import lora_dataset as routes  # noqa: E402


def test_prompt_is_split_into_plain_tags() -> None:
    prompt = "1girl, {{smile}}, 1.3::brown_hair::, [looking  at viewer], -1::blurry::, [表情: scared], Smile,\nnight"
    assert prompt_tags(prompt) == ["1girl", "smile", "brown hair", "looking at viewer", "night"]
    assert prompt_tags("") == [] and prompt_tags("x" * 101) == []


@pytest.mark.parametrize(
    ("tag", "axis"),
    [
        ("cowboy shot", "framings"),
        ("from above", "framings"),
        ("dutch angle", "framings"),
        ("sitting on chair", "poses"),
        ("looking at viewer", "poses"),
        ("peace sign", "poses"),
        ("arms crossed", "poses"),
        ("school uniform", "outfits"),
        ("hair ribbon", "outfits"),
        ("smile", "expressions"),
        ("closed eyes", "expressions"),
        ("open mouth", "expressions"),
        (":d", "expressions"),
        ("classroom", "locations"),
        ("night", "locations"),
        ("simple background", "locations"),
        ("depth of field", "other"),
        ("cat", "other"),
        # 容姿と、品質・画風は、キャラシートの側
        ("brown hair", "appearance"),
        ("blue eyes", "appearance"),
        ("masterpiece", "style"),
        ("artist:someone", "style"),
        # 人数は取り出さない
        ("1girl", "skip"),
        ("solo", "skip"),
        # 性的なタグは軸に入れない
        ("nude", "adult"),
    ],
)
def test_tags_go_to_their_axis(tag: str, axis: str) -> None:
    assert axis_of(tag) == axis


def test_extract_groups_keywords_and_keeps_first_seen_order() -> None:
    result = extract(
        [
            "1girl, smile, brown hair, school_uniform, classroom, sitting, cowboy shot, masterpiece, cat, nude",
            "1boy, Smile, hoodie, park, waving, cat",
        ]
    )
    assert result["axes"] == {
        "framings": ["cowboy shot"],
        "poses": ["sitting", "waving"],
        "outfits": ["school uniform", "hoodie"],
        "expressions": ["smile"],
        "locations": ["classroom", "park"],
    }
    assert result["other"] == ["cat"] and result["adult"] == ["nude"] and result["suggested"] == {}
    assert result["sheet"] == {"appearance": ["brown hair"], "style": ["masterpiece"]}
    assert result["skipped"] == ["1girl", "1boy"]
    assert clean_value("  school  uniform ,blazer ,, ") == "school uniform, blazer"


def test_category_names_hint_the_axis_of_unknown_keywords() -> None:
    assert hint_axis("表情") == "expressions" and hint_axis("服") == "outfits" and hint_axis("カメラ視点") == "framings"
    assert hint_axis("風景") == "locations" and hint_axis("ポーズ集") == "poses"
    assert hint_axis("髪型") == "appearance" and hint_axis("体型") == "appearance"
    assert hint_axis("絵柄") == "style" and hint_axis("アーティストタグ") == "style"
    assert hint_axis("キャラクター") is None and hint_axis("") is None
    # 語の一覧で分かるものはそのまま。分からないもの(と成人向け)にだけ、見当を付ける。取り出さないものには付けない
    result = extract(["tsurime, smile, brown hair, nude", "umbrella", "lantern"], ["expressions", None, "bogus"])
    assert result["axes"]["expressions"] == ["smile"]
    assert result["other"] == ["tsurime", "umbrella", "lantern"] and result["sheet"]["appearance"] == ["brown hair"]
    assert result["suggested"] == {"tsurime": "expressions", "nude": "expressions"}
    # キャラシートの項目の見当も付く
    sheet = extract(["streaked something", "flat tone"], ["appearance", "style"])
    assert sheet["suggested"] == {"streaked something": "appearance", "flat tone": "style"}


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "app.db")
    db.get_connection().close()
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as test_client:
        yield test_client


def test_candidates_start_from_the_defaults_and_can_be_changed(client: TestClient) -> None:
    url = "/api/lora-dataset/variations"
    start = client.get(url).json()
    assert start == {
        "framings": character_sheet.FRAMINGS,
        "poses": character_sheet.POSES,
        "outfits": character_sheet.OUTFITS,
        "expressions": character_sheet.EXPRESSIONS,
        "locations": character_sheet.LOCATIONS,
    }
    # 足す: 空白をそろえ、同じものは増えない。ほかの軸には入らない
    added = client.post(url, json={"axis": "poses", "values": [" holding  umbrella ", "holding umbrella", "standing", " "]}).json()
    assert added["poses"] == [*character_sheet.POSES, "holding umbrella"] and added["locations"] == start["locations"]
    assert client.post(url, json={"axis": "locations", "values": ["holding umbrella"]}).json()["locations"][-1] == "holding umbrella"
    assert client.post(url, json={"axis": "poses", "values": ["  "]}).status_code == 422
    assert client.post(url, json={"axis": "hair", "values": ["x"]}).status_code == 422
    # 消す: 既定の候補も消せて、開き直しても戻らない
    gone = client.delete(url, params={"axis": "poses", "value": "standing"}).json()
    assert "standing" not in gone["poses"]
    assert client.delete(url, params={"axis": "poses", "value": "standing"}).status_code == 404
    db.get_connection().close()
    assert "standing" not in client.get(url).json()["poses"] and "holding umbrella" in client.get(url).json()["poses"]


def test_extract_endpoint_only_reports(client: TestClient) -> None:
    before = client.get("/api/lora-dataset/variations").json()
    res = client.post("/api/lora-dataset/variations/extract", json={"prompts": ["1girl, grin, beach, running, sundress, from below"]})
    assert res.status_code == 200, res.text
    assert res.json()["axes"] == {
        "framings": ["from below"],
        "poses": ["running"],
        "outfits": ["sundress"],
        "expressions": ["grin"],
        "locations": ["beach"],
    }
    hinted = client.post(
        "/api/lora-dataset/variations/extract", json={"prompts": ["lantern", "umbrella"], "hints": ["locations", None]}
    ).json()
    assert hinted["other"] == ["lantern", "umbrella"] and hinted["suggested"] == {"lantern": "locations"}
    # 取り出すだけで、候補には足さない
    assert client.get("/api/lora-dataset/variations").json() == before
    assert client.post("/api/lora-dataset/variations/extract", json={"prompts": []}).status_code == 422
