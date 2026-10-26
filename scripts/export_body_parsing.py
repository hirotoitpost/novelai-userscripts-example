"""
体の部位分けのモデル(src/python/body_parsing.py)を作る。see-through の SAM Body Parsing(24yearsold/l2d_sam_iter2、
Apache-2.0)の重みと、see-through のコード(モデルの組み立て)を取ってきて、ONNX に書き出す。

アプリには torch を入れないので、一時的な環境で動かす(リポジトリのルートで):

    uv run --no-project --with torch --with torchvision --with einops --with timm --with numpy --with pillow \\
        --with opencv-python --with accelerate --with huggingface_hub --with safetensors --with packaging --with requests \\
        --with omegaconf --with pyyaml --with matplotlib --with onnx --with onnxruntime \\
        python scripts/export_body_parsing.py

作業用のフォルダ(既定は data/models/body-parsing/work。重み 1.3GB と see-through のコード)は、書き出したあと消してよい。
書き出したモデルは data/models/body-parsing/body_parsing.onnx(+ .data、約 2.6GB)。この PC の CPU で 1 枚 20 秒ほど。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "models" / "body-parsing"
REPO = "https://github.com/mitmul/see-through.git"
WEIGHTS = "https://huggingface.co/24yearsold/l2d_sam_iter2/resolve/main/checkpoint-18000.pt"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, default=OUT / "work")
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    repo = args.work / "see-through"
    if not repo.is_dir():
        subprocess.run(["git", "clone", "--depth", "1", REPO, str(repo)], check=True)
    ckpt = args.work / "checkpoint-18000.pt"
    if not ckpt.is_file():
        import requests

        with requests.get(WEIGHTS, stream=True, timeout=600) as response:
            response.raise_for_status()
            with open(ckpt.with_suffix(".part"), "wb") as f:
                for chunk in response.iter_content(1 << 20):
                    f.write(chunk)
        ckpt.with_suffix(".part").rename(ckpt)

    import onnx
    import torch

    sys.path.insert(0, str(repo / "common"))
    from modules.semanticsam import SemanticSam

    model = SemanticSam(class_num=19)
    model.load_state_dict(torch.load(ckpt, map_location="cpu", weights_only=True), strict=True)
    model.eval()

    class Core(torch.nn.Module):
        def __init__(self, m: torch.nn.Module) -> None:
            super().__init__()
            self.m = m

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            logits, _ = self.m(x)
            return logits

    raw = args.work / "raw" / "body_parsing.onnx"
    raw.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(Core(model).eval(), torch.zeros(1, 3, 1024, 1024), str(raw), input_names=["image"],
                      output_names=["logits"], opset_version=17, dynamo=False)
    # 重みを1つのファイルにまとめる(書き出しは重みごとにファイルを分けるため)
    onnx.save_model(onnx.load(str(raw), load_external_data=True), str(OUT / "body_parsing.onnx"),
                    save_as_external_data=True, all_tensors_to_one_file=True, location="body_parsing.onnx.data",
                    size_threshold=1024)
    print(f"書き出しました: {OUT / 'body_parsing.onnx'}")


if __name__ == "__main__":
    main()
