"""
ヘルプの動画を録り直す(public/help-media/<名前>.mp4)。画面が変わったら、これを動かせば同じ手順で録り直せる。

  uv run python scripts/help_media/make_media.py              # 全部
  uv run python scripts/help_media/make_media.py add-disk     # 1つだけ

やること(1つの動画ごと):
1. 見本の環境を一時フォルダに作る(demo_env.py。本物のデータは使わない)
2. 見本のバックエンドを別のポートで動かす(demo_server.py)
3. Playwright で画面を操作して録画する(record.mjs)
4. ffmpeg で mp4 にする

先に npm run build で画面をビルドしておくこと(dist を見本の環境に写す)。
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
OUT = REPO / "public" / "help-media"
SCENARIOS = ["add-disk", "remove-disk"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url: str, server: subprocess.Popen, timeout: float = 90) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server.poll() is not None:
            raise RuntimeError(f"見本のバックエンドが止まりました(終了コード {server.returncode})")
        try:
            with urllib.request.urlopen(url, timeout=3) as res:
                if res.status == 200:
                    return
        except OSError:
            pass
        time.sleep(1)
    raise TimeoutError(f"見本のバックエンドが起動しません: {url}")


def record(scenario: str) -> Path:
    if not (REPO / "dist" / "index.html").is_file():
        raise SystemExit("先に npm run build を実行してください。")
    import demo_env

    base = Path(tempfile.gettempdir()) / "nai-help-media" / scenario
    demo_env.prepare(base, REPO)
    port = _free_port()
    log = (base / "server.log").open("w", encoding="utf-8")
    server = subprocess.Popen(
        [sys.executable, str(HERE / "demo_server.py"), str(base), scenario, str(port)],
        stdout=log, stderr=subprocess.STDOUT,
    )
    try:
        url = f"http://127.0.0.1:{port}"
        _wait(f"{url}/api/admin/data-backup", server)
        webm = base / "video.webm"
        done = subprocess.run(["node", str(HERE / "record.mjs"), scenario, url, str(webm)], cwd=REPO)
        if done.returncode != 0:
            raise SystemExit(f"録画に失敗しました。画面: {webm}.png、見本のバックエンドのログ: {base / 'server.log'}")
    finally:
        server.terminate()
        server.wait(timeout=30)
        log.close()
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"backup-{scenario}.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(webm),
            "-vf", "fps=20,scale=trunc(iw/2)*2:trunc(ih/2)*2",
            # libopenh264: どの ffmpeg にも入っている H.264(libx264 は入っていない版がある)。スマホでもそのまま再生できる
            "-c:v", "libopenh264", "-b:v", "500k", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an",
            str(target),
        ],
        check=True,
    )
    shutil.rmtree(base, ignore_errors=True)
    return target


def main() -> None:
    sys.path.insert(0, str(HERE))
    wanted = sys.argv[1:] or SCENARIOS
    for scenario in wanted:
        if scenario not in SCENARIOS:
            raise SystemExit(f"知らない動画です: {scenario}(使えるもの: {', '.join(SCENARIOS)})")
        target = record(scenario)
        print(f"{target.relative_to(REPO)}: {target.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
