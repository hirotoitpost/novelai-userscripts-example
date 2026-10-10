"""
リバースプロンプト(画像 → プロンプトの逆引き)の評価用データを NovelAI で生成する。

プロンプトは danbooru の語彙にあるタグだけで組み立てる(逆引きでいくつ取り戻せたかがそのまま
精度になるように)。全年齢のみ: 人物は大人にし、ネガティブで露出・裸を打ち消す。人物のほかに風景・
動物・食べ物も混ぜ、カラー・白黒・水彩の絵柄も混ぜる。組み合わせは固定のシードで決めるので、
何度実行しても同じプロンプトになる(生成済みの画像は飛ばす)。

Opus プランで Anlas を使わない範囲(約1メガピクセル・27ステップ・1枚ずつ)で V5 を使う。

実行方法:
  uv run python scripts/make_reverse_eval.py            # 48枚(既定)
  uv run python scripts/make_reverse_eval.py --count 10

出力: data/eval/reverse_prompt/images/*.png と manifest.json(画像ごとの生成に使ったタグ tags、
目視で直した正解 truth、シード・サイズ)。生成済みの画像は作り直さない(正解の直しだけ反映する)
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

from python.novelai_image_v5 import generate_image_v5  # noqa: E402

OUT_DIR = ROOT / "data" / "eval" / "reverse_prompt"
VOCAB = ROOT / "data" / "models" / "wd-eva02-large-tagger-v3" / "selected_tags.csv"

SIZES = {"portrait": (832, 1216), "landscape": (1216, 832), "square": (1024, 1024)}

NEGATIVE = (
    "lowres, worst quality, bad quality, bad anatomy, bad hands, jpeg artifacts, signature, watermark, text, "
    "multiple views, comic, nsfw, nude, nipples, cleavage, underwear, panties, swimsuit, bikini, "
    "loli, child, aged down, petite"
)

STYLES = [
    [],
    [],
    [],
    ["monochrome", "greyscale"],
    ["watercolor (medium)", "traditional media"],
    ["flat color"],
    ["sketch", "monochrome"],
]

HAIR = ["black hair", "brown hair", "blonde hair", "grey hair", "red hair", "blue hair", "pink hair", "green hair"]
HAIR_LENGTH = ["long hair", "short hair", "medium hair", "ponytail", "twintails", "braid", "bob cut", "hair bun"]
EYES = ["blue eyes", "brown eyes", "green eyes", "red eyes", "purple eyes", "yellow eyes"]
OUTFITS = [
    ["white shirt", "black skirt"],
    ["suit", "necktie"],
    ["hoodie", "jeans"],
    ["kimono", "obi"],
    ["apron", "long sleeves"],
    ["coat", "scarf"],
    ["sweater", "long skirt"],
    ["dress", "frills"],
    ["t-shirt", "shorts"],
    ["jacket", "pants"],
]
ACCESSORIES = ["glasses", "hat", "hair ribbon", "earrings", "hairclip", "headphones", "beret", "choker"]
FRAMING = ["upper body", "cowboy shot", "full body", "portrait"]
POSES = [
    ["standing"],
    ["sitting", "chair"],
    ["walking"],
    ["holding cup"],
    ["holding book", "reading"],
    ["holding umbrella", "rain"],
    ["arms up"],
    ["hand on own hip"],
    ["waving"],
    ["v"],
]
EXPRESSIONS = ["smile", "open mouth", "closed eyes", "blush", "serious", "surprised", "laughing", "expressionless"]
PLACES = [
    ["indoors", "kitchen"],
    ["outdoors", "park", "tree"],
    ["outdoors", "city", "street", "night"],
    ["outdoors", "beach", "ocean"],
    ["indoors", "library", "bookshelf"],
    ["indoors", "cafe", "table"],
    ["outdoors", "snow", "winter"],
    ["outdoors", "cherry blossoms", "spring (season)"],
    ["simple background", "white background"],
    ["indoors", "bedroom", "bed"],
    ["outdoors", "shrine", "torii"],
    ["outdoors", "sunset", "sky"],
]
# 人物の出ない絵(風景・動物・食べ物)
NO_HUMANS = [
    ["no humans", "scenery", "mountain", "lake", "sky", "cloud", "tree"],
    ["no humans", "scenery", "city", "building", "night", "city lights"],
    ["no humans", "cat", "animal focus", "sitting", "indoors", "window"],
    ["no humans", "dog", "animal focus", "grass", "outdoors"],
    ["no humans", "food", "food focus", "cake", "plate", "fruit"],
    ["no humans", "food", "food focus", "ramen", "bowl", "chopsticks"],
    ["no humans", "flower", "still life", "vase", "table"],
    ["no humans", "scenery", "forest", "river", "sunlight"],
    ["no humans", "bird", "animal focus", "branch", "sky"],
    ["no humans", "scenery", "train station", "railroad tracks", "sunset"],
]


# 生成した絵が指定どおりでなかったものの正解の直し(2026-10-10 に48枚を目視で確認)。
# 構図: portrait=顔と肩、upper body=腰より上、cowboy shot=太ももあたりまで、full body=足先まで。
# 絵柄: flat color を指定した絵は全部、実際には平塗りでなかった。色のある絵の monochrome・sketch も外す。
# (名前 → (外すタグ, 足すタグ))
LABEL_FIXES: dict[str, tuple[list[str], list[str]]] = {
    "eval_001": (["upper body"], ["cowboy shot"]),
    "eval_004": (["flat color"], []),
    "eval_005": (["upper body", "flat color"], ["cowboy shot"]),
    "eval_006": (["upper body"], ["cowboy shot"]),
    "eval_007": (["upper body"], ["cowboy shot"]),
    "eval_008": (["flat color"], []),
    "eval_009": (["flat color"], []),
    "eval_011": (["flat color"], []),
    "eval_014": (["upper body"], ["cowboy shot"]),
    "eval_016": (["cowboy shot"], ["full body"]),
    "eval_019": (["sketch", "monochrome"], []),
    "eval_020": (["flat color"], []),
    "eval_021": (["portrait"], ["upper body"]),
    "eval_025": (["portrait", "watercolor (medium)", "traditional media"], ["cowboy shot"]),
    "eval_027": (["portrait", "flat color"], ["upper body"]),
    "eval_029": (["portrait", "watercolor (medium)", "traditional media"], ["upper body"]),
    "eval_030": (["upper body"], ["cowboy shot"]),
    "eval_032": (["sketch"], []),
    "eval_034": (["portrait"], ["upper body"]),
    "eval_037": (["portrait"], ["upper body"]),
    "eval_038": (["watercolor (medium)", "traditional media"], []),
    "eval_040": (["flat color"], []),
    "eval_042": (["portrait", "monochrome", "greyscale"], ["cowboy shot"]),
    "eval_044": (["upper body"], ["portrait"]),
    "eval_045": (["greyscale"], []),
    "eval_047": (["portrait"], ["upper body"]),
}


def truth_of(item: dict) -> list[str]:
    """評価の正解(生成に使ったタグを、目視で直したもの)。"""
    remove, add = LABEL_FIXES.get(item["name"], ([], []))
    return [t for t in item["tags"] if t not in remove] + [t for t in add if t not in item["tags"]]


def person(rng: random.Random, gender: str) -> list[str]:
    tags = [rng.choice(HAIR), rng.choice(HAIR_LENGTH), rng.choice(EYES), *rng.choice(OUTFITS)]
    if rng.random() < 0.5:
        tags.append(rng.choice(ACCESSORIES))
    if gender == "boy" and "kimono" in tags:
        tags = [t for t in tags if t not in ("kimono", "obi")] + ["japanese clothes"]
    return tags


def make_prompts(count: int) -> list[dict]:
    rng = random.Random(20261010)
    items = []
    for index in range(count):
        style = rng.choice(STYLES)
        kind = rng.choices(["girl", "boy", "couple", "none"], weights=[4, 3, 2, 2])[0]
        if kind == "none":
            tags = list(rng.choice(NO_HUMANS))
            size = rng.choice(["landscape", "square"])
        else:
            if kind == "couple":
                tags = ["1girl", "1boy", *person(rng, "girl"), *person(rng, "boy"), "smile"]
            else:
                tags = ["1girl" if kind == "girl" else "1boy", "solo", *person(rng, kind), rng.choice(EXPRESSIONS)]
            tags += [rng.choice(FRAMING), *rng.choice(POSES), *rng.choice(PLACES)]
            size = "landscape" if kind == "couple" else rng.choice(["portrait", "portrait", "square"])
        tags = list(dict.fromkeys(style + tags))
        items.append({"name": f"eval_{index:03d}", "tags": tags, "size": size, "seed": rng.randrange(2**32)})
    return items


def check_vocab(items: list[dict]) -> None:
    """正解のタグが danbooru の語彙(WD Tagger のタグ一覧)にあるか。無いタグは逆引きで取り戻せない。"""
    if not VOCAB.is_file():
        print("(語彙の一覧が無いので確認を飛ばします:", VOCAB, ")")
        return
    with open(VOCAB, encoding="utf-8") as f:
        vocab = {row["name"].replace("_", " ") for row in csv.DictReader(f)}
    missing = sorted({t for item in items for t in item["tags"] if t not in vocab})
    if missing:
        raise SystemExit(f"語彙に無いタグがあります: {missing}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=48)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    api_key = os.environ["NOVELAI_API_TOKEN"]

    items = make_prompts(args.count)
    check_vocab(items)
    image_dir = OUT_DIR / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    for item in items:
        path = image_dir / f"{item['name']}.png"
        item["image"] = str(path.relative_to(ROOT)).replace("\\", "/")
        item["truth"] = truth_of(item)
        if path.exists():
            continue
        width, height = SIZES[item["size"]]
        data = await generate_image_v5(
            api_key,
            ", ".join(item["tags"]) + ", very aesthetic, masterpiece",
            NEGATIVE,
            width=width,
            height=height,
            steps=27,
            scale=6.0,
            seed=item["seed"],
        )
        path.write_bytes(data)
        print(item["name"], ", ".join(item["tags"])[:100], flush=True)
    (OUT_DIR / "manifest.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(items), "images in", OUT_DIR)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
