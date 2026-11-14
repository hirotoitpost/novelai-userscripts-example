"""
ヘルプのチャット: ヘルプの記事(src/help/*.md)だけを元に、質問に答える。

- 記事を全部渡すと小さいモデルの文脈に入りきらないので、質問(と今開いている記事)に近い記事を選んで渡す。
  近さは、文字の2つ組(日本語は単語の区切りが無いので)の重なりで見る
- 答え方は、既定値「help.chat_model」(管理ページ)か、画面で選んだもの:
  excerpt(文章モデルを使わず、近い記事の節をそのまま出す。既定)/ ollama(この PC。費用なし)/
  novelai(NovelAI の GLM-4.6。文章生成の残量を使う)/ auto(トークンがあれば novelai、無ければ ollama)
  ローカルの小さいモデル(qwen2.5:7b)は、2026-11 の試しで手順を飛ばしたり、記事に無い助言(再起動・戻す)を
  したりしたので、既定は作り話をしない excerpt にした
- 記事に無いことは「ヘルプに書いてありません」と答えさせる(画面の名前や手順を作らせない)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

HELP_DIR = Path(__file__).resolve().parent.parent / "help"
MAX_ARTICLES = 3
ENGINES = ("excerpt", "auto", "ollama", "novelai")


@dataclass(frozen=True)
class Article:
    slug: str
    title: str
    feature: str
    summary: str
    body: str


def load_articles(folder: Path | None = None) -> list[Article]:
    articles = []
    for path in sorted((folder or HELP_DIR).glob("*.md")):
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        meta: dict[str, str] = {}
        body = text
        match = re.match(r"---\n(.*?)\n---\n?", text, re.S)
        if match:
            for line in match.group(1).splitlines():
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip()
            body = text[match.end():]
        articles.append(Article(path.stem, meta.get("title", path.stem), meta.get("feature", ""), meta.get("summary", ""), body))
    return articles


def _grams(text: str) -> set[str]:
    text = re.sub(r"[\s\W_]+", " ", text.lower())
    grams: set[str] = set()
    for word in text.split():
        if len(word) == 1:
            grams.add(word)
        grams.update(word[i:i + 2] for i in range(len(word) - 1))
    return grams


# 聞き方の言い換え(記事で使っている言葉を足して探す)
SYNONYMS: dict[str, str] = {
    "消": "隠す 知らせない 片付け 外す",
    "削除": "片付け ごみ箱 外す",
    "止め": "隠す 知らせない",
    "増や": "足す 追加",
    "追加": "足す",
    "減ら": "外す 片付け",
    "復元": "戻す",
    "元に": "戻す",
    "容量": "空き",
    "いっぱい": "空き 足りない",
    "通知": "お知らせ ベル",
    "ディスク": "写し先",
    "ドライブ": "写し先 ディスク",
    "作り方": "流れ 手順",
    "作る": "流れ",
    "やり方": "流れ 手順",
}


def expand(question: str) -> str:
    return question + " " + " ".join(words for key, words in SYNONYMS.items() if key in question)


def pick(question: str, articles: list[Article], current: str | None = None, limit: int = MAX_ARTICLES) -> list[Article]:
    """質問に近い記事(今開いている記事は先頭に入れる)。"""
    wanted = _grams(expand(question))
    scored = []
    for article in articles:
        head = _grams(f"{article.title} {article.summary} {article.feature}")
        body = _grams(article.body)
        # 題名・要約の重なりを重く見る。本文は長いほど重なりやすいので、割合で見る
        score = 3 * len(wanted & head) + 10 * len(wanted & body) / max(1, len(body)) ** 0.5
        if article.slug == current:
            score += 1000
        scored.append((score, article))
    scored.sort(key=lambda pair: -pair[0])
    return [article for score, article in scored[:limit] if score > 0]


SYSTEM = (
    "あなたは、このアプリ(NovelAI の画像生成・漫画・物語ツール)のヘルプ係です。\n"
    "下の「ヘルプの記事」に書いてあることだけを元に、日本語だけで、短く答えてください(ほかの言語の文字を混ぜない)。\n"
    "手順を聞かれたら、記事の番号付きの手順を、言い換えずにそのまま写してください。\n"
    "ボタンや欄の名前は、記事に **太字** で書いてある名前を、一字も変えずに使ってください。記事に無い名前や手順を作らないでください。\n"
    "記事に答えが無いときは、推測せずに「ヘルプには書いてありません」と答え、近い記事があればそれを挙げてください。\n"
    "参考にした記事は、答えの最後に [記事の題名](/help/記事の名前) の形で挙げてください。"
)


def build_messages(question: str, history: list[dict[str, str]], articles: list[Article]) -> list[dict[str, str]]:
    context = "\n\n".join(
        f"=== 記事の名前: {a.slug} / 題名: {a.title}(機能: {a.feature})===\n{a.body.strip()}" for a in articles
    ) or "(質問に近い記事はありませんでした)"
    messages = [{"role": "system", "content": f"{SYSTEM}\n\n# ヘルプの記事\n\n{context}"}]
    for turn in history[-6:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": str(turn["content"])[:2000]})
    messages.append({"role": "user", "content": question})
    return messages


def choose_engine(wanted: str, setting: str, has_novelai: bool) -> str:
    """使う文章モデル(画面の指定 → 既定値 → auto の解決)。"""
    engine = wanted if wanted in ENGINES and wanted != "default" else setting
    if engine not in ENGINES:
        engine = "excerpt"
    if engine == "auto":
        engine = "novelai" if has_novelai else "ollama"
    return engine


# アラビア文字・キリル文字・ハングル(小さいモデルが混ぜることがある)
_FOREIGN = re.compile(r"[\u0600-\u06ff\u0400-\u04ff\uac00-\ud7af]")


def flag_unknown_names(answer: str, articles: list[Article]) -> str:
    """
    答えの中の **太字** と「かぎかっこ」の名前のうち、渡した記事のどこにも無いものを、最後に知らせる
    (小さいモデルはボタンの名前を作ってしまうことがある)。ほかの言語の文字が混ざったときも知らせる。
    """
    source = "\n".join(f"{a.title}\n{a.body}" for a in articles)
    unknown: list[str] = []
    for bold, quoted in re.findall(r"\*\*([^*\n]{1,40})\*\*|「([^」\n]{1,40})」", answer):
        name = (bold or quoted).strip()
        if name and name not in source and name not in unknown and not name.startswith("ヘルプには"):
            unknown.append(name)
    notes = []
    if unknown:
        notes.append("記事に無い名前があります(まちがいかもしれません。記事で確かめてください): " + "、".join(unknown))
    if _FOREIGN.search(answer):
        notes.append("答えにほかの言語の文字が混ざっています。記事で確かめてください。")
    return answer + ("\n\n> ※ " + " ".join(notes) if notes else "")


def _sections(body: str) -> list[tuple[str, str]]:
    """記事を「## 見出し」ごとに分ける(最初の見出しより前は、見出しなし)。"""
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in body.splitlines():
        heading = re.match(r"##\s+(.*)", line)
        if heading:
            sections.append((heading.group(1).strip(), []))
        else:
            sections[-1][1].append(line)
    return [(head, "\n".join(lines).strip()) for head, lines in sections if "\n".join(lines).strip()]


