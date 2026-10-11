"""
出版(パブリッシュ): 選んだ画像・本を、NovelAI の生成情報(メタデータ)を取り除いた「出版物」として別に保存し、
ZIP にまとめる。

NovelAI の画像には、生成の設定(プロンプト・シード値など)が2か所に入っている: PNG のテキスト(Comment など)と、
アルファチャンネルの最下位ビットに隠した埋め込み(Stealth)。出版物では両方を取り除く:
- 画素だけを新しい画像に写す(テキスト・EXIF・ICC などの付属情報を持ち越さない)
- アルファチャンネルは捨てる(隠した埋め込みごと消える)。絵の色は変えない。透明な部分のある画像だけ、白地に重ねる
取り除いたあと、もう一度読み取って、生成情報が残っていないことを確かめる(残っていたらエラーにして、出版物を作らない)。

元の画像・本はそのまま残す。出版物は outputs/publications/pub_<ID>/ に置く(files/ に画像、同じ場所に ZIP)。
ギャラリーには出ない。
"""

from __future__ import annotations

import io
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .nai_metadata import read_comment

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PUBLICATIONS_DIR = _PROJECT_ROOT / "outputs" / "publications"
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
# 画像の付属情報のうち、生成情報が入りうるもの(確かめるときに見る)
_INFO_KEYS = ("Comment", "Description", "Software", "Source", "Title", "Generation time", "parameters", "exif")
# これ以上のアルファだけの画像は、不透明として扱う
_OPAQUE_ALPHA = 254


class MetadataRemains(RuntimeError):
    """メタデータを取り除いたはずの画像に、生成情報が残っていた。"""


def safe_name(name: str, fallback: str) -> str:
    """ファイル名・フォルダ名に使える形にする(使えない文字を _ に。空なら fallback)。"""
    cleaned = _UNSAFE.sub("_", name).strip().strip(".")[:80].strip()
    return cleaned or fallback


def strip_metadata(data: bytes) -> bytes:
    """画像から生成情報を取り除いた PNG。取り除けなかった(残っていた)ら MetadataRemains。"""
    with Image.open(io.BytesIO(data)) as source:
        source.load()
        if source.mode in ("RGBA", "LA") or (source.mode == "P" and "transparency" in source.info):
            rgba = source.convert("RGBA")
            if rgba.getchannel("A").getextrema()[0] >= _OPAQUE_ALPHA:
                # ほぼ不透明(NovelAI の画像のアルファは 254 か 255 だけ): アルファを捨てるだけにする。
                # 白地に重ねると、アルファが 254 の画素の色が 1 だけ変わってしまう(実機で確認)
                clean = Image.new("RGB", rgba.size)
                clean.paste(rgba.convert("RGB"))
            else:
                # 本当に透明な部分がある画像は、白地に重ねる
                clean = Image.new("RGB", rgba.size, (255, 255, 255))
                clean.paste(rgba, mask=rgba.getchannel("A"))
        else:
            # 画素だけを新しい画像に写す(テキスト・EXIF・ICC などは持ち越さない)
            rgb = source.convert("RGB")
            clean = Image.new("RGB", rgb.size)
            clean.paste(rgb)
    buffer = io.BytesIO()
    clean.save(buffer, "PNG")
    result = buffer.getvalue()
    check(result)
    return result


def check(data: bytes) -> None:
    """生成情報が残っていないことを確かめる。残っていたら MetadataRemains。"""
    if read_comment(data) is not None:
        raise MetadataRemains("NovelAI の生成情報が残っています。")
    with Image.open(io.BytesIO(data)) as image:
        left = [key for key in _INFO_KEYS if image.info.get(key)]
        if left or "A" in image.getbands():
            raise MetadataRemains(f"画像の付属情報が残っています: {', '.join(left) or 'アルファチャンネル'}")


@dataclass
class Entry:
    """出版物に入れる1つのまとまり(画像のまとまり、または本1冊)。"""

    kind: str  # "images" | "book"
    title: str
    # 画像のファイル(リポジトリのルートからのパス)。この順に 001, 002… と名前を付ける
    images: list[str] = field(default_factory=list)
    # 漫画のページが無い本の本文
    text: str = ""
    # 本なら物語のID
    story_id: int | None = None


def _folder_names(entries: list[Entry]) -> list[str]:
    """まとまりごとのフォルダ名(ZIP の中)。まとまりが1つだけなら、フォルダに分けない。"""
    if len(entries) == 1:
        return [""]
    names: list[str] = []
    for index, entry in enumerate(entries, start=1):
        base = safe_name(entry.title, f"item{index}")
        name, suffix = base, 2
        while name in names:
            name = f"{base}_{suffix}"
            suffix += 1
        names.append(name)
    return names


def build(publication_id: int, title: str, entries: list[Entry]) -> dict[str, object]:
    """
    出版物を作る: 画像から生成情報を取り除いて pub_<ID>/files/ に保存し、ZIP にまとめる。
    戻り値は {dir, zip_path, bytes, file_count, items}(パスはリポジトリのルートから)。
    途中で失敗したら、作りかけのフォルダを消して例外を上げる。
    """
    root = PUBLICATIONS_DIR / f"pub_{publication_id}"
    files_dir = root / "files"
    shutil.rmtree(root, ignore_errors=True)
    files_dir.mkdir(parents=True)
    written: list[tuple[Path, str]] = []
    items: list[dict[str, object]] = []
    try:
        for entry, folder in zip(entries, _folder_names(entries)):
            target = files_dir / folder if folder else files_dir
            target.mkdir(parents=True, exist_ok=True)
            width = max(3, len(str(len(entry.images))))
            count = 0
            for index, path in enumerate(entry.images, start=1):
                source = _PROJECT_ROOT / path
                if not source.is_file():
                    continue
                name = f"{index:0{width}d}.png"
                (target / name).write_bytes(strip_metadata(source.read_bytes()))
                written.append((target / name, f"{folder}/{name}" if folder else name))
                count += 1
            if entry.text and not count:
                name = "本文.txt"
                # 改行は LF に固定する(write_text だと Windows では CRLF になる)
                (target / name).write_bytes(f"{entry.title}\n\n{entry.text}\n".encode("utf-8"))
                written.append((target / name, f"{folder}/{name}" if folder else name))
            items.append(
                {
                    "kind": entry.kind,
                    "title": entry.title,
                    "story_id": entry.story_id,
                    "files": count or (1 if entry.text else 0),
                }
            )
        if not written:
            raise ValueError("出版できるファイルがありません。")
        zip_file = root / f"{safe_name(title, f'publication{publication_id}')}.zip"
        # PNG は既に圧縮されているので、zip では圧縮し直さない
        with zipfile.ZipFile(zip_file, "w", zipfile.ZIP_STORED) as archive:
            for path, name in written:
                archive.write(path, name)
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return {
        "dir": root.relative_to(_PROJECT_ROOT).as_posix(),
        "zip_path": zip_file.relative_to(_PROJECT_ROOT).as_posix(),
        "bytes": zip_file.stat().st_size,
        "file_count": len(written),
        "items": items,
    }


def remove(publication_id: int) -> None:
    shutil.rmtree(PUBLICATIONS_DIR / f"pub_{publication_id}", ignore_errors=True)
