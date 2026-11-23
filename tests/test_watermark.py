"""見えない透かし(watermark.py)と台帳(watermark_registry.py)。"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from python import publish, watermark, watermark_registry


def _picture(width: int = 512, height: int = 768, seed: int = 1) -> Image.Image:
    """ベタ塗りと線のある絵の代わり(平らな所・なめらかな所・細かい所を混ぜる)。"""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:height, 0:width]
    base = np.zeros((height, width, 3))
    base[..., 0] = 120 + 80 * np.sin(x / 37.0)
    base[..., 1] = 90 + 60 * np.cos(y / 23.0)
    base[..., 2] = 200
    base[(x // 64 + y // 64) % 2 == 0] = (230, 230, 240)  # 平らな所
    base += rng.normal(0, 6, base.shape) * ((x > width // 2)[..., None])  # 細かい所
    return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), "RGB")


def _jpeg(image: Image.Image, quality: int) -> Image.Image:
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality)
    return Image.open(io.BytesIO(buffer.getvalue())).convert("RGB")


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def test_payload_roundtrip_and_check() -> None:
    bits = watermark.encode_payload(0xDEADBEEF)
    assert watermark.decode_payload(bits) == 0xDEADBEEF
    bits[40] ^= 1
    assert watermark.decode_payload(bits) is None


def test_embed_is_invisible_and_readable_after_jpeg() -> None:
    image = _picture()
    marked = watermark.embed(image, 123456789)
    a = np.asarray(image, dtype=float)
    b = np.asarray(marked, dtype=float)
    psnr = 10 * np.log10(255**2 / ((a - b) ** 2).mean())
    assert psnr > 38
    assert watermark.extract(marked).mark_id == 123456789
    assert watermark.extract(_jpeg(marked, 85)).mark_id == 123456789
    assert watermark.extract(_jpeg(marked, 70)).mark_id == 123456789
    assert watermark.extract(image).mark_id is None


def test_other_key_cannot_read(monkeypatch: pytest.MonkeyPatch) -> None:
    marked = watermark.embed(_picture(), 42)
    monkeypatch.setenv("WATERMARK_KEY", "another-key")
    assert watermark.extract(marked).mark_id is None


def test_too_small_image_is_rejected() -> None:
    with pytest.raises(ValueError):
        watermark.embed(Image.new("RGB", (64, 64)), 1)


def test_phash_is_stable_under_recompression() -> None:
    image = _picture()
    resized = _jpeg(image.resize((256, 384), Image.Resampling.LANCZOS), 80)
    assert watermark_registry.distance(watermark_registry.phash(image), watermark_registry.phash(resized)) <= 6
    other = _picture(seed=2).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    assert watermark_registry.distance(watermark_registry.phash(image), watermark_registry.phash(other)) > 12


def test_mark_and_verify(tmp_path: Path) -> None:
    data = _png_bytes(_picture())
    marked = watermark_registry.mark(data, purpose=watermark_registry.PUBLICATION, source_path="a.png", label="本 / 001.png")
    marks = watermark_registry.list_marks()
    assert len(marks) == 1 and marks[0]["purpose_label"] == "出版物"
    mark_id = marks[0]["mark_id"]

    assert watermark_registry.verify(marked)["method"] == "exact"
    recompressed = _jpeg(Image.open(io.BytesIO(marked)), 80)
    result = watermark_registry.verify(_png_bytes(recompressed))
    assert result["found"] and result["method"] == "watermark" and result["mark_id"] == mark_id
    # 縮めた画像は、指紋の近い記録の大きさに戻して読む
    small = Image.open(io.BytesIO(marked)).resize((384, 576), Image.Resampling.LANCZOS)
    result = watermark_registry.verify(_png_bytes(_jpeg(small, 90)))
    assert result["found"] and result["method"] in ("watermark_resized", "similar")
    assert result["mark_id"] == mark_id
    # 出していない画像
    result = watermark_registry.verify(_png_bytes(_picture(seed=3).transpose(Image.Transpose.ROTATE_180)))
    assert not result["found"]


def test_x_post_retry_reuses_mark_id() -> None:
    data = _png_bytes(_picture())
    first = watermark_registry.mark(data, purpose=watermark_registry.X_POST, ref="history:1:0", x_post_id=7)
    second = watermark_registry.mark(data, purpose=watermark_registry.X_POST, ref="history:1:0", x_post_id=7)
    assert first == second
    assert len(watermark_registry.list_marks()) == 1


def test_publication_with_watermark(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publish, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(publish, "PUBLICATIONS_DIR", tmp_path / "pubs")
    (tmp_path / "img.png").write_bytes(_png_bytes(_picture()))
    built = publish.build(5, "本", [publish.Entry("images", "画像", images=["img.png"])], watermark=True)
    out = (tmp_path / str(built["dir"]) / "files" / "001.png").read_bytes()
    publish.check(out)
    reading = watermark.extract(Image.open(io.BytesIO(out)))
    marks = watermark_registry.list_marks()
    assert reading.mark_id == marks[0]["mark_id"] and marks[0]["publication_id"] == 5


def test_agreement_separates_right_id_when_bits_are_noisy() -> None:
    rng = np.random.default_rng(7)
    right = watermark.encode_payload(0x2468ACE0).astype(float) * 2 - 1
    # 弱い票(強さ 0.3)に雑音を足して、何ビットか逆になる状態を作る
    votes = right * 0.3 + rng.normal(0, 0.25, size=right.shape)
    reading = watermark.decide(votes)
    assert reading.mark_id is None or reading.mark_id == 0x2468ACE0
    assert watermark.agreement(reading, 0x2468ACE0) > 0.6
    others = [watermark.agreement(reading, int(m)) for m in rng.integers(1, 2**32 - 1, size=200)]
    assert max(others) < 0.6 and abs(float(np.mean(others))) < 0.1


def test_video_profile_block16_roundtrip() -> None:
    image = _picture(768, 1152)
    marked = watermark.embed(image, 99, watermark.VIDEO)
    assert watermark.extract(marked, watermark.VIDEO).mark_id == 99
    assert watermark.extract(marked).mark_id == 99  # 省くと全部の入れ方を試す
