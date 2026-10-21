"""
漫画の MCP ツールのうち、ページのコマ割り・背景を消す・シーンの状態/セリフ/生成サイズのツールが、バックエンドへ
正しいリクエストを送るかのテスト(バックエンドは呼ばず、送る中身を記録する)。

実行方法:
  uv run pytest tests/test_mcp_manga_tools.py -v
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import mcp_manga  # noqa: E402


class _FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., Any]] = {}

    def tool(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def register(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.tools[fn.__name__] = fn
            return fn

        return register


@pytest.fixture()
def tools(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, str, Any]] = []
    responses: dict[tuple[str, str], Any] = {}

    async def fake_call(method: str, path: str, body: Any = None, timeout: float = 120) -> Any:
        calls.append((method, path, body))
        return responses.get((method, path.split("?")[0]), {})

    monkeypatch.setattr(mcp_manga, "_call", fake_call)
    mcp = _FakeMCP()
    mcp_manga.register_manga_tools(mcp)  # type: ignore[arg-type]
    mcp.calls = calls  # type: ignore[attr-defined]
    mcp.responses = responses  # type: ignore[attr-defined]
    return mcp


def test_edit_scenes_changes_only_the_given_scenes(tools: _FakeMCP) -> None:
    scene = {
        "id": 1, "title": "a", "text": "「やあ」", "prompt_tags": "x", "narration": None, "sfx": None, "page_index": 0,
        "characters": [{"id": 7, "name": "栄子", "action_tags": "", "outfit_tags": "", "state_id": None}],
        "panel_size": None, "dialogue": [{"text": "やあ", "speaker_id": 7}], "dialogue_custom": False,
    }
    tools.responses[("GET", "/api/story/5/edit")] = {  # type: ignore[attr-defined]
        "title": "t", "panels_per_page": 4, "custom_pages": False,
        "scenes": [scene, {**scene, "id": 2, "dialogue_custom": True}],
    }
    asyncio.run(tools.tools["manga_edit_scenes"](5, {0: {"panel_size": "landscape", "dialogue": [{"text": "おはよう", "speaker_id": 7}]}}))
    method, path, body = tools.calls[-1]  # type: ignore[attr-defined]
    assert (method, path) == ("PUT", "/api/story/5/edit")
    first, second = body["scenes"]
    assert first["panel_size"] == "landscape" and first["dialogue"] == [{"text": "おはよう", "speaker_id": 7}]
    # 渡していないシーンは、決めていたセリフはそのまま
    assert second["panel_size"] is None and second["dialogue"] == [{"text": "やあ", "speaker_id": 7}]


def test_update_scene_sends_outfits_and_states(tools: _FakeMCP) -> None:
    with pytest.raises(ValueError):
        asyncio.run(tools.tools["manga_update_scene"](3, character_states={7: 1}))
    asyncio.run(tools.tools["manga_update_scene"](3, character_ids=[7], character_outfits={7: "yukata"}, character_states={7: 1}))
    method, path, body = tools.calls[-1]  # type: ignore[attr-defined]
    assert (method, path) == ("PUT", "/api/story/scenes/3/characters")
    assert body == {"character_ids": [7], "outfits": {7: "yukata"}, "states": {7: 1}}


def test_layout_and_cutout_tools(tools: _FakeMCP, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [{"tree": "panel", "gaps": {"margin": 0}, "floats": [{"rect": [0.1, 0.1, 0.5, 0.5], "cutout": {"outline": True}}]}]
    asyncio.run(tools.tools["manga_save_page_layout"](9, pages, "tri3"))
    assert tools.calls[-1] == ("PUT", "/api/manga-v2/9/page-layout", {"template": "tri3", "pages": pages})  # type: ignore[attr-defined]
    asyncio.run(tools.tools["manga_reset_page_layout"](9))
    assert tools.calls[-1][:2] == ("DELETE", "/api/manga-v2/9/page-layout?template=grid4")  # type: ignore[attr-defined]

    from PIL import Image

    (tmp_path / "outputs").mkdir()
    Image.new("RGBA", (10, 10)).save(tmp_path / "outputs" / "p.cutout-preview.png")
    monkeypatch.setattr(mcp_manga, "_USER_ROOT", tmp_path)
    tools.responses[("POST", "/api/manga-v2/scenes/4/cutout/novelai")] = {"preview_path": "outputs/p.cutout-preview.png"}  # type: ignore[attr-defined]
    content = asyncio.run(tools.tools["manga_cutout"](4, "novelai"))
    assert tools.calls[-1][:2] == ("POST", "/api/manga-v2/scenes/4/cutout/novelai") and content[1].type == "image"  # type: ignore[attr-defined]
    with pytest.raises(ValueError):
        asyncio.run(tools.tools["manga_cutout"](4, "magic"))


def test_generate_panels_passes_manga_style(tools: _FakeMCP) -> None:
    tools.responses[("GET", "/api/story/2")] = {"scenes": [{"scene_index": 0}], "manga_v2_compose_settings": None}  # type: ignore[attr-defined]
    tools.responses[("GET", "/api/story/2/job")] = {"status": "done", "message": "ok"}  # type: ignore[attr-defined]
    tools.responses[("GET", "/api/manga-v2/2/panels")] = []  # type: ignore[attr-defined]
    asyncio.run(tools.tools["manga_generate_panels"](2, manga_style=False, color=True))
    post = next(c for c in tools.calls if c[0] == "POST")  # type: ignore[attr-defined]
    assert post[2]["manga_style"] is False and post[2]["color"] is True
