"""
複数ユーザー(APP_MODE=multi)で使える人を招待する・取り消す・一覧する。docs/multi_user_plan_jp.md のステップ 2。

招待した人だけが、NovelAI のメールアドレスとパスワードでログインできる。メールアドレスそのものは保存せず、
.env の APP_MASTER_KEY から作った鍵で変換した値だけを持つ(一覧にもメールアドレスは出ない。誰を招待したかは
--note で残す)。そのため APP_MASTER_KEY を変えると、招待も利用者もすべて見分けられなくなる。

画面からは招待できないようにしてあるので、サーバーの PC でだけ実行できる。

実行方法:
  uv run python scripts/manage_users.py add someone@example.com --note "友人A"
  uv run python scripts/manage_users.py remove someone@example.com   # その人のセッションも消える(データは残る)
  uv run python scripts/manage_users.py list
  uv run python scripts/manage_users.py adopt someone@example.com     # single のデータをその人に渡す(コピー)

adopt: single で使っていたデータ(data/app.db・data/trash)を、その人の multi のデータとしてコピーする。元のデータは
消さない。その人が一度ログインしてから実行する。すでにその人のデータがあれば断る(--force で上書き)。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
load_dotenv(_ROOT / ".env")

from python import sessions  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="APP_MODE=multi で使える人を管理する")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="招待する")
    add.add_argument("email")
    add.add_argument("--note", default="", help="誰かのメモ(一覧に出る。メールアドレスは保存しない)")
    remove = sub.add_parser("remove", help="招待を取り消す")
    remove.add_argument("email")
    sub.add_parser("list", help="招待した人の一覧")
    adopt = sub.add_parser("adopt", help="single のデータをその人のデータとしてコピーする")
    adopt.add_argument("email")
    adopt.add_argument("--force", action="store_true", help="その人のデータがあっても上書きする")
    args = parser.parse_args()

    try:
        if args.command == "add":
            sessions.add_invite(args.email, args.note)
            print("招待しました。")
        elif args.command == "adopt":
            user_id = sessions.adopt_legacy_data(args.email, force=args.force)
            print(f"コピーしました(利用者 {user_id})。動いているバックエンドはそのまま使えます。")
        elif args.command == "remove":
            print("取り消しました。" if sessions.remove_invite(args.email) else "招待されていません。")
        else:
            rows = sessions.list_invites()
            if not rows:
                print("招待した人はいません。")
            for row in rows:
                login = f"最後のログイン {row['last_login_at']}" if row["user_id"] else "まだログインしていない"
                print(f"{row['email_hash'][:12]}…  {row['note'] or '(メモなし)'}  招待 {row['created_at']}  {login}")
    except (RuntimeError, sessions.AdoptError) as exc:  # APP_MASTER_KEY がない など
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
