"""LoRA学習用データセットをNovelAI APIで生成するCLI。

ロジック本体は src/python/lora_dataset.py を共有利用する（GUI版と同じ生成処理）。
出力先: outputs/<root-name>/<character-id>/<category>/####.png + ####.txt
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

load_dotenv(_PROJECT_ROOT / ".env", override=True)

from novelai import AsyncNovelAI  # noqa: E402
from python.lora_dataset import GenConfig, build_shots, run_dataset  # noqa: E402

API_KEY = os.environ.get("NOVELAI_API_KEY") or os.environ.get("NOVELAI_API_TOKEN")
if not API_KEY:
    raise RuntimeError("NOVELAI_API_TOKEN が .env に設定されていません")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--character-id", default="cahrunna", help="管理用キャラクターID（フォルダ名）")
    parser.add_argument("--trigger-word", default="cahrunna_girl", help="LoRA学習用トリガーワード")
    parser.add_argument("--base-tags", default="1girl, solo, brown hair, medium hair, brown eyes")
    parser.add_argument("--extra-tags", default="", help="共通タグに追加するタグ（カンマ区切り）")
    parser.add_argument("--outfit-tag", default="white dress", help="上半身/全身カットの衣装タグ（空文字で無効化）")
    parser.add_argument("--root-name", default="training_data", help="outputs/ 配下のルートフォルダ名")
    parser.add_argument("--model", default="nai-diffusion-3")
    parser.add_argument("--steps", type=int, default=23)
    parser.add_argument("--scale", type=float, default=5.0)
    parser.add_argument("--sampler", default="k_euler_ancestral")
    parser.add_argument("--noise-schedule", default="karras")
    parser.add_argument("--cfg-rescale", type=float, default=0.0)
    args = parser.parse_args()

    shots = build_shots(args.trigger_word, args.base_tags, args.extra_tags, args.outfit_tag)
    config = GenConfig(
        output_root=_PROJECT_ROOT / "outputs" / args.root_name / args.character_id,
        model=args.model,
        steps=args.steps,
        scale=args.scale,
        sampler=args.sampler,
        noise_schedule=args.noise_schedule,
        cfg_rescale=args.cfg_rescale,
    )

    async def _run() -> None:
        client = AsyncNovelAI(api_key=API_KEY)
        total = sum(s.count for s in shots)
        try:
            async for event in run_dataset(client, shots, config):
                mark = "OK" if event["status"] == "ok" else f"ERROR: {event.get('message')}"
                print(f"[{event['current']}/{total}] {event['category']}/{event['file']} {mark}")
        finally:
            await client.close()
        print(f"完了: {total}枚を {config.output_root} に保存しました。")

    asyncio.run(_run())
