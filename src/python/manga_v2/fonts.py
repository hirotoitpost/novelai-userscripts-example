"""
描き文字(効果音)向けのフォント一覧と、そのダウンロード。

Google Fonts の公式リポジトリ(github.com/google/fonts)の ofl/ にある日本語の
ディスプレイ書体だけを載せている。いずれも SIL Open Font License 1.1 で、商用利用・
画像への使用が認められている。ダウンロード時にはライセンス文(OFL.txt)も一緒に保存する。
Web を自由に探して落とすと書体ごとにライセンスがまちまちで確認しきれないため、
一覧は手で選んだものに限っている。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
FONT_DIR = _PROJECT_ROOT / "data" / "fonts"
_RAW_BASE = "https://github.com/google/fonts/raw/main/ofl"
LICENSE_NAME = "SIL Open Font License 1.1"


@dataclass(frozen=True)
class CatalogFont:
    id: str
    label: str
    # google/fonts の ofl/ 以下のディレクトリ名とファイル名
    family_dir: str
    filename: str
    # AIがフォントを選ぶときの手がかり(書体の雰囲気・向いている効果音)
    mood: str

    @property
    def path(self) -> Path:
        return FONT_DIR / self.id / self.filename

    @property
    def installed(self) -> bool:
        return self.path.is_file()

    @property
    def source_url(self) -> str:
        return f"https://fonts.google.com/specimen/{self.label.replace(' ', '+')}"


CATALOG: list[CatalogFont] = [
    CatalogFont("dela-gothic-one", "Dela Gothic One", "delagothicone", "DelaGothicOne-Regular.ttf",
                "極太のゴシック。重い衝撃・迫力・爆発(ドドド、ドーン、バーン、ゴゴゴ)"),
    CatalogFont("rampart-one", "Rampart One", "rampartone", "RampartOne-Regular.ttf",
                "立体的な縁取りの太字。派手な登場・大きな音・強調(ジャーン、バァン)"),
    CatalogFont("reggae-one", "Reggae One", "reggaeone", "ReggaeOne-Regular.ttf",
                "角ばった筆風の太字。勢い・アクション・打撃(ガッ、バキッ、ズバッ)"),
    CatalogFont("rocknroll-one", "RocknRoll One", "rocknrollone", "RocknRollOne-Regular.ttf",
                "丸みのある太字。元気・コミカル・軽い動き(ポン、ピョン、パタパタ)"),
    CatalogFont("potta-one", "Potta One", "pottaone", "PottaOne-Regular.ttf",
                "筆で書いたような太い文字。和風・力強い叫び・荒々しさ(ザッ、ドン)"),
    CatalogFont("train-one", "Train One", "trainone", "TrainOne-Regular.ttf",
                "線で縁取った中抜き文字。にぎやか・キラキラ・ポップな効果(キラッ、ワァッ)"),
    CatalogFont("yusei-magic", "Yusei Magic", "yuseimagic", "YuseiMagic-Regular.ttf",
                "マーカーで書いた手書き風。日常の小さな音・生活音(コトッ、カチャ、トントン)"),
    CatalogFont("hachi-maru-pop", "Hachi Maru Pop", "hachimarupop", "HachiMaruPop-Regular.ttf",
                "丸文字の手書き。かわいい・照れ・ほのぼの(ドキッ、ニコッ、ニャー)"),
    CatalogFont("mochiy-pop-one", "Mochiy Pop One", "mochiypopone", "MochiyPopOne-Regular.ttf",
                "ぷっくりしたポップ体。明るい・はずむ・楽しい(ワクワク、ルンルン)"),
    CatalogFont("dotgothic16", "DotGothic16", "dotgothic16", "DotGothic16-Regular.ttf",
                "ドット文字。電子音・機械・ゲーム(ピピッ、ブーン、ピコーン)"),
    CatalogFont("stick", "Stick", "stick", "Stick-Regular.ttf",
                "細い棒で組んだ文字。静けさ・張りつめた空気・不気味(シーン、ヒュウウ)"),
    CatalogFont("yomogi", "Yomogi", "yomogi", "Yomogi-Regular.ttf",
                "細めの手書き。ささやき・小さな動き・柔らかい雰囲気(そっ、ふわっ、サラサラ)"),
    CatalogFont("kaisei-tokumin", "Kaisei Tokumin", "kaiseitokumin", "KaiseiTokumin-ExtraBold.ttf",
                "太い明朝。シリアス・重厚・決めの場面(ゴォォ、ズン)"),
    CatalogFont("zen-antique", "Zen Antique", "zenantique", "ZenAntique-Regular.ttf",
                "古風な明朝。和風・時代もの・怪談(ギィ…、カァー)"),
    CatalogFont("shippori-antique-b1", "Shippori Antique B1", "shipporiantiqueb1", "ShipporiAntiqueB1-Regular.ttf",
                "レトロな丸みのある書体。懐かしさ・穏やかな情景(チリン、サァァ)"),
    # 筆文字
    CatalogFont("yuji-boku", "Yuji Boku", "yujiboku", "YujiBoku-Regular.ttf",
                "太く荒々しい毛筆。豪快・力強い一撃・怒り(ドォン、ズバァッ、ゴゴゴ)"),
    CatalogFont("yuji-syuku", "Yuji Syuku", "yujisyuku", "YujiSyuku-Regular.ttf",
                "端正な毛筆の楷書。和風・厳か・緊張感(カッ、シン…、ザッ)"),
    CatalogFont("yuji-mai", "Yuji Mai", "yujimai", "YujiMai-Regular.ttf",
                "流れるような細い毛筆。しなやか・風・水の音(ヒュウ、サラサラ、ポチャン)"),
    CatalogFont("zen-kurenaido", "Zen Kurenaido", "zenkurenaido", "ZenKurenaido-Regular.ttf",
                "筆ペン風の手書き。素朴・日常の音・感情のこもった小さな音(トクン、ふぅ)"),
]
CATALOG_BY_ID = {font.id: font for font in CATALOG}


async def download_font(font: CatalogFont) -> None:
    """フォント本体とライセンス文を data/fonts/<id>/ に保存する。"""
    target = FONT_DIR / font.id
    target.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
        # ライセンス文を先に保存し、本体があれば「導入済み」=ライセンスも揃っている状態にする
        for name in ("OFL.txt", font.filename):
            response = await client.get(f"{_RAW_BASE}/{font.family_dir}/{name}")
            response.raise_for_status()
            # 途中で失敗しても壊れたファイルが「導入済み」に見えないよう、書き切ってから置き換える
            tmp = target / f"{name}.part"
            tmp.write_bytes(response.content)
            tmp.replace(target / name)
