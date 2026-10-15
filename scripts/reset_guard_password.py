"""
コンテンツガードのパスワードを消す(忘れたとき用)。消したあとは、管理ページで新しく設定し直す。

画面からは消せないようにしてあるので、この PC でだけ実行できる。パスワードが無いあいだは、守りを緩める変更は
できない(止める側には働く)。

実行方法:
  uv run python scripts/reset_guard_password.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from python import guard_password  # noqa: E402


def main() -> None:
    if not guard_password.is_set():
        print("パスワードは設定されていません。管理ページで設定してください。")
        return
    answer = input("コンテンツガードのパスワードを消します。よろしいですか? [y/N] ").strip().lower()
    if answer != "y":
        print("やめました。")
        return
    guard_password.clear()
    print("消しました。管理ページで新しいパスワードを設定してください(動いているバックエンドの合言葉は、再起動で消えます)。")


if __name__ == "__main__":
    main()
