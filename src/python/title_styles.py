"""
作品風テンプレート(title_styles)。よく画像生成で使われるゲーム・アニメの「立ち絵/画面の雰囲気」を、
作品タグ + 雰囲気タグ + 衣装の例 + ネガティブの組で持ち、自作キャラに着せて使い回す。

作品の既存キャラを描くためのものではなく、絵柄・塗り・背景・衣装の方向だけを借りる。作品タグは
その作品のキャラの特徴(髪色・耳・しっぽ・艤装など)を混ぜることがあるので、使うときはキャラの髪色・髪型・
目の色のタグを強めにする。混ざりやすい物は negative に入れてある(馬耳など作品の顔になる物は notes に書く)。

実機(NovelAI V4.5 Full・参照なし・2026-11-19, 生成履歴 #1603〜)で確かめた結果:
- 雰囲気タグ(official art・塗り・背景など)だけでは、どの作品も「よくあるゲーム絵」になる。作品タグが決め手。
- ゲームの作品タグは「official art, white background」と組むと立ち絵(全身・白背景)になる。
- アニメの作品タグは anime screenshot / anime coloring と組むと、放送画面の塗り(平たいセル・細い線)になる。
- 作品タグが弱い作品(衣装を指定しないとオフィス服などに流れる)は、衣装の例を一緒に入れると寄る。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class TitleStyle:
    key: str
    label: str  # 画面に出す名前
    kind: str  # game / anime
    title_tag: str  # 作品タグ(Danbooru の作品名)
    look: str  # 絵柄・塗り・作品らしい小物(場面を問わず足せる物)
    outfit: str  # その作品らしい衣装の例(自作キャラ用。キャラの服を使うなら入れない)
    negative: str = ""  # 作品タグが混ぜやすい物
    notes: str = ""  # 傾向・注意(実機の結果)
    framing: str = ""  # 立ち絵・画面の構図と背景(1枚絵のときだけ足す)

    def style_tags(self) -> str:
        """作画タグに足す画風(作品タグ + 絵柄)。構図・背景・衣装は入れない(漫画のコマ・コレクションは場面が決める)。"""
        return join_tags(self.title_tag, self.look)

    def prompt_tags(self, outfit: bool = True) -> str:
        """1枚絵(立ち絵)のプロンプトに足すタグ。outfit=True なら衣装の例も足す。"""
        return join_tags(self.outfit if outfit else "", self.title_tag, self.look, self.framing)


def join_tags(*parts: str) -> str:
    """タグの列をつなぐ(空・重複は落とす。最初に出た順)。"""
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        for tag in (part or "").split(","):
            tag = tag.strip()
            if tag and tag.lower() not in seen:
                seen.add(tag.lower())
                out.append(tag)
    return ", ".join(out)


_STAND = "full body, standing, looking at viewer, white background, simple background"
_GAME = "official art"
_ANIME = "anime screenshot, anime coloring"

TITLE_STYLES: tuple[TitleStyle, ...] = (
    # ---- ゲーム(立ち絵) ----
    TitleStyle(
        "blue_archive", "ブルーアーカイブ風", "game", "blue archive",
        _GAME + ", halo, soft shading, pastel colors, light blue theme",
        "white school uniform, blue necktie, pleated skirt, tactical vest, holding assault rifle, sneakers",
        notes="作品タグで色付きの幾何学的なヘイロー・明るくすっきりした塗りになる。青ネクタイ・タイツが出やすい",
        framing=_STAND,
    ),
    TitleStyle(
        "nikke", "NIKKE風(立ち絵)", "game", "goddess of victory: nikke",
        _GAME + ", semi-realistic, detailed, glossy skin, techwear, fashion",
        "cropped jacket, high-cut bodysuit, thighhighs, gloves, headphones around neck, holding assault rifle",
        notes="セミリアルでつやのある肌、テックウェア。作品タグ無しだと重たい汎用の絵になる",
        framing=_STAND,
    ),
    TitleStyle(
        "nikke_back", "NIKKE風(戦闘の後ろ姿)", "game", "goddess of victory: nikke",
        _GAME + ", detailed, glossy skin",
        "black tactical bodysuit, cropped jacket, thigh strap, gloves, combat boots, holding sniper rifle",
        notes="戦闘画面の後ろ姿。お尻が強調される",
        framing="full body, standing, from behind, looking back, gradient background, grey background",
    ),
    TitleStyle(
        "genshin", "原神風", "game", "genshin impact",
        _GAME + ", detailed clothes, gold trim, fantasy",
        "fantasy adventurer outfit, detached sleeves, gem ornament, tassels, gold trim, thighhighs, boots",
        notes="神の目のような宝石の飾り・ひらひらの袖・金の縁取り。作品タグだけでも宝石と袖が出る",
        framing=_STAND,
    ),
    TitleStyle(
        "star_rail", "崩壊:スターレイル風", "game", "honkai: star rail",
        _GAME + ", detailed clothes, sci-fi, elegant",
        "elegant long coat, ornate jacket, gloves, thigh boots, jewelry",
        notes="原神より落ち着いた上品な服。作品タグだけでは弱めなので衣装の例と組む",
        framing=_STAND,
    ),
    TitleStyle(
        "zzz", "ゼンレスゾーンゼロ風", "game", "zenless zone zero",
        _GAME + ", urban, streetwear, cel shading, bold colors",
        "oversized jacket, crop top, shorts, thigh strap, chunky sneakers, headphones",
        notes="ストリート系・ベルトやブーツ。くっきりしたセル塗り",
        framing=_STAND,
    ),
    TitleStyle(
        "wuthering_waves", "鳴潮風", "game", "wuthering waves",
        _GAME + ", detailed clothes, futuristic, fantasy",
        "asymmetrical clothes, high-tech fantasy outfit, tassels, cropped jacket, thigh boots",
        notes="房飾り・左右非対称のSFファンタジー服",
        framing=_STAND,
    ),
    TitleStyle(
        "arknights", "アークナイツ風", "game", "arknights",
        _GAME + ", muted colors, techwear",
        "tactical jacket, hood, straps, armband, gloves, black pantyhose, boots",
        "animal ears, tail",
        notes="ストラップ・手袋・くすんだ色のテックウェア。獣耳が混ざることがあるのでネガティブに入れている",
        framing=_STAND,
    ),
    TitleStyle(
        "azur_lane", "アズールレーン風", "game", "azur lane",
        _GAME + ", shiny, detailed",
        "naval uniform, epaulettes, cape, rigging, thighhighs, high heels",
        "animal ears",
        notes="作品タグだけでは弱い(オフィス服に流れる)。軍服・艤装の衣装と組む",
        framing=_STAND,
    ),
    TitleStyle(
        "kancolle", "艦これ風", "game", "kantai collection",
        _GAME + ", rigging, machinery, turret",
        "sailor collar, serafuku, pleated skirt, thighhighs",
        notes="作品タグだけで艤装(砲塔)が出るほど強い",
        framing=_STAND,
    ),
    TitleStyle(
        "fgo", "Fate/Grand Order風", "game", "fate/grand order",
        _GAME + ", ornate, detailed",
        "ornate armor dress, cape, gauntlets, armored boots, holding sword",
        notes="作品タグだけでは弱い。鎧ドレス・マントの衣装と組む",
        framing=_STAND,
    ),
    TitleStyle(
        "umamusume", "ウマ娘風", "game", "umamusume",
        _GAME + ", horse ears, horse tail, ear covers",
        "racing outfit, jacket, gloves, boots",
        notes="作品タグだけで馬耳・しっぽが出る(作品の顔)。耳が要らないときはネガティブに horse ears, tail を足す",
        framing=_STAND,
    ),
    TitleStyle(
        "idolmaster", "アイドルマスター風", "game", "idolmaster cinderella girls",
        _GAME + ", idol, sparkle, shiny",
        "idol costume, frills, ribbons, puffy sleeves, frilled skirt, holding microphone",
        notes="作品タグだけでは弱い。アイドル衣装と組む",
        framing=_STAND,
    ),
    TitleStyle(
        "hololive", "ホロライブ(VTuber)風", "game", "hololive",
        _GAME + ", virtual youtuber",
        "vtuber costume, detached sleeves, frills, hair ornament, thighhighs",
        notes="作品タグだけでは弱い。VTuber らしい盛った衣装と組む",
        framing=_STAND,
    ),
    TitleStyle(
        "touhou", "東方Project風", "game", "touhou",
        _GAME,
        "frilled dress, puffy short sleeves, bow, hat ribbon, mary janes",
        notes="フリルのドレス・パフスリーブに寄る",
        framing=_STAND,
    ),
    TitleStyle(
        "granblue", "グランブルーファンタジー風", "game", "granblue fantasy",
        _GAME + ", fantasy, detailed",
        "fantasy armor dress, cape, gold trim, gauntlets, thigh boots",
        notes="作品タグだけでは弱い。ファンタジーの鎧ドレスと組む",
        framing=_STAND,
    ),
    TitleStyle(
        "princess_connect", "プリンセスコネクト!風", "game", "princess connect!",
        _GAME + ", fantasy",
        "fantasy guild outfit, ribbon, capelet, frilled skirt, boots",
        notes="作品タグだけでは弱い。ファンタジーのギルド服と組む",
        framing=_STAND,
    ),
    TitleStyle(
        "persona5", "ペルソナ5風", "game", "persona 5",
        _GAME + ", red and black, stylish, high contrast",
        "phantom thief outfit, domino mask, gloves, long coat, boots",
        notes="作品タグだけでは弱い。赤黒・怪盗服と組む",
        framing=_STAND,
    ),
    # ---- アニメ(放送画面) ----
    TitleStyle(
        "frieren", "葬送のフリーレン風", "anime", "sousou no frieren",
        _ANIME + ", soft colors, fantasy",
        "mage robe, capelet, long skirt, boots, holding staff",
        notes="やわらかい色・細い線の放送画面の塗り",
        framing="cowboy shot",
    ),
    TitleStyle(
        "oshi_no_ko", "推しの子風", "anime", "oshi no ko",
        _ANIME + ", sparkle",
        "idol costume, frills, ribbons",
        "star-shaped pupils, symbol-shaped pupils",
        notes="星の瞳が出やすいのでネガティブに入れている(入れたいときは外す)",
        framing="cowboy shot",
    ),
    TitleStyle(
        "bocchi", "ぼっち・ざ・ろっく!風", "anime", "bocchi the rock!",
        _ANIME + "",
        "track jacket, pleated skirt, guitar case",
        "pink hair",
        notes="髪色が金髪やピンクに引っ張られることがある。キャラの髪色を強めにする",
        framing="cowboy shot",
    ),
    TitleStyle(
        "jujutsu", "呪術廻戦風", "anime", "jujutsu kaisen",
        _ANIME + "",
        "black high-collar jacket, black skirt, black pantyhose, boots",
        notes="作品タグだけで黒い高専の制服っぽい服になる",
        framing="cowboy shot",
    ),
    TitleStyle(
        "chainsaw_man", "チェンソーマン風", "anime", "chainsaw man",
        _ANIME + ", muted colors",
        "white shirt, black necktie, black pants, suit",
        notes="くすんだ色・白シャツに黒ネクタイ。髪色が金髪に引っ張られることがある",
        framing="cowboy shot",
    ),
    TitleStyle(
        "kimetsu", "鬼滅の刃風", "anime", "kimetsu no yaiba",
        _ANIME + ", bold lineart",
        "kimono, hakama, plain haori, holding katana",
        "demon slayer uniform, butterfly print, checkered print",
        notes="作品タグだけで隊服+柄の羽織になるほど強い(既存キャラに寄る)ので、無地の羽織・袴にして隊服と柄はネガティブ",
        framing="cowboy shot",
    ),
    TitleStyle(
        "evangelion", "エヴァンゲリオン風(90年代)", "anime", "neon genesis evangelion",
        "1990s (style), retro artstyle, anime screenshot",
        "casual clothes, jacket, t-shirt, shorts",
        "plugsuit, interface headset, bodysuit",
        notes="90年代の塗り。プラグスーツ+赤茶の髪だと既存キャラそっくりになるので、プラグスーツはネガティブに入れている",
        framing="cowboy shot",
    ),
    TitleStyle(
        "sailor_moon", "セーラームーン風(90年代)", "anime", "bishoujo senshi sailor moon",
        "1990s (style), retro artstyle, anime screenshot, magical girl",
        "magical girl, frilled dress, ribbon, heart brooch, holding wand",
        "sailor senshi uniform, tiara, sailor collar",
        notes="90年代少女アニメの塗りと顔。セーラー戦士の服(セーラー襟・ティアラ)だと既存キャラそっくりになるのでネガティブに入れている",
        framing="cowboy shot",
    ),
)

_BY_KEY = {s.key: s for s in TITLE_STYLES}


def get(key: str | None) -> TitleStyle | None:
    if not key:
        return None
    return _BY_KEY.get(key.strip())


def require(key: str) -> TitleStyle:
    style = get(key)
    if style is None:
        raise KeyError(f"作品風テンプレート「{key}」はありません。使えるもの: {', '.join(_BY_KEY)}")
    return style


def catalog() -> list[dict[str, Any]]:
    """一覧(画面・MCP 用)。style_tags / prompt_tags は組み立てた結果。"""
    return [
        {**asdict(s), "style_tags": s.style_tags(), "prompt_tags": s.prompt_tags()}
        for s in TITLE_STYLES
    ]


def apply(prompt: str, negative: str | None, key: str | None, outfit: bool = True) -> tuple[str, str | None]:
    """プロンプトとネガティブに作品風を足す(key が無ければそのまま)。"""
    style = require(key) if key else None
    if style is None:
        return prompt, negative
    new_negative = join_tags(negative or "", style.negative) or None
    return join_tags(prompt, style.prompt_tags(outfit)), new_negative
