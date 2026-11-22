"""X への投稿(x_posts.py / x_api.py)。X の API は呼ばず、偽の客(FakeX)で確かめる。"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from python import db, minor_guard, x_api, x_posts
from python.image_tagger import TagScore


class FakeX:
    def __init__(self, fail_on_post: int | None = None) -> None:
        self.uploads: list[tuple[bytes, str]] = []
        self.metadata: list[tuple[str, bool]] = []
        self.posts: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.fail_on_post = fail_on_post

    def me(self) -> dict[str, Any]:
        return {"id": "1", "username": "tester"}

    def upload_image(self, data: bytes, mime: str, filename: str = "image") -> str:
        self.uploads.append((data, mime))
        return f"m{len(self.uploads)}"

    def set_media_metadata(self, media_id: str, *, sensitive: bool = False, alt_text: str | None = None) -> None:
        self.metadata.append((media_id, sensitive))

    def create_post(self, text: str, media_ids: list[str] | None = None, reply_to: str | None = None) -> str:
        if self.fail_on_post is not None and len(self.posts) == self.fail_on_post:
            self.fail_on_post = None
            raise x_api.XApiError("X の API のエラー(503): busy", 503)
        self.posts.append({"text": text, "media": list(media_ids or []), "reply_to": reply_to})
        return f"t{len(self.posts)}"

    def delete_post(self, post_id: str) -> None:
        self.deleted.append(post_id)


def _png(path: Path, with_comment: bool = True) -> None:
    info = PngInfo()
    if with_comment:
        info.add_text("Comment", '{"prompt": "1girl"}')
    Image.new("RGBA", (32, 32), (200, 100, 50, 255)).save(path, "PNG", pnginfo=info)


def _safe(_: Image.Image) -> list[Any]:
    return [TagScore("general", 0.9, "rating"), TagScore("explicit", 0.01, "rating")]


def _explicit(_: Image.Image) -> list[Any]:
    return [TagScore("explicit", 0.9, "rating")]


@pytest.fixture()
def images(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, x_posts.ImageInfo]:
    """画像6枚(ref: img1〜img6)。ギャラリーの代わりに resolve を差し替える。"""
    root = tmp_path / "files"
    root.mkdir()
    infos: dict[str, x_posts.ImageInfo] = {}
    for n in range(1, 7):
        _png(root / f"p{n}.png")
        infos[f"img{n}"] = x_posts.ImageInfo(f"img{n}", f"p{n}.png", f"page {n}")
    monkeypatch.setattr(x_posts, "user_files", lambda: root)

    def fake_resolve(refs: Any) -> dict[str, x_posts.ImageInfo]:
        return {ref: infos[ref].__class__(**{**infos[ref].__dict__, "warnings": []}) for ref in refs if ref in infos}

    monkeypatch.setattr(x_posts, "resolve", fake_resolve)
    return infos


def test_weighted_length() -> None:
    assert x_api.weighted_length("abc") == 3
    assert x_api.weighted_length("あ" * 140) == 280
    assert x_api.weighted_length("見て https://example.com/very/long/path/that/is/long") == 4 + 1 + 23


def test_thread_posts_in_order_and_strips_metadata(images: dict[str, Any]) -> None:
    parts = [
        x_posts.PartInput("新作です (1/2)", ["img1", "img2", "img3", "img4"]),
        x_posts.PartInput("(2/2)", ["img5", "img6"]),
    ]
    post = x_posts.create_post("漫画", parts, sensitive=False, scheduled_at=None, scorer=_safe)
    assert post["status"] == x_posts.SCHEDULED and not post["sensitive"]
    fake = FakeX()
    assert x_posts.tick(client=fake) == 1  # type: ignore[arg-type]
    done = x_posts.get_post(post["id"])
    assert done is not None and done["status"] == x_posts.POSTED
    assert done["tweet_ids"] == ["t1", "t2"]
    assert done["urls"][0] == "https://x.com/tester/status/t1"
    assert fake.posts[0]["reply_to"] is None and fake.posts[1]["reply_to"] == "t1"
    assert fake.posts[0]["media"] == ["m1", "m2", "m3", "m4"]
    assert all(not sensitive for _, sensitive in fake.metadata)
    for data, mime in fake.uploads:
        assert mime == "image/png"
        with Image.open(io.BytesIO(data)) as image:
            assert "Comment" not in image.info and "A" not in image.getbands()


def test_adult_image_forces_sensitive(images: dict[str, Any]) -> None:
    post = x_posts.create_post("", [x_posts.PartInput("", ["img1"])], sensitive=False, scheduled_at=None, scorer=_explicit)
    assert post["sensitive"]
    fake = FakeX()
    x_posts.tick(client=fake)  # type: ignore[arg-type]
    assert fake.metadata == [("m1", True)]


def test_adult_image_with_minor_character_is_rejected(images: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    images["img1"].story_id = 5
    monkeypatch.setattr(x_posts, "_character_ids", lambda conn, info: [9] if info.story_id == 5 else [])
    monkeypatch.setattr(db, "get_character", lambda conn, cid: {"id": cid, "name": "ひな", "is_minor": 1})
    with pytest.raises(x_posts.PostRejected) as caught:
        x_posts.create_post("", [x_posts.PartInput("", ["img1"])], sensitive=True, scheduled_at=None, scorer=_explicit)
    assert "未成年" in caught.value.reasons[0]
    assert x_posts.list_posts() == []
    # 全年齢の画像なら、未成年のキャラが出ていても投稿できる
    post = x_posts.create_post("", [x_posts.PartInput("", ["img1"])], sensitive=False, scheduled_at=None, scorer=_safe)
    assert post["status"] == x_posts.SCHEDULED
    assert minor_guard.MINOR == "minor"


def test_failure_mid_thread_resumes_on_retry(images: dict[str, Any]) -> None:
    parts = [x_posts.PartInput(f"({n}/3)", [f"img{n}"]) for n in (1, 2, 3)]
    post = x_posts.create_post("", parts, sensitive=False, scheduled_at=None, scorer=_safe)
    fake = FakeX(fail_on_post=1)
    x_posts.tick(client=fake)  # type: ignore[arg-type]
    failed = x_posts.get_post(post["id"])
    assert failed is not None and failed["status"] == x_posts.FAILED
    assert failed["tweet_ids"] == ["t1"] and "1/3" in failed["error"]
    x_posts.set_status(post["id"], x_posts.SCHEDULED, scheduled_at=datetime.now(timezone.utc))
    x_posts.tick(client=fake)  # type: ignore[arg-type]
    done = x_posts.get_post(post["id"])
    assert done is not None and done["status"] == x_posts.POSTED
    assert done["tweet_ids"] == ["t1", "t2", "t3"]
    assert [p["reply_to"] for p in fake.posts] == [None, "t1", "t2"]


def test_schedule_waits_and_misses_when_late(images: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc)
    later = x_posts.create_post("", [x_posts.PartInput("later", [])], sensitive=False, scheduled_at=now + timedelta(hours=1), scorer=_safe)
    late = x_posts.create_post("", [x_posts.PartInput("late", [])], sensitive=False, scheduled_at=now - timedelta(hours=7), scorer=_safe)
    fake = FakeX()
    assert x_posts.tick(now=now, client=fake) == 0  # type: ignore[arg-type]
    assert fake.posts == []
    assert (x_posts.get_post(later["id"]) or {})["status"] == x_posts.SCHEDULED
    assert (x_posts.get_post(late["id"]) or {})["status"] == x_posts.MISSED
    assert x_posts.tick(now=now + timedelta(hours=1, minutes=1), client=fake) == 1  # type: ignore[arg-type]
    assert fake.posts[0]["text"] == "later"


def test_validation(images: dict[str, Any]) -> None:
    with pytest.raises(x_posts.PostRejected):
        x_posts.build_parts([x_posts.PartInput("", [])], scorer=_safe)
    with pytest.raises(x_posts.PostRejected):
        x_posts.build_parts([x_posts.PartInput("x", ["img1", "img2", "img3", "img4", "img5"])], scorer=_safe)
    with pytest.raises(x_posts.PostRejected):
        x_posts.build_parts([x_posts.PartInput("あ" * 141, [])], scorer=_safe)
    with pytest.raises(x_posts.PostRejected):
        x_posts.build_parts([x_posts.PartInput("x", ["missing"])], scorer=_safe)


def test_delete_remote_removes_whole_thread(images: dict[str, Any]) -> None:
    parts = [x_posts.PartInput("a", []), x_posts.PartInput("b", [])]
    post = x_posts.create_post("", parts, sensitive=False, scheduled_at=None, scorer=_safe)
    fake = FakeX()
    x_posts.tick(client=fake)  # type: ignore[arg-type]
    deleted = x_posts.delete_remote(post["id"], client=fake)  # type: ignore[arg-type]
    assert deleted is not None and deleted["status"] == x_posts.DELETED
    assert fake.deleted == ["t2", "t1"]


def test_only_key_owner_can_post_in_multi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_MODE", "multi")
    monkeypatch.delenv("X_POST_USER_ID", raising=False)
    with db.as_user(1):
        assert x_posts.is_owner()
    with db.as_user(2):
        assert not x_posts.is_owner()
    monkeypatch.setenv("APP_MODE", "single")
    assert x_posts.is_owner()


def test_large_image_becomes_jpeg_under_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(x_api, "MAX_IMAGE_BYTES", 20_000)
    noise = Image.effect_noise((256, 256), 80).convert("RGB")
    buffer = io.BytesIO()
    noise.save(buffer, "PNG")
    data, mime = x_posts.prepare_image(buffer.getvalue())
    assert mime == "image/jpeg" and len(data) <= 20_000
