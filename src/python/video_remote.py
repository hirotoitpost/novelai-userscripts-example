"""
リモートの GPU(Colab・GCP など)で動かす ComfyUI に、画像から動画を作らせる(うごイラの F。動画モデルで動かす)。

この PC には動画モデル(Wan など)を動かせる GPU が無いので、ComfyUI を GPU のあるマシンで起動し、その HTTP API を呼ぶ。
接続先は .env の VIDEO_GEN_BASE_URL(例: Colab の cloudflared の URL)。ComfyUI 自体には認証が無いので、外に出すときは
scripts/comfy_auth_proxy.py を前に置き、VIDEO_GEN_TOKEN を Bearer で送る(docs/video_remote_jp.md)。

流れ:
  1. POST /upload/image   元の絵を送る(ComfyUI の input フォルダに入る)
  2. POST /prompt         ワークフロー(API 形式の JSON)を送る。{{image}} などの置き換え用の文字を、送る前に値にする
  3. GET  /history/{id}   終わるまで待つ(数分かかる)
  4. GET  /view           出てきた動画(WebM・MP4)か絵(1コマずつの PNG、アニメ WebP・GIF)を受け取り、コマに分ける

ワークフローは assets/comfyui/ に置く(Wan 2.2 の画像から動画・最初と最後を同じ絵にして繰り返す)。ComfyUI の画面で
「Export (API)」した JSON の値を {{prompt}} などに書き換えれば、別のモデル・別の作り方にも差し替えられる。

通信量: 送るのは元の絵1枚(GCP・Colab への受信は無料)、受け取るのは動画1つ。既定のワークフローは VP9 の WebM で出す
(512×752・33コマで約 0.6MB。PNG だと 19MB で、Colab の cloudflared のトンネルが 約110KB/s しか出ず 3分かかった)。
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from PIL import Image, ImageSequence
from . import usage

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOW_DIR = _PROJECT_ROOT / "assets" / "comfyui"
# 作り方ごとのワークフロー。loop は最初と最後を元の絵にして、うごイラとして繰り返しても継ぎ目が出ないようにする。
# end は最初を元の絵、最後を別の絵(NovelAI で描き直したコマなど)にする。最後が決まっていると、途中も元の絵柄のまま
# 描かれる(自由に動かす i2v は、終わりに近づくほど線の陰影が減り、髪の色が変わった。docs/trials/2026-10-17_ugoira-video.md)
WORKFLOWS = {"i2v": "wan22_i2v_api.json", "loop": "wan22_loop_api.json", "end": "wan22_end_api.json"}
# Wan の動画は 16fps。コマ数は 4n+1(1秒 16コマ + 最初の1コマ)
FPS = 16
SECONDS = (2, 3, 5)
# Wan 2.2 14B の 480p の面積(832×480)。縦横は 16 の倍数
MAX_AREA = 832 * 480
_MULTIPLE = 16
# 待つ時間の上限(L4 で 5 秒・480p は 10〜20 分ほど)と、確かめる間隔
TIMEOUT = 45 * 60
POLL = 3.0
# 待っているあいだの問い合わせ・コマの受け取りで、一時的な失敗をやり直す回数と、間隔の単位(秒。回ごとに伸ばし、30 秒まで)
RETRIES = 8
RETRY_WAIT = 5.0
# コマを同時に受け取る数(1枚ずつ PNG を出すワークフローのとき)
DOWNLOADS = 6
_VIDEO_SUFFIXES = {".webm", ".mp4", ".mkv", ".mov"}
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
DEFAULT_NEGATIVE = (
    "flicker, jitter, static, frozen, blurry, distorted, deformed, bad anatomy, extra fingers, fused fingers, "
    "morphing face, worst quality, low quality, jpeg artifacts, text, watermark, subtitles"
)


class VideoRemoteError(RuntimeError):
    """リモートの ComfyUI が使えない・失敗した。"""


def base_url() -> str:
    return os.environ.get("VIDEO_GEN_BASE_URL", "").strip().rstrip("/")


def _headers() -> dict[str, str]:
    token = os.environ.get("VIDEO_GEN_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def frame_count(seconds: int) -> int:
    return FPS * seconds + 1


def video_size(size: tuple[int, int], max_area: int = MAX_AREA) -> tuple[int, int]:
    """元の絵の縦横比のまま、面積を max_area 以内に、縦横を 16 の倍数にする(小さい絵は大きくしない)。"""
    w, h = size
    scale = min(1.0, (max_area / (w * h)) ** 0.5)
    return max(_MULTIPLE, int(w * scale) // _MULTIPLE * _MULTIPLE), max(
        _MULTIPLE, int(h * scale) // _MULTIPLE * _MULTIPLE
    )


def video_prompt(motion: str, base: str) -> str:
    """動きの説明(自然な文が効く)を先に、元の絵のプロンプト(NovelAI の重み 1.4::…:: は外す)をあとに。"""
    cleaned = re.sub(r"-?\d+(\.\d+)?::|::", "", base or "")
    cleaned = ", ".join(t.strip() for t in cleaned.split(",") if t.strip())
    return ". ".join(p for p in (motion.strip(), cleaned) if p)


def load_workflow(kind: str) -> dict[str, Any]:
    name = WORKFLOWS.get(kind)
    if name is None:
        raise VideoRemoteError(f"知らない作り方です: {kind}")
    custom = os.environ.get(f"VIDEO_GEN_WORKFLOW_{kind.upper()}", "").strip()
    path = Path(custom) if custom else WORKFLOW_DIR / name
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    try:
        workflow = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VideoRemoteError(f"ワークフローを読めません({path}): {exc}") from exc
    # 説明用のキー(_comment など)は ComfyUI に送らない
    return {k: v for k, v in workflow.items() if not k.startswith("_")}


def fill(workflow: Any, values: dict[str, Any]) -> Any:
    """
    ワークフローの {{名前}} を値にする。文字がまるごと {{seed}} なら、値の型のまま(数は数として)入れる。
    知らない名前が残っていたら止める(送ってから ComfyUI で分かりにくい失敗になるので)。
    """
    if isinstance(workflow, dict):
        return {k: fill(v, values) for k, v in workflow.items()}
    if isinstance(workflow, list):
        return [fill(v, values) for v in workflow]
    if isinstance(workflow, str):
        whole = _PLACEHOLDER.fullmatch(workflow)
        if whole:
            if whole.group(1) not in values:
                raise VideoRemoteError(
                    f"ワークフローの {workflow} に入れる値がありません。"
                )
            return values[whole.group(1)]

        def replace(match: re.Match[str]) -> str:
            if match.group(1) not in values:
                raise VideoRemoteError(
                    f"ワークフローの {match.group(0)} に入れる値がありません。"
                )
            return str(values[match.group(1)])

        return _PLACEHOLDER.sub(replace, workflow)
    return workflow


def _client() -> httpx.AsyncClient:
    url = base_url()
    if not url:
        raise VideoRemoteError(
            "動画モデルの接続先が設定されていません。.env に VIDEO_GEN_BASE_URL(Colab・GCP で起動した ComfyUI の URL)を書いてください。"
        )
    return httpx.AsyncClient(
        base_url=url,
        headers=_headers(),
        timeout=httpx.Timeout(120, connect=15),
        follow_redirects=True,
    )


def _transient(response: httpx.Response) -> bool:
    """トンネル(cloudflared など)や中継が一時的に返すエラー。GPU 側では作り続けているので、待ってからやり直す。"""
    return response.status_code in (408, 429) or 500 <= response.status_code < 600


async def _get_retrying(
    client: httpx.AsyncClient,
    path: str,
    what: str,
    report: Callable[[str], Awaitable[None]],
    **kwargs: Any,
) -> httpx.Response:
    """
    GET する。接続切れ・タイムアウト・5xx は、RETRIES 回まで間を空けてやり直す(合言葉の誤り 401・403 などは、
    やり直しても変わらないのですぐ返す)。待っているあいだに1回失敗しただけで、作りかけの動画を捨てないように。
    """
    for attempt in range(RETRIES + 1):
        try:
            response = await client.get(path, **kwargs)
        except httpx.TransportError as exc:
            if attempt == RETRIES:
                raise VideoRemoteError(
                    f"{what}: つながらなくなりました({exc})。"
                ) from exc
        else:
            if not _transient(response) or attempt == RETRIES:
                return response
        await report(
            f"接続が途切れました。待ってからやり直しています({attempt + 1}/{RETRIES})"
        )
        await asyncio.sleep(min(RETRY_WAIT * (attempt + 1), 30.0))
    raise AssertionError("ここには来ない")


def _check(response: httpx.Response, what: str) -> Any:
    if response.status_code in (401, 403):
        raise VideoRemoteError(
            f"{what}: 接続先に断られました(VIDEO_GEN_TOKEN が合っているか確かめてください)。"
        )
    if response.status_code >= 400:
        detail = response.text[:500]
        raise VideoRemoteError(f"{what}: HTTP {response.status_code} {detail}")
    return response.json() if response.content else None


async def status() -> dict[str, Any]:
    """接続先が設定されているか・つながるか・GPU の名前と空きメモリ(画面に出す)。"""
    if not base_url():
        return {
            "configured": False,
            "reachable": False,
            "message": "未設定(.env の VIDEO_GEN_BASE_URL)",
        }
    try:
        async with _client() as client:
            stats = _check(await client.get("/system_stats", timeout=10), "接続の確認")
    except (httpx.HTTPError, VideoRemoteError) as exc:
        return {
            "configured": True,
            "reachable": False,
            "message": f"つながりません: {exc}",
        }
    devices = (stats or {}).get("devices") or []
    gpu = devices[0] if devices else {}
    free = gpu.get("vram_free")
    name = gpu.get("name") or "GPU 不明"
    return {
        "configured": True,
        "reachable": True,
        "gpu": name,
        "vram_free_gb": round(free / 1024**3, 1)
        if isinstance(free, (int, float))
        else None,
        "message": f"つながりました({name})",
    }


def _frames_from(data: bytes, filename: str = "") -> list[Image.Image]:
    """受け取った絵をコマに分ける(PNG は1枚、アニメ WebP・GIF はコマごと、動画 WebM・MP4 は OpenCV で読む)。"""
    if Path(filename).suffix.lower() in _VIDEO_SUFFIXES:
        return _video_frames(data, Path(filename).suffix)
    with Image.open(io.BytesIO(data)) as opened:
        return [frame.convert("RGB") for frame in ImageSequence.Iterator(opened)]


def _video_frames(data: bytes, suffix: str) -> list[Image.Image]:
    import tempfile

    import cv2

    # OpenCV の動画の読み込みは、ファイルからしか読めない
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"video{suffix}"
        path.write_bytes(data)
        capture = cv2.VideoCapture(str(path))
        frames: list[Image.Image] = []
        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                frames.append(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
        finally:
            capture.release()
    if not frames:
        raise VideoRemoteError("受け取った動画を読めませんでした。")
    return frames


def _output_files(history: dict[str, Any]) -> list[dict[str, Any]]:
    """
    履歴の出力から、受け取る絵を出す順に並べる(ノードの番号順。SaveImage・SaveWEBM は images、ほかの動画の保存ノードは
    gifs・videos)。
    """
    files: list[dict[str, Any]] = []
    outputs = history.get("outputs") or {}
    for node_id in sorted(outputs, key=lambda k: (len(k), k)):
        node = outputs[node_id] or {}
        for key in ("images", "gifs", "videos"):
            for item in node.get(key) or []:
                if item.get("type", "output") == "output" and item.get("filename"):
                    files.append(item)
    return files


async def _upload(client: httpx.AsyncClient, image: Image.Image, size: tuple[int, int], what: str) -> str:
    """絵を動画の大きさにして ComfyUI の input フォルダへ送り、ワークフローの LoadImage に書く名前を返す。"""
    buf = io.BytesIO()
    image.convert("RGB").resize(size, Image.Resampling.LANCZOS).save(buf, "PNG")
    name = f"nai_ugoira_{uuid4().hex[:12]}.png"
    uploaded = _check(
        await client.post(
            "/upload/image",
            files={"image": (name, buf.getvalue(), "image/png")},
            data={"overwrite": "true"},
        ),
        what,
    )
    return "/".join(
        p for p in ((uploaded or {}).get("subfolder"), (uploaded or {}).get("name", name)) if p
    )


async def generate(*args: Any, **kwargs: Any) -> list[Image.Image]:
    """元の絵から動画を作らせる(_generate)。multi では利用者ごとの 1 日の本数と「同時に 1 つ」をかける(usage.py)。"""
    async with usage.slot("video"):
        return await _generate(*args, **kwargs)


async def _generate(
    source: Image.Image,
    prompt: str,
    *,
    kind: str = "loop",
    seconds: int = 3,
    seed: int = 1,
    negative: str | None = None,
    end: Image.Image | None = None,
    on_progress: Callable[[str], Awaitable[None] | None] | None = None,
) -> list[Image.Image]:
    """元の絵から動画を作らせて、コマ(元の絵の大きさに戻したもの)を返す。kind が end なら、end を最後の絵にする。"""
    if seconds not in SECONDS:
        raise VideoRemoteError(
            f"長さは {', '.join(map(str, SECONDS))} 秒から選んでください。"
        )
    if (kind == "end") != (end is not None):
        raise VideoRemoteError("最後の絵は、作り方が「最後の絵へ動かす」のときだけ渡します。")

    async def report(message: str) -> None:
        if on_progress is not None:
            result = on_progress(message)
            if asyncio.iscoroutine(result):
                await result

    width, height = video_size(source.size)
    workflow = load_workflow(kind)
    async with _client() as client:
        await report("リモートの GPU へ元の絵を送っています")
        image_name = await _upload(client, source, (width, height), "元の絵を送る")
        values: dict[str, Any] = {
            "image": image_name,
            "prompt": prompt,
            "negative": negative or DEFAULT_NEGATIVE,
            "seed": int(seed),
            "width": width,
            "height": height,
            "length": frame_count(seconds),
            "prefix": f"nai_ugoira/{uuid4().hex[:8]}",
        }
        if end is not None:
            values["end_image"] = await _upload(client, end, (width, height), "最後の絵を送る")
        queued = _check(
            await client.post(
                "/prompt",
                json={"prompt": fill(workflow, values), "client_id": uuid4().hex},
            ),
            "動画を頼む",
        )
        prompt_id = (queued or {}).get("prompt_id")
        if not prompt_id:
            errors = (queued or {}).get("node_errors") or queued
            raise VideoRemoteError(
                f"ComfyUI がワークフローを受け付けませんでした: {json.dumps(errors, ensure_ascii=False)[:500]}"
            )
        started = time.monotonic()
        history: dict[str, Any] | None = None
        while history is None:
            elapsed = time.monotonic() - started
            if elapsed > TIMEOUT:
                raise VideoRemoteError(
                    f"{TIMEOUT // 60} 分たっても終わりませんでした。"
                )
            await report(
                f"リモートの GPU で動画を作っています({int(elapsed // 60)}分{int(elapsed % 60):02d}秒)"
            )
            await asyncio.sleep(POLL)
            entry = (
                _check(
                    await _get_retrying(
                        client, f"/history/{prompt_id}", "進み具合", report
                    ),
                    "進み具合",
                )
                or {}
            ).get(prompt_id)
            if not entry:
                continue
            state = entry.get("status") or {}
            if state.get("status_str") == "error":
                messages = [
                    m[1]
                    for m in state.get("messages") or []
                    if m and m[0] == "execution_error"
                ]
                detail = (
                    messages[0].get("exception_message")
                    if messages and isinstance(messages[0], dict)
                    else "不明"
                )
                raise VideoRemoteError(f"ComfyUI で失敗しました: {detail}")
            if state.get("completed") or entry.get("outputs"):
                history = entry
        files = _output_files(history)
        if not files:
            raise VideoRemoteError(
                "ComfyUI から絵が出てきませんでした(ワークフローに SaveImage などの保存ノードがあるか確かめてください)。"
            )
        # トンネル越しに1枚ずつ順に取ると、1枚 約5秒かかった(L4・Colab、33枚で3分。実機で確認)。何枚かずつ同時に取る
        received = 0
        limit = asyncio.Semaphore(DOWNLOADS)

        async def fetch(item: dict[str, Any]) -> list[Image.Image]:
            nonlocal received
            params = {
                "filename": item["filename"],
                "subfolder": item.get("subfolder", ""),
                "type": "output",
            }
            async with limit:
                response = await _get_retrying(
                    client, "/view", "コマを受け取る", report, params=params
                )
            if response.status_code >= 400:
                raise VideoRemoteError(f"コマを受け取る: HTTP {response.status_code}")
            received += 1
            await report(f"コマを受け取っています({received}/{len(files)})")
            return _frames_from(response.content, item["filename"])

        await report(f"コマを受け取っています(0/{len(files)})")
        frames = [f for got in await asyncio.gather(*(fetch(i) for i in files)) for f in got]
    # 元の絵の大きさに戻す(漫画のコマへの嵌め込み・ほかのうごイラと大きさをそろえる)
    return [
        f.resize(source.size, Image.Resampling.LANCZOS) if f.size != source.size else f
        for f in frames
    ]
