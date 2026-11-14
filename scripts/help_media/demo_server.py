"""
見本の環境のバックエンド(make_media.py が別のプロセスで動かす)。ビルドした画面も同じポートで配る。

  python demo_server.py <base> <scenario> <port>
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import demo_env  # noqa: E402


def main() -> None:
    base, scenario, port = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    app = base / "app"
    # 本物の .env やトークンを使わない
    os.environ["APP_MODE"] = "single"
    os.environ["SERVE_FRONTEND"] = "1"
    for key in [k for k in os.environ if k.startswith(("NAI_", "NOVELAI_", "PERSISTENT_", "APP_ALLOWED"))]:
        del os.environ[key]
    os.environ["NAI_LOG_DIR"] = str(base / "logs")
    os.environ["NAI_GENERATION_LOG_DIR"] = str(base / "logs")
    os.chdir(app)
    demo_env.use(app)
    from python import server  # 見本のアプリのコード(本物のアプリのフォルダは読まない)

    assert Path(server.__file__).resolve().is_relative_to(app.resolve()), server.__file__
    demo_env.patch(base)
    demo_env.seed(app, scenario)

    import uvicorn

    uvicorn.run(server.app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
