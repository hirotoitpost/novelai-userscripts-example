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
  uv run python scripts/manage_users.py admin someone@example.com          # 管理者にする(開発管理者のページを使える)
  uv run python scripts/manage_users.py admin someone@example.com --remove # 管理者から外す
  uv run python scripts/manage_users.py adopt someone@example.com     # single のデータをその人に渡す(コピー)
  uv run python scripts/manage_users.py adopt someone@example.com --files-only   # ファイルだけ(DB は写さない)
  uv run python scripts/manage_users.py export someone@example.com    # その人のデータを ZIP に書き出す(data/exports/)
  uv run python scripts/manage_users.py delete someone@example.com    # その人を消す(取り消せない。確かめてから)
  uv run python scripts/manage_users.py audit --limit 50              # 監査ログ(新しい順)

adopt: single で使っていたデータを、その人の multi のデータとしてコピーする。DB(data/app.db)・ごみ箱(data/trash)と、
画像などのファイル(outputs/・data/imports/・data/stamps/)を data/users/{id}/ の下へ。元のデータは消さない。その人が
一度ログインしてから実行する。すでにその人の DB があれば断る(--force で上書き)。DB を先に写してあるときは
--files-only でファイルだけ写す(同じ名前のファイルは上書き)。ファイルが多いと数分かかる。

delete: 招待・利用者・セッションと、その人のフォルダ(DB とファイル)を消す。取り消せないので、先に export するかを聞き、
メールアドレスを打ち直してもらってから消す(--yes で聞かない)。バックアップに入っている分は残る。

ここでの操作は、監査ログに「cli」として残る。
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
    admin = sub.add_parser("admin", help="管理者にする(--remove で外す)")
    admin.add_argument("email")
    admin.add_argument("--remove", action="store_true", help="管理者から外す")
    adopt = sub.add_parser("adopt", help="single のデータをその人のデータとしてコピーする")
    adopt.add_argument("email")
    adopt.add_argument("--force", action="store_true", help="その人のデータがあっても上書きする")
    adopt.add_argument("--files-only", action="store_true", help="ファイルだけ写す(DB は写さない)")
    export = sub.add_parser("export", help="その人のデータを ZIP に書き出す")
    export.add_argument("email")
    delete = sub.add_parser("delete", help="その人を消す(データも。取り消せない)")
    delete.add_argument("email")
    delete.add_argument("--yes", action="store_true", help="確かめずに消す")
    audit = sub.add_parser("audit", help="監査ログ(新しい順)")
    audit.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    def log(action: str, **detail: object) -> None:
        email = getattr(args, "email", "")
        sessions.audit(action, actor="cli", target=sessions.actor_of(email) if email else "", detail=dict(detail))

    try:
        if args.command == "add":
            sessions.add_invite(args.email, args.note)
            log("invite_added")
            print("招待しました。")
        elif args.command == "adopt":
            print("コピーしています(ファイルが多いと数分かかります)…")
            user_id = sessions.adopt_legacy_data(args.email, force=args.force, files_only=args.files_only)
            log("data_adopted", user_id=user_id, files_only=args.files_only)
            print(f"コピーしました(利用者 {user_id})。動いているバックエンドはそのまま使えます。")
        elif args.command == "admin":
            if not sessions.set_admin(args.email, not args.remove):
                print("招待されていません(先に add で招待してください)。")
            else:
                log("admin_removed" if args.remove else "admin_added")
                print("管理者から外しました。" if args.remove else "管理者にしました(ログインし直さなくても効きます)。")
        elif args.command == "remove":
            if sessions.remove_invite(args.email):
                log("invite_removed")
                print("取り消しました。")
            else:
                print("招待されていません。")
        elif args.command == "export":
            print("書き出しています(ファイルが多いと数分かかります)…")
            path = sessions.export_user(args.email)
            log("data_exported", file=path.name)
            print(f"書き出しました: {path}")
        elif args.command == "delete":
            if not args.yes:
                answer = input("消す前に export で書き出しましたか? 書き出さずに消すと戻せません [y/N] ").strip().lower()
                if answer != "y":
                    print("やめました。先に export してください。")
                    return
                if input("確かめのため、消す人のメールアドレスをもう一度入れてください: ").strip().lower() != args.email.strip().lower():
                    print("メールアドレスが合わないので、やめました。")
                    return
            user_id = sessions.delete_user(args.email)
            log("user_deleted", user_id=user_id)
            print(f"消しました(利用者 {user_id})。動いているバックエンドの、その人のセッションも使えなくなります。")
        elif args.command == "audit":
            for row in reversed(sessions.list_audit(args.limit)):
                who = row["actor"] or (f"利用者 {row['user_id']}" if row["user_id"] else "")
                detail = " ".join(f"{k}={v}" for k, v in row["detail"].items())
                print(f"{row['created_at'][:19]}  {row['action']:<16} {who:<14} {row['target']}  {detail}  {row['ip']}")
        else:
            rows = sessions.list_invites()
            if not rows:
                print("招待した人はいません。")
            for row in rows:
                login = f"最後のログイン {row['last_login_at']}" if row["user_id"] else "まだログインしていない"
                role = "  管理者" if row["is_admin"] else ""
                print(f"{row['email_hash'][:12]}…  {row['note'] or '(メモなし)'}{role}  招待 {row['created_at']}  {login}")
    except (RuntimeError, sessions.AdoptError) as exc:  # APP_MASTER_KEY がない など
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
