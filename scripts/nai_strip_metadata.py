"""
NovelAI 生成画像からメタデータを抽出・分離するスクリプト。

Usage:
    python scripts/nai_strip_metadata.py image.png
    python scripts/nai_strip_metadata.py *.png -o ./output
    python scripts/nai_strip_metadata.py ./images/ -o ./output
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path
from typing import Union

import numpy as np
from PIL import Image


# ---------------------------------------------------------------------------
# Stealth pnginfo 読み取り (nai_meta.py の実装を自己完結形式で移植)
# ---------------------------------------------------------------------------

def _byteize(alpha: np.ndarray) -> np.ndarray:
    alpha = alpha.T.reshape((-1,))
    alpha = alpha[: (alpha.shape[0] // 8) * 8]
    alpha = np.bitwise_and(alpha, 1)
    alpha = alpha.reshape((-1, 8))
    return np.packbits(alpha, axis=1)


class _LSBExtractor:
    def __init__(self, data: np.ndarray) -> None:
        self.data = _byteize(data[..., -1])
        self.pos = 0

    def _read_bytes(self, n: int) -> bytearray:
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return bytearray(chunk)

    def _read_uint32(self) -> int | None:
        raw = self._read_bytes(4)
        return int.from_bytes(raw, "big") if len(raw) == 4 else None


def extract_metadata(image: Union[Image.Image, np.ndarray]) -> dict:
    """アルファチャンネル LSB から stealth pnginfo を取り出して返す。"""
    if isinstance(image, Image.Image):
        image = np.array(image.convert("RGBA"))

    if image.shape[-1] != 4 or image.ndim != 3:
        raise ValueError("RGBA 画像が必要です")

    reader = _LSBExtractor(image)
    magic = "stealth_pngcomp"
    read_magic = reader._read_bytes(len(magic)).decode("utf-8")
    if magic != read_magic:
        raise ValueError(f"stealth pnginfo マジックナンバーが一致しません: {read_magic!r}")

    data_len = reader._read_uint32()
    if data_len is None:
        raise ValueError("データ長の読み取りに失敗しました")

    json_bytes = reader._read_bytes(data_len // 8)
    meta: dict = json.loads(gzip.decompress(json_bytes).decode("utf-8"))

    # Comment フィールドが JSON 文字列のときはデコード
    if "Comment" in meta and isinstance(meta["Comment"], str):
        meta["Comment"] = json.loads(meta["Comment"])

    return meta


# ---------------------------------------------------------------------------
# クリーン画像の生成
# ---------------------------------------------------------------------------

def make_clean_image(image: Image.Image) -> Image.Image:
    """アルファチャンネルの LSB をすべてクリア (0xFF に統一) した画像を返す。

    NAI 生成画像のアルファは元々すべて 0xFE か 0xFF なので、
    LSB を 1 にするだけで視覚的変化はない。
    """
    arr = np.array(image.convert("RGBA"))
    arr[..., 3] = 0xFF  # alpha を完全不透明に統一し LSB データを消去
    return Image.fromarray(arr)


# ---------------------------------------------------------------------------
# ファイル処理
# ---------------------------------------------------------------------------

def process_file(
    src: Path,
    output_dir: Path,
    save_meta: bool,
    save_clean: bool,
) -> tuple[bool, str]:
    """1 枚の PNG を処理する。(成功フラグ, メッセージ) を返す。"""
    try:
        image = Image.open(src)
    except Exception as e:
        return False, f"画像を開けませんでした: {e}"

    meta: dict | None = None
    if save_meta:
        try:
            meta = extract_metadata(image)
        except Exception as e:
            return False, f"メタデータ抽出失敗: {e}"

        meta_path = output_dir / f"{src.stem}.json"
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if save_clean:
        clean_image = make_clean_image(image)
        clean_path = output_dir / f"{src.stem}_clean.png"
        # pnginfo を渡さないことで PNG テキストチャンクも除去
        clean_image.save(clean_path, "PNG")

    parts = []
    if save_meta and meta is not None:
        parts.append(f"メタデータ → {output_dir / (src.stem + '.json')}")
    if save_clean:
        parts.append(f"クリーン画像 → {output_dir / (src.stem + '_clean.png')}")

    return True, " / ".join(parts)


def collect_inputs(paths: list[str]) -> list[Path]:
    """引数リストからディレクトリ・glob・直接ファイルをまとめて PNG リストに変換する。"""
    result: list[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            result.extend(sorted(path.glob("*.png")))
        elif path.exists():
            result.append(path)
        else:
            # glob 展開 (シェルが展開しない場合)
            expanded = sorted(Path(".").glob(p))
            if expanded:
                result.extend(expanded)
            else:
                print(f"[警告] 見つかりません: {p}", file=sys.stderr)
    return result


# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="NovelAI 生成画像からメタデータを抽出・分離します"
    )
    parser.add_argument("inputs", nargs="+", help="入力 PNG ファイル、またはディレクトリ")
    parser.add_argument(
        "-o", "--output-dir",
        help="出力先ディレクトリ (省略時: 入力ファイルと同じ場所)",
    )
    parser.add_argument(
        "--no-meta", action="store_true", help="メタデータ JSON を保存しない"
    )
    parser.add_argument(
        "--no-clean", action="store_true", help="クリーン画像を保存しない"
    )
    args = parser.parse_args()

    if args.no_meta and args.no_clean:
        parser.error("--no-meta と --no-clean を同時に指定することはできません")

    files = collect_inputs(args.inputs)
    if not files:
        print("処理対象の PNG ファイルが見つかりませんでした", file=sys.stderr)
        sys.exit(1)

    ok = 0
    ng = 0
    for src in files:
        out_dir = Path(args.output_dir) if args.output_dir else src.parent
        out_dir.mkdir(parents=True, exist_ok=True)

        success, msg = process_file(
            src,
            out_dir,
            save_meta=not args.no_meta,
            save_clean=not args.no_clean,
        )
        status = "OK" if success else "NG"
        print(f"[{status}] {src.name}: {msg}")
        if success:
            ok += 1
        else:
            ng += 1

    print(f"\n完了: {ok} 件成功 / {ng} 件失敗")
    if ng:
        sys.exit(1)


if __name__ == "__main__":
    main()
