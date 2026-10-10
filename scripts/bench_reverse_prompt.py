"""
リバースプロンプト(画像 → プロンプトの逆引き)の性能を、評価用データ(scripts/make_reverse_eval.py)で測る。

正解は生成に使ったタグ(すべて danbooru の語彙)を、生成した絵を目視で確かめて直したもの(manifest の truth)。逆引きしたタグとの一致で、精度(P)・再現率(R)・F1 と、
タグの種類ごとの再現率を出す。画像に埋め込まれた生成情報は使わず、画素だけから推定する。

実行方法:
  uv run python scripts/bench_reverse_prompt.py                 # 今の設定(しきい値・調整込み)
  uv run python scripts/bench_reverse_prompt.py --sweep         # 一般タグのしきい値を変えて比べる
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from PIL import Image  # noqa: E402

import make_reverse_eval as ev  # noqa: E402
from python.image_tagger import STYLE_TAGS, TagScore, select_tags, split_characters, tag_image  # noqa: E402

MANIFEST = ev.OUT_DIR / "manifest.json"

# タグの種類(評価用データの組み立てに使った候補から決める)
GROUPS: dict[str, set[str]] = {
    "人数": {"1girl", "1boy", "solo", "no humans"},
    "絵柄": {t for style in ev.STYLES for t in style},
    "髪・目": set(ev.HAIR) | set(ev.HAIR_LENGTH) | set(ev.EYES),
    "服・小物": {t for outfit in ev.OUTFITS for t in outfit} | set(ev.ACCESSORIES) | {"japanese clothes"},
    "構図・動作・表情": set(ev.FRAMING) | {t for pose in ev.POSES for t in pose} | set(ev.EXPRESSIONS),
    "場所": {t for place in ev.PLACES for t in place},
    "風景・動物・食べ物": {t for scene in ev.NO_HUMANS for t in scene} - {"no humans"},
}


def group_of(tag: str) -> str:
    for name, tags in GROUPS.items():
        if tag in tags:
            return name
    return "そのほか"


def score(items: list[dict], predictions: dict[str, set[str]]) -> dict:
    tp = fp = fn = 0
    by_group: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for item in items:
        truth = set(item.get("truth") or item["tags"])
        pred = predictions[item["name"]]
        tp += len(truth & pred)
        fp += len(pred - truth)
        fn += len(truth - pred)
        for tag in truth:
            group = by_group[group_of(tag)]
            group[0] += tag in pred
            group[1] += 1
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {
        "P": p,
        "R": r,
        "F1": 2 * p * r / (p + r) if p + r else 0.0,
        "tags": sum(len(v) for v in predictions.values()) / len(items),
        "groups": {name: hit / total for name, (hit, total) in by_group.items()},
    }


def people_score(items: list[dict]) -> None:
    """
    二人の絵で、人物ごとのプロンプト(左から順)に、その人の性別と髪の色が付いたか。正解は絵を目視で確かめた
    もの(manifest の verified_people)。髪の色がほかの人のプロンプトに付いたら「入れ替わり」。
    """
    people = [it for it in items if it.get("verified_people")]
    if not people:
        return
    right = swapped = gender_ok = total = 0
    for item in people:
        guesses = split_characters(Image.open(ROOT / item["image"]).convert("RGB"))
        truth = item["verified_people"]
        for i, person in enumerate(truth):
            total += 1
            if i >= len(guesses):
                continue
            gender_ok += guesses[i].gender == person["gender"]
            right += person["hair"] in guesses[i].tags
            swapped += any(person["hair"] in g.tags for j, g in enumerate(guesses) if j != i)
    print(
        f"\n人物ごと(二人の絵 {len(people)} 枚・{total} 人): 髪の色が正しい人に {right}・ほかの人に {swapped}・"
        f"性別が合った {gender_ok}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sweep", action="store_true")
    args = parser.parse_args()
    items = json.loads(MANIFEST.read_text(encoding="utf-8"))

    # 画素だけから判定する(RGB にすると、画素に隠した生成情報も使えない)
    scores: dict[str, list[TagScore]] = {}
    started = time.time()
    for item in items:
        scores[item["name"]] = tag_image(Image.open(ROOT / item["image"]).convert("RGB"))
    per_image = (time.time() - started) / len(items)

    thresholds = [0.3, 0.35, 0.4, 0.5, 0.6] if args.sweep else [None]
    print(f"{len(items)}枚・1枚 {per_image:.1f}秒(初回はモデルの読み込みを含む)\n")
    print(f"{'設定':24} {'P':>6} {'R':>6} {'F1':>6} {'タグ数':>6}")
    results = {}
    for th in thresholds:
        kwargs = {} if th is None else {"general_threshold": th}
        preds = {name: {s.tag for s in select_tags(sc, **kwargs)} for name, sc in scores.items()}
        label = "今の設定" if th is None else f"しきい値 {th}"
        result = score(items, preds)
        results[label] = result
        print(f"{label:24} {result['P']:6.3f} {result['R']:6.3f} {result['F1']:6.3f} {result['tags']:6.1f}")
    print("\nタグの種類ごとの再現率")
    for label, result in results.items():
        groups = "  ".join(f"{name} {value:.2f}" for name, value in result["groups"].items())
        print(f"  {label}: {groups}")
    people_score(items)
    # 絵柄のタグの定義が評価用データとそろっているか(STYLE_TAGS に無いものは低いしきい値の対象外)
    missing = GROUPS["絵柄"] - set(STYLE_TAGS)
    if missing:
        print("\n(注: 評価用データの絵柄タグのうち STYLE_TAGS に無いもの:", sorted(missing), ")")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
