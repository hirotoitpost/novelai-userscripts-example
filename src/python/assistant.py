"""
作業アシスタントの「次の操作」(画面の横のパネル)。今のページと作業の状態から、やることの一覧と次の一手を出す。

- 案内(guide)は、ページの名前ごとの関数(GUIDES)。ほかの機能も、関数を1つ足せば同じパネルに出せる
- 1つの手順(Step)は、状態(済み・これから・注意・参考)と、押すとその場所へ移る action(画面の中のリンク。# でその欄へ)を持つ
- 読むだけ。ここからアプリの操作(生成・設定の変更)はしない。押すのは利用者
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from . import db
from .db import get_connection

Status = Literal["done", "todo", "warn", "info"]


@dataclass
class Step:
    key: str
    label: str
    status: Status
    detail: str = ""
    action: str = ""  # 押すと移る場所(例: /story?story=3#manga-v2)
    action_label: str = ""
    help: str = ""  # ヘルプの記事


@dataclass
class Guide:
    title: str
    summary: str = ""
    steps: list[Step] = field(default_factory=list)
    help: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # 次の一手: 最初の「これから」、無ければ最初の「注意」
        upcoming = next((s for s in self.steps if s.status == "todo"), None) or next(
            (s for s in self.steps if s.status == "warn"), None
        )
        data["next"] = upcoming.key if upcoming else None
        return data


GuideFn = Callable[[dict[str, str]], Guide]
GUIDES: dict[str, GuideFn] = {}


def guide_for(page: str) -> Callable[[GuideFn], GuideFn]:
    def register(fn: GuideFn) -> GuideFn:
        GUIDES[page] = fn
        return fn

    return register


def guide(page: str, params: dict[str, str]) -> dict[str, Any]:
    fn = GUIDES.get(page, _default_guide)
    return fn(params).to_dict()


# ---- 漫画(物語 → 漫画) ----


def _story_link(story_id: int, anchor: str = "") -> str:
    return f"/story?story={story_id}" + (f"#{anchor}" if anchor else "")


def _file_mtime(rel: str | None) -> datetime | None:
    if not rel:
        return None
    path = Path(db.user_root()) / rel
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _parse_time(text: str) -> datetime:
    value = datetime.fromisoformat(text)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def manga_steps(story_id: int) -> Guide:
    conn = get_connection()
    try:
        story = db.get_story(conn, story_id)
        if story is None:
            return Guide(title="物語が見つかりません", summary="一覧から物語を開き直してください。", help="manga")
        panels = conn.execute(
            "SELECT scene_id, MAX(created_at) AS created_at, MAX(stale) AS stale FROM manga_panels WHERE story_id = ? GROUP BY scene_id",
            (story_id,),
        ).fetchall()
        v1_pages = conn.execute("SELECT COUNT(*) FROM manga_pages WHERE story_id = ?", (story_id,)).fetchone()[0]
    finally:
        conn.close()

    scenes = story["scenes"]
    title = story.get("title") or (story.get("premise") or "").splitlines()[0][:30] or f"物語 {story_id}"
    steps: list[Step] = []
    total = len(scenes)

    # 1. シーン
    if not total:
        steps.append(Step("scenes", "シーンに分ける", "todo", "まだシーンがありません。前提からドラフトを作るか、本文を取り込んでください。",
                          _story_link(story_id), "物語を開く", "manga"))
        return Guide(title=title, summary="シーンに分けるところからです。", steps=steps, help="manga")
    steps.append(Step("scenes", "シーンに分ける", "done", f"{total} シーン", _story_link(story_id, "scene-list"), "シーン一覧へ", "manga"))

    # 2. 本文
    unwritten = [s for s in scenes if not s["novelai_text"]]
    if unwritten:
        steps.append(Step("text", "本文を入れる", "todo",
                          f"本文の無いシーンが {len(unwritten)} あります(全部のシーンに本文が入ると、漫画の欄が出ます)。",
                          _story_link(story_id, "scene-list"), "シーン一覧へ", "manga"))
    else:
        steps.append(Step("text", "本文を入れる", "done", "全部のシーンに本文があります。"))

    # 3. 登場人物
    with_cast = [s for s in scenes if s["characters"]]
    if not with_cast:
        steps.append(Step("cast", "登場人物を割り当てる", "todo",
                          "どのシーンにも登場人物がいません。人物の見た目がコマごとにばらばらになります。",
                          _story_link(story_id, "scene-list"), "編集で割り当てる", "manga-characters"))
    else:
        missing = total - len(with_cast)
        steps.append(Step("cast", "登場人物を割り当てる", "done" if not missing else "info",
                          f"{len(with_cast)}/{total} シーンに人物がいます"
                          + (f"(残り {missing} シーンは人物なし。風景や小物のコマなら問題ありません)" if missing else ""),
                          _story_link(story_id, "scene-list"), "シーン一覧へ", "manga-characters"))
        cast: dict[int, dict[str, Any]] = {}
        for scene in with_cast:
            for person in scene["characters"]:
                cast[person["id"]] = person
        no_ref = sorted(p["name"] for p in cast.values() if not p.get("reference_image_path"))
        if no_ref:
            steps.append(Step("references", "参照画像を決める", "warn",
                              f"参照画像の無いキャラ: {'、'.join(no_ref)}。キャラ参照を使うなら、先に決めてください。",
                              "/characters", "キャラ一覧へ", "manga-characters"))

    # 4. コマの絵
    drawn = {row["scene_id"]: row for row in panels}
    stale = [s for s in scenes if s["id"] in drawn and drawn[s["id"]]["stale"]]
    undrawn = [s for s in scenes if s["id"] not in drawn]
    if v1_pages and not drawn:
        steps.append(Step("panels", "コマの絵を描く", "info",
                          f"v1(ページごと生成)で {v1_pages} ページ描いてあります。v2 でコマごとに描くと、セリフや配置を直せます。",
                          _story_link(story_id, "manga-v2"), "漫画の欄へ", "manga-panels"))
    elif undrawn:
        first = undrawn[0]["scene_index"] + 1
        steps.append(Step("panels", "コマの絵を描く", "todo",
                          f"{len(drawn)}/{total} コマ描いてあります。未生成 {len(undrawn)} コマ(シーン {first} から)。"
                          "「絵があるシーンは飛ばす」にして「コマの絵を生成」を押すと、残りだけ描きます。",
                          _story_link(story_id, "manga-v2"), "漫画の欄へ", "manga-panels"))
    else:
        steps.append(Step("panels", "コマの絵を描く", "done", f"全 {total} コマ描いてあります。",
                          _story_link(story_id, "manga-v2"), "漫画の欄へ", "manga-panels"))
    if stale:
        steps.append(Step("stale", "描き直しが要るコマ", "warn",
                          f"内容を変えたあと描き直していないコマ: シーン {'・'.join(str(s['scene_index'] + 1) for s in stale[:10])}"
                          + (" ほか" if len(stale) > 10 else ""),
                          _story_link(story_id, "manga-v2"), "漫画の欄へ", "manga-panels"))

    # 5. 効果音・ナレーション(なくてもよい)
    unset_sfx = sum(1 for s in scenes if s["sfx"] is None)
    unset_narration = sum(1 for s in scenes if s["narration"] is None)
    if drawn and (unset_sfx or unset_narration):
        steps.append(Step("sfx", "効果音・ナレーション", "info",
                          f"未設定: 効果音 {unset_sfx} コマ・ナレーション {unset_narration} コマ。AI に提案させるか、無しのままでも合成できます。",
                          _story_link(story_id, "manga-v2"), "漫画の欄へ", "manga-compose"))

    # 6. ページの合成
    composed_at = _file_mtime(story.get("final_image_path"))
    latest_panel = max((_parse_time(row["created_at"]) for row in panels), default=None)
    if not drawn:
        pass
    elif composed_at is None:
        steps.append(Step("compose", "ページを合成する", "todo", "まだ合成していません。「ページを合成」で、吹き出しと描き文字を入れたページにします。",
                          _story_link(story_id, "manga-v2"), "漫画の欄へ", "manga-compose"))
    elif latest_panel and latest_panel > composed_at:
        steps.append(Step("compose", "ページを合成する", "warn", "合成したあとに描いたコマがあります。合成し直すと、ページに入ります。",
                          _story_link(story_id, "manga-v2"), "漫画の欄へ", "manga-compose"))
    else:
        steps.append(Step("compose", "ページを合成する", "done", "合成してあります。吹き出しの位置はページでドラッグして直せます。",
                          _story_link(story_id, "manga-v2"), "漫画の欄へ", "manga-compose"))
        steps.append(Step("read", "読む・ダウンロード", "info", "本棚で読めます。漫画の欄の「ダウンロード」で PDF・画像一式にできます。",
                          "/bookshelf", "本棚へ", "manga-compose"))

    todo = [s for s in steps if s.status in ("todo", "warn")]
    summary = f"次は「{todo[0].label}」です。" if todo else "この物語の漫画は仕上がっています。"
    return Guide(title=title, summary=summary, steps=steps, help="manga")


@guide_for("story")
def _story_guide(params: dict[str, str]) -> Guide:
    story_id = int(params.get("story_id") or 0)
    if story_id > 0:
        return manga_steps(story_id)
    return Guide(
        title="物語 → 漫画",
        summary="物語を開くか、新しく作ってください。",
        steps=[
            Step("open", "物語を開く", "todo", "下の「過去の物語」から選ぶと、その物語の進み具合を案内します。", "/story", "", "manga"),
            Step("new", "新しく作る", "info", "前提から「ドラフト生成」、または物語エディタ・漫画ドラフトから作れます。", "/writer", "物語エディタへ", "manga"),
        ],
        help="manga",
    )


# ---- バックアップ(開発管理者) ----


@guide_for("admin")
def _admin_guide(params: dict[str, str]) -> Guide:
    from . import backup, notices

    link = "/admin?tab=ops#data-backup"
    settings = backup.load_settings()
    steps: list[Step] = []
    if not settings.disks:
        steps.append(Step("disks", "バックアップの写し先を決める", "todo", "外付けドライブなどを写し先にすると、1日1回自動で取ります。", link, "設定する", "backup"))
        return Guide(title="開発管理者", summary="バックアップがまだ設定されていません。", steps=steps, help="backup")
    lives, problems = backup.open_disks(settings, scan=False)
    active = [lv for lv in lives if not lv.disk.retiring]
    steps.append(Step("disks", "写し先", "warn" if problems else "done",
                      f"{len(lives)}/{len(settings.disks)} 台つながっています" + (f"(使えない: {'、'.join(problems)})" if problems else ""),
                      link, "バックアップの欄へ", "backup"))
    if len(active) < settings.copies:
        steps.append(Step("copies", f"{settings.copies} か所にそろえる", "todo",
                          f"置く先として使える写し先が {len(active)} 台です。写し先を足してください。", link, "写し先を足す", "backup-add-disk"))
    listed = backup.list_snapshots(lives)
    if listed:
        last = datetime.fromisoformat(listed[0]["created_at"])
        days = (datetime.now(timezone.utc) - last).total_seconds() / 86400
        steps.append(Step("last", "前回のバックアップ", "warn" if days >= notices.STALE_DAYS else "done",
                          f"{last.astimezone().strftime('%m/%d %H:%M')}({days:.1f} 日前)", link, "今すぐ取る", "backup"))
    else:
        steps.append(Step("last", "最初のバックアップを取る", "todo", "「今すぐバックアップ」を押してください。", link, "今すぐ取る", "backup"))
    for disk in settings.disks:
        if disk.retiring:
            steps.append(Step(f"retire:{disk.path}", f"{disk.path} を外す", "todo",
                              "外す準備中です。バックアップを取り、「この写し先は外せます」と出たら「外す」→「設定を保存」。",
                              link, "バックアップの欄へ", "backup-remove-disk"))
    for notice in notices.collect():
        if notice.key.startswith("backup.") and notice.level != "info":
            steps.append(Step(notice.key, notice.title, "warn", notice.body, notice.link, notice.link_label, notice.help))
    todo = [s for s in steps if s.status in ("todo", "warn")]
    return Guide(title="バックアップ", summary=f"次は「{todo[0].label}」です。" if todo else "バックアップは順調です。", steps=steps, help="backup")


# ---- キャラ ----


def _character_images_link(character_id: int, anchor: str = "") -> str:
    return f"/character-images?character={character_id}" + (f"#{anchor}" if anchor else "")


@guide_for("characters")
def _characters_guide(params: dict[str, str]) -> Guide:
    conn = get_connection()
    try:
        characters = db.list_characters(conn)
    finally:
        conn.close()
    if not characters:
        return Guide(title="キャラ一覧", summary="まだキャラがいません。", help="characters", steps=[
            Step("none", "キャラを作る", "todo", "物語の「登場人物を抽出」や、データセットのキャラシートから作れます。", "/story", "物語 → 漫画へ", "characters"),
        ])
    steps = [Step("count", "キャラ", "info", f"{len(characters)} 人います。「参照画像: なし」で、参照画像の無いキャラだけを出せます。")]
    no_ref = [c for c in characters if not c.get("reference_image_path")]
    if no_ref:
        names = "、".join(c["name"] for c in no_ref[:8]) + (f" ほか {len(no_ref) - 8} 人" if len(no_ref) > 8 else "")
        # 抽出で作られた端役のキャラも多いので、急ぎの「注意」ではなく「参考」にする
        steps.append(Step("references", "参照画像を決める", "info",
                          f"参照画像の無いキャラ: {names}。漫画のキャラ参照や、見た目をそろえるのに使います。",
                          _character_images_link(no_ref[0]["id"]), f"{no_ref[0]['name']} の画像へ", "character-images"))
    else:
        steps.append(Step("references", "参照画像を決める", "done", "全員に参照画像があります。"))
    with_ref = [c for c in characters if c.get("reference_image_path")]
    no_back = [c for c in with_ref if not c.get("back_reference_image_path")]
    if with_ref and no_back:
        steps.append(Step("back", "後ろ姿の参照", "info",
                          f"後ろ姿の参照が無いキャラが {len(no_back)} 人います。後ろ姿のコマを描くときだけ要ります(無ければ正面の参照を弱めて使います)。",
                          "", "", "manga-characters"))
    todo = [s for s in steps if s.status in ("todo", "warn")]
    summary = f"次は「{todo[0].label}」です。" if todo else (
        f"参照画像の無いキャラが {len(no_ref)} 人います。漫画に出すキャラから決めてください。" if no_ref else "キャラの準備はそろっています。")
    return Guide(title="キャラ一覧", summary=summary,
                 steps=steps, help="characters")


@guide_for("character-images")
def _character_images_guide(params: dict[str, str]) -> Guide:
    from . import character_images

    character_id = int(params.get("character_id") or 0)
    if character_id <= 0:
        return Guide(title="生成した画像", summary="上でキャラを選ぶと、そのキャラの画像と次の操作を案内します。", help="character-images", steps=[
            Step("choose", "キャラを選ぶ", "todo", "上の「キャラを選ぶ」から選んでください。", "", "", "character-images"),
        ])
    conn = get_connection()
    try:
        character = db.get_character(conn, character_id)
    finally:
        conn.close()
    if character is None:
        return Guide(title="キャラが見つかりません", summary="キャラを選び直してください。", help="character-images")
    items = character_images.collect(character)
    counts: dict[str, int] = {}
    for item in items:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    steps: list[Step] = []
    if character.get("reference_image_path"):
        steps.append(Step("reference", "参照画像", "done", "決まっています。ほかの画像を拡大して「参照画像にする」で選び直せます。", "", "", "character-images"))
    elif items:
        steps.append(Step("reference", "参照画像を決める", "todo",
                          f"まだありません。下の画像({len(items)} 枚)を押して拡大し、「参照画像にする」を押してください。",
                          "", "", "character-images"))
    else:
        steps.append(Step("reference", "参照画像を決める", "todo",
                          "まだ画像がありません。物語の「登場人物をそろえる(参照画像も)」で候補を作るか、画像生成で作ってこのキャラに関連付けてください。",
                          "/story", "物語 → 漫画へ", "manga-characters"))
    if character.get("reference_image_path") and not character.get("back_reference_image_path"):
        steps.append(Step("back", "後ろ姿の参照", "info", "ありません。後ろ姿のコマを描くときだけ要ります。漫画 v2 の「キャラ参照」で、コマの絵から選べます。",
                          "", "", "manga-characters"))
    summary_kinds = "・".join(f"{character_images.KINDS.get(k, k)} {n}" for k, n in counts.items())
    if summary_kinds:
        steps.append(Step("images", "画像", "info", f"{summary_kinds} 枚。種類のボタンで絞り込めます。"))
    candidates = counts.get("candidate", 0)
    if character.get("reference_image_path") and candidates:
        steps.append(Step("cleanup", "使わない候補を片付ける", "info",
                          f"参照画像の候補が {candidates} 枚あります。もう使わなければ「古い画像の整理」でごみ箱へ移せます(30日は戻せます)。",
                          _character_images_link(character_id, "image-cleanup"), "古い画像の整理へ", "character-images"))
    todo = [s for s in steps if s.status in ("todo", "warn")]
    return Guide(title=f"{character['name']} の画像", summary=f"次は「{todo[0].label}」です。" if todo else "参照画像は決まっています。",
                 steps=steps, help="character-images")


# ---- ホーム(全体の案内) ----


def _stories_in_progress(limit: int = 3) -> list[dict[str, Any]]:
    """やりかけの漫画(コマが残っている・まだ合成していない)。新しい物語から。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT s.id, s.title, s.premise, s.final_image_path,
                   (SELECT COUNT(*) FROM story_scenes x WHERE x.story_id = s.id) AS scenes,
                   (SELECT COUNT(DISTINCT p.scene_id) FROM manga_panels p WHERE p.story_id = s.id) AS drawn
            FROM stories s ORDER BY s.id DESC LIMIT 40
            """
        ).fetchall()
    finally:
        conn.close()
    found = []
    for row in rows:
        if not row["scenes"] or not row["drawn"]:
            continue
        if row["drawn"] < row["scenes"] or not row["final_image_path"]:
            found.append(dict(row))
        if len(found) >= limit:
            break
    return found


@guide_for("home")
def _home_guide(params: dict[str, str]) -> Guide:
    steps: list[Step] = []
    for story in _stories_in_progress():
        title = story["title"] or (story["premise"] or "").splitlines()[0][:24] or f"物語 {story['id']}"
        left = story["scenes"] - story["drawn"]
        detail = f"未生成 {left} コマ(全 {story['scenes']} コマ)。" if left else "コマは全部あります。まだページを合成していません。"
        steps.append(Step(f"story:{story['id']}", f"漫画の続き: {title}", "todo", detail,
                          _story_link(story["id"], "manga-v2"), "続きを開く", "manga"))
    conn = get_connection()
    try:
        no_ref = [c for c in db.list_characters(conn) if not c.get("reference_image_path")]
    finally:
        conn.close()
    if no_ref:
        steps.append(Step("characters", "参照画像の無いキャラ", "info", f"{len(no_ref)} 人います。", "/characters", "キャラ一覧へ", "characters"))
    if params.get("is_admin") == "1":
        from . import notices

        backup_steps = [st for st in _admin_guide(params).steps if st.status in ("todo", "warn")]
        warnings = [n for n in notices.collect() if n.level != "info"]
        if backup_steps:
            first = backup_steps[0]
            steps.append(Step("backup", f"バックアップ: {first.label}", "warn", first.detail, first.action, first.action_label, first.help))
        elif warnings:
            steps.append(Step("notices", "お知らせ", "warn", f"対応が要るお知らせが {len(warnings)} 件あります(右下のベル)。", "", "", "notices"))
        else:
            steps.append(Step("backup", "バックアップ", "done", "順調です。", "/admin?tab=ops#data-backup", "バックアップの欄へ", "backup"))
    todo = [s for s in steps if s.status in ("todo", "warn")]
    return Guide(title="ホーム", summary=f"やりかけの作業が {len(todo)} つあります。" if todo else "やりかけの作業はありません。",
                 steps=steps, help="assistant")


# ---- そのほかのページ ----

PAGE_HELP = {
    "help": ("ヘルプ", "assistant"),
    "gallery": ("ギャラリー", "character-images"),
    "generate": ("画像生成", "character-images"),
    "bookshelf": ("本棚", "manga-compose"),
    "trash": ("ごみ箱", "storage"),
}


def _default_guide(params: dict[str, str]) -> Guide:
    page = params.get("page", "")
    name, slug = PAGE_HELP.get(page, ("このページ", ""))
    steps = [
        Step("home", "やりかけの作業を見る", "info", "ホームで、続きの漫画や参照画像の無いキャラなどをまとめて案内します。", "/", "ホームへ", "assistant"),
        Step("manga", "漫画を作る・続きを描く", "info", "物語を開くと、進み具合と次の一手を案内します。", "/story", "物語 → 漫画へ", "manga"),
        Step("characters", "キャラを整える", "info", "参照画像の無いキャラなどを案内します。", "/characters", "キャラ一覧へ", "characters"),
    ]
    return Guide(title=name, summary="このページの案内はまだありません。よく使う作業から選べます。", steps=steps, help=slug)


# ---- チャット(読むだけ。今のページの状況とヘルプの記事を渡して答えさせる) ----

CHAT_SYSTEM = (
    "あなたは、このアプリ(NovelAI の画像生成・漫画・物語・キャラ・バックアップのツール)の作業アシスタントです。\n"
    "日本語だけで、短く、具体的に答えてください。手順は番号付きにしてください。\n"
    "あなたはアプリを操作できません。操作は利用者が行います。「〜しておきました」のように、自分がやったように言わないでください。\n"
    "下の「今の画面の状況」と「ヘルプの記事」を元に答えてください。ボタンや欄の名前は、そこに書いてある名前を一字も変えずに使ってください。\n"
    "そこに無いアプリの機能・ボタン・手順を作らないでください。分からないときは「分かりません」と言い、近いヘルプの記事を挙げてください。\n"
    "移ってほしい場所や記事は [名前](/リンク) の形で示してください(リンクは状況と記事にあるものだけ)。\n"
    "プロンプトの書き方や物語の相談のような、アプリの操作でない質問には、ふつうに答えてかまいません。"
)


def guide_text(data: dict[str, Any]) -> str:
    """案内(guide の戻り値)を、文章モデルに渡す文に。"""
    label = {"done": "済み", "todo": "これから", "warn": "注意", "info": "参考"}
    lines = [f"ページ: {data['title']}", data.get("summary") or ""]
    for step in data["steps"]:
        mark = " ← 次はこれ" if data.get("next") == step["key"] else ""
        link = f"(移る先: [{step['action_label']}]({step['action']}))" if step.get("action") and step.get("action_label") else ""
        lines.append(f"- [{label[step['status']]}] {step['label']}: {step['detail']}{link}{mark}")
    return "\n".join(line for line in lines if line)


def chat_messages(question: str, history: list[dict[str, str]], guide_data: dict[str, Any], articles: list[Any]) -> list[dict[str, str]]:
    from .help_chat import build_messages

    # ヘルプのチャットと同じ形で記事と履歴を入れ、先頭の指示だけ作業アシスタントのものにする
    messages = build_messages(question, history, articles)
    context = messages[0]["content"].split("# ヘルプの記事", 1)[1] if "# ヘルプの記事" in messages[0]["content"] else ""
    messages[0] = {
        "role": "system",
        "content": f"{CHAT_SYSTEM}\n\n# 今の画面の状況\n\n{guide_text(guide_data)}\n\n# ヘルプの記事{context}",
    }
    return messages
