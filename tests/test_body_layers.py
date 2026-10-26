"""
see-through の層から作る素体の2Dモデル(body_layers.py)と、リモートの see-through の呼び出し(see_through_remote.py)のテスト。
人物の切り出し・骨格・see-through のサーバーは使わず、作った人形を層に分けた PSD と、偽のサーバーの応答で確かめる。

実行方法:
  uv run pytest tests/test_body_layers.py -v
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import body_export, body_layers, see_through_remote  # noqa: E402
from test_body_rig import P, _doll  # noqa: E402


def _layer(mask: np.ndarray, color: tuple[int, int, int]) -> Image.Image:
    rgba = np.zeros((*mask.shape, 4), np.uint8)
    rgba[mask] = (*color, 255)
    return Image.fromarray(rgba, "RGBA")


def _psd(tmp_path: Path, mask: np.ndarray) -> tuple[Path, dict[str, np.ndarray]]:
    """人形を、後ろ髪・上の服(腰の少し上まで)・脚(腰の少し下から)・前髪に分けた PSD。腰の帯(270〜300)はどの層にも入らない。"""
    from psd_tools import PSDImage
    from psd_tools.api.layers import PixelLayer

    h, w = mask.shape
    rows = np.arange(h)[:, None]
    parts = {
        "back hair": np.zeros_like(mask),
        "topwear": mask & (rows < 270),
        "legwear": mask & (rows >= 300),
        "front hair": mask & (rows < 40),
    }
    parts["back hair"][60:200, 150:250] = True
    psd = PSDImage.new("RGBA", (w, h))
    for name, color in (("back hair", (60, 40, 20)), ("topwear", (120, 120, 120)),
                        ("legwear", (110, 110, 110)), ("front hair", (70, 50, 30))):
        psd.append(PixelLayer.frompil(_layer(parts[name], color), psd, name, 0, 0))
    path = tmp_path / "layers.psd"
    psd.save(path)
    return path, parts


@pytest.fixture
def fake_detect(monkeypatch: pytest.MonkeyPatch):
    _, mask, points = _doll()
    from python import anime_seg, ugoira_rig

    monkeypatch.setattr(anime_seg, "person_mask", lambda _img: mask.astype(np.float32))
    skeleton = ugoira_rig.Skeleton(
        np.array([points.get(n, (0, 0)) for n in ugoira_rig.KEYPOINTS], np.float32),
        np.array([1.0 if n in points else 0.0 for n in ugoira_rig.KEYPOINTS], np.float32),
    )
    monkeypatch.setattr(ugoira_rig, "detect_pose", lambda _img, _box: skeleton)
    return mask


def test_load_layers_keeps_order_and_names(tmp_path: Path) -> None:
    _, mask, _ = _doll()
    path, _ = _psd(tmp_path, mask)
    size, layers = body_layers.load_layers(path)
    assert size == (400, 560)
    assert [name for name, _ in layers] == ["back hair", "topwear", "legwear", "front hair"]
    assert all(rgba.shape == (560, 400, 4) for _, rgba in layers)


def test_remainder_fills_the_band_no_layer_covers(tmp_path: Path, fake_detect: np.ndarray) -> None:
    image, mask, _ = _doll()
    source = tmp_path / "a_pose.png"
    image.save(source)
    path, _ = _psd(tmp_path, mask)
    size, layers = body_layers.load_layers(path)
    rest = body_layers.remainder_layer(source, size, layers)
    assert rest is not None
    visible = rest[..., 3] > 0
    # 腰の帯(270〜300)の体は入り、背景と、層のある所の奥(胸・すね)は入らない
    assert visible[285, 200] and visible[285, 180]
    assert not visible[5, 5] and not visible[200, 200] and not visible[450, 168]


def test_build_from_layers_writes_rig_and_exports(tmp_path: Path, fake_detect: np.ndarray) -> None:
    image, mask, _ = _doll()
    source = tmp_path / "a_pose.png"
    image.save(source)
    path, _ = _psd(tmp_path, mask)
    rig = body_layers.build_from_layers(path, tmp_path / "rig", hair=False, source=source)
    names = [m["name"] for m in sorted(rig["meshes"], key=lambda m: m["z"])]
    # 残りの層は後ろ髪のすぐ前(体の一部)
    assert names == ["back hair", "remainder", "topwear", "legwear", "front hair"]
    assert rig["kind"] == "see_through" and rig["size"] == [400, 560]
    by_name = {m["name"]: m for m in rig["meshes"]}
    assert by_name["front hair"]["bones"] == ["head"]
    for mesh in rig["meshes"]:
        weights = np.array(mesh["weights"])
        assert weights.shape == (len(mesh["vertices"]), len(mesh["bones"]))
        assert np.allclose(weights.sum(axis=1), 1.0, atol=1e-3)
        assert (tmp_path / "rig" / mesh["texture"]).is_file()
    # 残りの層の腰より上は胴だけで動く(上の服の層と離れない)
    remainder = by_name["remainder"]
    verts, weights = np.array(remainder["vertices"]), np.array(remainder["weights"])
    hip = (P["left_hip"][1] + P["right_hip"][1]) / 2
    above = verts[:, 1] < hip - 8
    assert above.any() and (weights[above][:, remainder["bones"].index("torso")] > 0.99).all()
    saved = json.loads((tmp_path / "rig" / "rig.json").read_text(encoding="utf-8"))
    assert saved["kind"] == "see_through"

    # PSD は層ごとに1枚(奥から)、Spine にはメッシュごとのスロット
    from psd_tools import PSDImage

    out = body_export.export_all(tmp_path / "rig")
    assert [layer.name for layer in PSDImage.open(out["model.psd"])] == [
        "back_hair", "remainder", "topwear", "legwear", "front_hair",
    ]
    with zipfile.ZipFile(out["spine.zip"]) as archive:
        skeleton = json.loads(archive.read(next(n for n in archive.namelist() if n.endswith(".json"))))
    assert {s["name"] for s in skeleton["slots"]} >= {"remainder", "topwear", "front hair"}


def _zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("layers.psd", b"psd")
    return buffer.getvalue()


def test_decompose_posts_a_job_and_polls_until_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEE_THROUGH_BASE_URL", "https://example.test/")
    monkeypatch.setenv("SEE_THROUGH_TOKEN", "secret")
    monkeypatch.setattr(see_through_remote, "POLL", 0)
    states = iter(["queued", "running", "done"])
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret"
        seen.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(200, json={"id": "abc"})
        if request.url.path.endswith("/result"):
            return httpx.Response(200, content=_zip())
        return httpx.Response(200, json={"status": next(states), "seconds": 1, "detail": None})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(see_through_remote.httpx, "post", lambda url, **kw: httpx.Client(transport=transport).post(url, **kw))
    monkeypatch.setattr(see_through_remote.httpx, "get", lambda url, **kw: httpx.Client(transport=transport).get(url, **kw))
    image = tmp_path / "a.png"
    Image.new("RGB", (8, 8)).save(image)
    statuses: list[str] = []
    out = see_through_remote.decompose(image, tmp_path / "out" / "layers.zip", on_status=lambda s, _t: statuses.append(s))
    assert zipfile.ZipFile(out).read("layers.psd") == b"psd"
    assert statuses == ["queued", "running", "done"]
    assert seen[0] == ("POST", "/jobs") and seen[-1] == ("GET", "/jobs/abc/result")


def test_decompose_reports_server_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEE_THROUGH_BASE_URL", "https://example.test")
    monkeypatch.setenv("SEE_THROUGH_TOKEN", "secret")
    monkeypatch.setattr(see_through_remote, "POLL", 0)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "abc"})
        return httpx.Response(200, json={"status": "error", "seconds": 3, "detail": "CUDA out of memory"})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(see_through_remote.httpx, "post", lambda url, **kw: httpx.Client(transport=transport).post(url, **kw))
    monkeypatch.setattr(see_through_remote.httpx, "get", lambda url, **kw: httpx.Client(transport=transport).get(url, **kw))
    image = tmp_path / "a.png"
    Image.new("RGB", (8, 8)).save(image)
    with pytest.raises(see_through_remote.SeeThroughError, match="CUDA out of memory"):
        see_through_remote.decompose(image, tmp_path / "layers.zip")


def test_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEE_THROUGH_BASE_URL", raising=False)
    monkeypatch.delenv("SEE_THROUGH_TOKEN", raising=False)
    assert not see_through_remote.configured()
    with pytest.raises(see_through_remote.SeeThroughError):
        see_through_remote.health()
