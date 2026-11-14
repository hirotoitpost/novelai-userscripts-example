"""
ヘルプの動画を録る「見本の環境」(make_media.py から使う)。

本物のデータを映さないよう、アプリのコード(src/python)とビルドした画面(dist)だけを一時フォルダに写し、
見本の画像と設定だけを入れて動かす。リポジトリは公開なので、パソコンの利用者名が入ったパスも映さない:
写し先は X:\\backup・Y:\\backup・Z:\\backup と見せ、裏で一時フォルダに置き換える。空きの大きさも見本の値にする。

  <base>/app/     … 見本のアプリ(src/python・dist・data・outputs)
  <base>/drives/  … 写し先 X: Y: Z: の中身
"""

from __future__ import annotations

import shutil
import sys
from collections import namedtuple
from pathlib import Path

GIB = 1 << 30
Usage = namedtuple("Usage", "total used free")

# 見せる写し先 → (一時フォルダの名前、全体 GB、空き GB、ドライブの番号)
DRIVES: dict[str, tuple[str, int, int, int]] = {
    "X:": ("x", 500, 180, 101),
    "Y:": ("y", 1000, 900, 102),
    "Z:": ("z", 2000, 1500, 103),
}
APP_USAGE = Usage(2000 * GIB, 800 * GIB, 1200 * GIB)
APP_DEVICE = 100


def prepare(base: Path, repo: Path) -> Path:
    """見本のアプリのフォルダを作り直す(コードと画面を写す)。アプリのフォルダを返す。"""
    if base.exists():
        shutil.rmtree(base)
    app = base / "app"
    shutil.copytree(repo / "src" / "python", app / "src" / "python", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(repo / "dist", app / "dist")
    for name in DRIVES.values():
        (base / "drives" / name[0]).mkdir(parents=True)
    return app


def use(app: Path) -> None:
    """見本のアプリのコードを読み込めるようにする(本物のアプリのフォルダを読まないよう、先頭に置く)。"""
    sys.path.insert(0, str(app / "src"))


def patch(base: Path) -> None:
    """写し先の場所・空き・ドライブの番号を、見本の値に差し替える。"""
    from python import backup, notices

    real = {letter: base / "drives" / info[0] for letter, info in DRIVES.items()}

    def to_real(path: str) -> str:
        text = path.strip()
        real_dir = real.get(text[:2].upper())
        return str(real_dir) + text[2:] if real_dir is not None else text

    original = backup.check_destination

    def check_destination(path: str) -> Path:
        return original(to_real(path))

    def letter_of(path: Path) -> str | None:
        resolved = Path(path).resolve()
        for letter, real_dir in real.items():
            if resolved == real_dir.resolve() or real_dir.resolve() in resolved.parents:
                return letter
        return None

    def usage(path: Path) -> Usage:
        letter = letter_of(path)
        if letter is None:
            return APP_USAGE
        _, total, free, _ = DRIVES[letter]
        return Usage(total * GIB, (total - free) * GIB, free * GIB)

    def device_of(path: Path) -> int:
        letter = letter_of(path)
        return DRIVES[letter][3] if letter else APP_DEVICE

    backup.check_destination = check_destination
    backup._disk_usage = usage
    backup._device_of = device_of
    notices._disk_usage = usage


def seed(app: Path, scenario: str) -> None:
    """見本の画像と設定を入れ、1回目のバックアップを取っておく。"""
    from PIL import Image

    from python import backup, db

    history = app / "outputs" / "history"
    history.mkdir(parents=True, exist_ok=True)
    for i in range(24):
        color = ((i * 37) % 256, (i * 91) % 256, (i * 53) % 256)
        Image.new("RGB", (96, 128), color).save(history / f"demo_{i:02d}.png")
    db.get_connection().close()

    def disk(letter: str, **kwargs: object) -> backup.Disk:
        return backup.register_disk(backup.Disk(f"{letter}\\backup", reserve_gb=20, reserve_percent=5, **kwargs))

    # 1台目(X:)だけで取ったあとに写し先を足した、という形にする(外す見本では、X: に全部の中身がある)
    settings = backup.Settings(disks=[disk("X:")], copies=2, auto=False)
    backup.save_settings(settings)
    backup.run_backup(settings, reason="手動")
    if scenario == "remove-disk":
        settings.disks += [disk("Y:"), disk("Z:")]
        backup.save_settings(settings)
        backup.run_backup(settings, reason="手動")
