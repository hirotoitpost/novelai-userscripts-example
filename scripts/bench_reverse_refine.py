"""
リバースプロンプトの「生成して見比べて直す」の効果を、評価用データ(scripts/make_reverse_eval.py)で測る。

画像ごとに、逆引きしたプロンプトで生成(1回目) → 元の絵と見比べてプロンプトを直す → 同じシードで生成(2回目)。
元の絵との近さ(タグの確率の cosine)と、元の絵で確かなタグが生成した絵に出た割合を比べる。
NovelAI で生成する(Opus プランで Anlas を使わない範囲: 約1メガピクセル・27ステップ・1枚)。

実行方法:
  uv run python scripts/bench_reverse_refine.py            # 12枚(1枚につき2回生成)
  uv run python scripts/bench_reverse_refine.py --count 6
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from PIL import Image  # noqa: E402

import make_reverse_eval as ev  # noqa: E402
from python.character_sheet import DEFAULT_NEGATIVE  # noqa: E402
from python.image_tagger import (  # noqa: E402
    _COUNT_TAGS,
    TagScore,
    build_negative,
    build_prompt,
    split_characters,
    tag_image,
)
from python.manga_v2.layout import generation_size  # noqa: E402
from python.novelai_image_v5 import generate_image_v5  # noqa: E402

OUT = ev.OUT_DIR / "refine"


# ---- 生成して見比べ、プロンプトを直す(試したがアプリには入れていない。docs/trials を参照) ----

# 元の絵で確か(これ以上)なのに、生成した絵では弱い(これ未満)タグを強める
_REFINE_ORIGINAL = 0.5
_REFINE_MISSING = 0.25
# 生成した絵にだけ強く出た(これ以上で、元の絵ではこれ未満の)タグはネガティブに足す
_REFINE_EXTRA = 0.6
_REFINE_ABSENT = 0.15
_REFINE_MAX_NEGATIVE = 8
# 強めるときの重み(NovelAI の 1.3::tag:: の書き方)。直すたびに足していき、上限で止める
_REFINE_WEIGHT_STEP = 0.2
_REFINE_WEIGHT_MAX = 1.8
_WEIGHTED = re.compile(r"^(\d+(?:\.\d+)?)::(.+?)::$")


def _general_probs(scores: list[TagScore]) -> dict[str, float]:
    return {s.tag: s.probability for s in scores if s.category == "general"}


def tag_similarity(original: list[TagScore], generated: list[TagScore]) -> float:
    """二枚の絵の近さ(0〜1)。タグの確率を並べたものの cosine。"""
    a, b = _general_probs(original), _general_probs(generated)
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    va = np.array([a.get(k, 0.0) for k in keys])
    vb = np.array([b.get(k, 0.0) for k in keys])
    denominator = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return float(va @ vb) / denominator if denominator else 0.0


def _split_tags(prompt: str) -> list[str]:
    return [t.strip() for t in prompt.split(",") if t.strip()]


def _bare(tag: str) -> tuple[str, float]:
    """重み付きのタグ(1.3::blue kimono::)を、タグと重みに分ける。"""
    match = _WEIGHTED.match(tag)
    return (match.group(2).strip(), float(match.group(1))) if match else (tag, 1.0)


@dataclass
class Refinement:
    positive: str
    negative: str
    characters: list[str]
    emphasized: list[str]
    suppressed: list[str]


def refine_prompt(
    positive: str,
    negative: str,
    characters: list[str],
    original: list[TagScore],
    generated: list[TagScore],
) -> Refinement:
    """
    元の絵と、プロンプトで生成した絵のタグを比べて、プロンプトを直す。元の絵で確かなのに生成した絵に
    出なかったタグは重みを上げ、生成した絵にだけ出たタグはネガティブに足す。
    """
    orig, gen = _general_probs(original), _general_probs(generated)
    emphasized: list[str] = []

    def strengthen(prompt: str) -> str:
        out = []
        for raw in _split_tags(prompt):
            tag, weight = _bare(raw)
            if orig.get(tag, 0.0) >= _REFINE_ORIGINAL and gen.get(tag, 0.0) < _REFINE_MISSING:
                weight = min(round(weight + _REFINE_WEIGHT_STEP, 2), _REFINE_WEIGHT_MAX)
                emphasized.append(tag)
                out.append(f"{weight:g}::{tag}::")
            else:
                out.append(raw)
        return ", ".join(out)

    new_positive = strengthen(positive)
    new_characters = [strengthen(c) for c in characters]
    in_prompt = {_bare(t)[0] for p in [positive, *characters] for t in _split_tags(p)}
    negative_tags = _split_tags(negative)
    extras = sorted(
        (
            (p, tag)
            for tag, p in gen.items()
            if p >= _REFINE_EXTRA
            and orig.get(tag, 0.0) < _REFINE_ABSENT
            and tag not in in_prompt
            and tag not in negative_tags
            and tag not in _COUNT_TAGS
        ),
        reverse=True,
    )[:_REFINE_MAX_NEGATIVE]
    suppressed = [tag for _, tag in extras]
    return Refinement(
        positive=new_positive,
        negative=", ".join(negative_tags + suppressed),
        characters=new_characters,
        emphasized=list(dict.fromkeys(emphasized)),
        suppressed=suppressed,
    )


def confident_recall(original: list[TagScore], generated: list[TagScore]) -> float:
    """元の絵で確か(0.5以上)なタグのうち、生成した絵でも 0.35 以上だった割合。"""
    orig = {s.tag for s in original if s.category == "general" and s.probability >= 0.5}
    gen = {s.tag for s in generated if s.category == "general" and s.probability >= 0.35}
    return len(orig & gen) / len(orig) if orig else 0.0


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=12)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    api_key = os.environ["NOVELAI_API_TOKEN"]
    items = json.loads((ev.OUT_DIR / "manifest.json").read_text(encoding="utf-8"))
    # 人物なし・一人・二人がまんべんなく入るよう、間を空けて選ぶ
    step = max(1, len(items) // args.count)
    items = items[::step][: args.count]
    OUT.mkdir(parents=True, exist_ok=True)

    rows = []
    for item in items:
        image = Image.open(ROOT / item["image"]).convert("RGB")
        original = tag_image(image)
        characters = split_characters(image)
        moved = {t for c in characters for t in c.tags}
        positive = build_prompt(original, exclude=moved)
        negative = build_negative(DEFAULT_NEGATIVE, positive)
        prompts = [c.prompt for c in characters]
        width, height = generation_size((0, 0, image.width, image.height))
        seed = item["seed"] + 1  # 元の絵と同じシードにはしない

        async def generate(pos: str, neg: str, chars: list[str], tag: str) -> list[TagScore]:
            data = await generate_image_v5(
                api_key,
                pos,
                neg,
                width=width,
                height=height,
                steps=27,
                scale=6.0,
                seed=seed,
                character_tags=chars or None,
            )
            (OUT / f"{item['name']}_{tag}.png").write_bytes(data)
            return tag_image(Image.open(io.BytesIO(data)).convert("RGB"))

        first = await generate(positive, negative, prompts, "1")
        fix = refine_prompt(positive, negative, prompts, original, first)
        second = await generate(fix.positive, fix.negative, fix.characters, "2")
        row = {
            "name": item["name"],
            "sim1": tag_similarity(original, first),
            "sim2": tag_similarity(original, second),
            "rec1": confident_recall(original, first),
            "rec2": confident_recall(original, second),
            "emphasized": fix.emphasized,
            "suppressed": fix.suppressed,
        }
        rows.append(row)
        print(
            f"{item['name']}  近さ {row['sim1']:.3f} → {row['sim2']:.3f}  確かなタグ {row['rec1']:.2f} → {row['rec2']:.2f}"
            f"  強めた {fix.emphasized[:5]}  打ち消した {fix.suppressed[:5]}",
            flush=True,
        )
    n = len(rows)
    print(
        f"\n平均({n}枚): 近さ {sum(r['sim1'] for r in rows) / n:.3f} → {sum(r['sim2'] for r in rows) / n:.3f}"
        f"  確かなタグ {sum(r['rec1'] for r in rows) / n:.3f} → {sum(r['rec2'] for r in rows) / n:.3f}"
        f"  良くなった {sum(r['sim2'] > r['sim1'] for r in rows)}枚"
    )
    (OUT / "result.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
