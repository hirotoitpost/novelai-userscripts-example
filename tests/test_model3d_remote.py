"""
素体の 3D の形を作るリモートのサーバー(scripts/model3d_server.py)と、それを呼ぶ model3d_remote のテスト(GPU は使わない)。

実行方法:
  uv run pytest tests/test_model3d_remote.py -v
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from python import model3d_remote  # noqa: E402


def _png(path: Path) -> Path:
    Image.new("RGB", (8, 8)).save(path)
    return path


def _mock(monkeypatch: pytest.MonkeyPatch, handler) -> None:  # noqa: ANN001
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(model3d_remote.httpx, "post", lambda url, **kw: httpx.Client(transport=transport).post(url, **kw))
    monkeypatch.setattr(model3d_remote.httpx, "get", lambda url, **kw: httpx.Client(transport=transport).get(url, **kw))


def test_generate_sends_the_views_and_polls_until_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL3D_BASE_URL", "https://example.test/")
    monkeypatch.setenv("MODEL3D_TOKEN", "secret")
    monkeypatch.setattr(model3d_remote, "POLL", 0)
    states = iter(["queued", "running", "done"])
    seen: list[tuple[str, str]] = []
    sent: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret"
        seen.append((request.method, request.url.path))
        if request.method == "POST":
            sent.append(request.read())
            return httpx.Response(200, json={"id": "abc"})
        if request.url.path.endswith("/result"):
            return httpx.Response(200, content=b"glTF")
        return httpx.Response(200, json={"status": next(states), "seconds": 1, "detail": None})

    _mock(monkeypatch, handler)
    views = {k: _png(tmp_path / f"{k}.png") for k in ("front", "left", "back")}
    statuses: list[str] = []
    out = model3d_remote.generate(views, tmp_path / "out" / "model.glb", on_status=lambda s, _t: statuses.append(s))
    assert out.read_bytes() == b"glTF"
    assert statuses == ["queued", "running", "done"]
    assert seen[0] == ("POST", "/jobs") and seen[-1] == ("GET", "/jobs/abc/result")
    # 3つの向きを、それぞれの名前の欄で送る
    assert all(f'name="{k}"'.encode() in sent[0] for k in ("front", "left", "back"))


def test_generate_reports_server_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL3D_BASE_URL", "https://example.test")
    monkeypatch.setenv("MODEL3D_TOKEN", "secret")
    monkeypatch.setattr(model3d_remote, "POLL", 0)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "abc"})
        return httpx.Response(200, json={"status": "error", "seconds": 3, "detail": "CUDA out of memory"})

    _mock(monkeypatch, handler)
    with pytest.raises(model3d_remote.Model3DError, match="CUDA out of memory"):
        model3d_remote.generate({"front": _png(tmp_path / "front.png")}, tmp_path / "model.glb")


def test_generate_needs_the_front_and_known_views(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL3D_BASE_URL", "https://example.test")
    monkeypatch.setenv("MODEL3D_TOKEN", "secret")
    image = _png(tmp_path / "a.png")
    with pytest.raises(model3d_remote.Model3DError, match="正面"):
        model3d_remote.generate({"left": image}, tmp_path / "model.glb")
    with pytest.raises(model3d_remote.Model3DError, match="right"):
        model3d_remote.generate({"front": image, "right": image}, tmp_path / "model.glb")


def test_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MODEL3D_BASE_URL", raising=False)
    monkeypatch.delenv("MODEL3D_TOKEN", raising=False)
    assert not model3d_remote.configured()
    with pytest.raises(model3d_remote.Model3DError):
        model3d_remote.health()


def _server(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN202
    monkeypatch.setenv("MODEL3D_TOKEN", "secret")
    spec = importlib.util.spec_from_file_location("model3d_server", ROOT / "scripts" / "model3d_server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_server_checks_the_token_and_runs_one_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)
    calls: list[tuple[list[str], dict]] = []

    def fake_generate(images: dict[str, bytes], out: Path, **options) -> None:  # noqa: ANN003
        calls.append((sorted(images), options))
        out.write_bytes(b"glTF")

    monkeypatch.setattr(server, "_generate", fake_generate)
    auth = {"Authorization": "Bearer secret"}
    with TestClient(server.app) as client:
        assert client.post("/jobs", files={"front": ("f.png", b"x")}).status_code == 401
        assert client.post("/jobs", headers=auth, files={"front": ("f.png", b"x")}, data={"steps": "500"}).status_code == 422
        response = client.post(
            "/jobs", headers=auth, files={"front": ("f.png", b"f"), "back": ("b.png", b"b")}, data={"seed": "7"},
        )
        job_id = response.json()["id"]
        for _ in range(100):
            status = client.get(f"/jobs/{job_id}", headers=auth).json()
            if status["status"] == "done":
                break
            time.sleep(0.01)
        assert status["status"] == "done"
        assert client.get(f"/jobs/{job_id}/result", headers=auth).content == b"glTF"
        assert client.get("/jobs/nothing", headers=auth).status_code == 404
    assert calls == [(["back", "front"], {"seed": 7, "steps": 50, "octree_resolution": 380})]


def test_server_reports_a_failed_job(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _server(monkeypatch)

    def broken(images: dict[str, bytes], out: Path, **options) -> None:  # noqa: ANN003
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(server, "_generate", broken)
    auth = {"Authorization": "Bearer secret"}
    with TestClient(server.app) as client:
        job_id = client.post("/jobs", headers=auth, files={"front": ("f.png", b"f")}).json()["id"]
        for _ in range(100):
            status = client.get(f"/jobs/{job_id}", headers=auth).json()
            if status["status"] == "error":
                break
            time.sleep(0.01)
        assert status["status"] == "error" and "CUDA out of memory" in status["detail"]
        assert client.get(f"/jobs/{job_id}/result", headers=auth).status_code == 404