def excerpt(question: str, articles: list[Article]) -> str:
    """
    文章モデルを使わない答え: いちばん近い記事の、いちばん近い節を、書いてあるまま出す(作り話をしない。費用なし)。
    """
    if not articles:
        return "ヘルプには書いてありません。[ヘルプの一覧](/help) から探してください。"
    article = articles[0]
    wanted = _grams(expand(question))
    # 「関連する記事」のようにリンクを並べただけの節は、答えにしない
    sections = [sec for sec in _sections(article.body) if "関連" not in sec[0]] or _sections(article.body)
    # 言い換えの言葉(隠す・足す など)がそのまま見出しにあれば、文字の重なりより重く見る
    words = [w for key, ws in SYNONYMS.items() if key in question for w in ws.split()]

    def score(section: tuple[str, str]) -> float:
        head, text = section
        overlap = 3 * len(wanted & _grams(head)) + len(wanted & _grams(text)) / max(1, len(_grams(text))) ** 0.5
        return overlap + sum(10 for w in words if w in head)

    scored = sorted(sections, key=lambda sec: -score(sec))
    # 手順の節があれば、聞き方が「〜するには」でも手順を優先する
    if any(word in question for word in ("には", "方法", "手順", "やり方", "どうすれば", "したい")):
        steps = [sec for sec in sections if "手順" in sec[0]]
        if steps:
            scored = steps + [sec for sec in scored if sec not in steps]
    head, text = scored[0]
    title = f"「{article.title}」" + (f"の「{head}」" if head else "")
    others = "".join(f"\n- [{a.title}](/help/{a.slug})" for a in articles[1:])
    return (
        f"{title}より(記事のまま):\n\n{text}\n\n詳しくは [{article.title}](/help/{article.slug})"
        + (f"\n\nほかに近い記事:{others}" if others else "")
    )
