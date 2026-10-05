from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "app.db"


def get_connection() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prompt_chunks (
            id TEXT PRIMARY KEY,
            remote_object_id TEXT,
            container_id TEXT,
            label TEXT NOT NULL,
            expansion TEXT NOT NULL DEFAULT '',
            color TEXT,
            is_category INTEGER NOT NULL DEFAULT 0,
            child_order TEXT,
            version INTEGER,
            synced_at TEXT NOT NULL
        )
        """
    )
    # 「カテゴリ」はNovelAI公式のフォルダ構造(is_category/container_id)をそのまま流用し、
    # 「シチュエーション」はそれとは独立した自前のタグ分類として追加する。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS situations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prompt_chunk_situations (
            chunk_id TEXT NOT NULL REFERENCES prompt_chunks(id) ON DELETE CASCADE,
            situation_id INTEGER NOT NULL REFERENCES situations(id) ON DELETE CASCADE,
            PRIMARY KEY (chunk_id, situation_id)
        )
        """
    )
    # ワードの関係(競合/排他): 「髪の長さ」のような排他グループを作り、
    # 同じグループに属するチャンクは同時に選ばれてはいけない、という形でモデル化する。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS exclusive_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prompt_chunk_exclusive_groups (
            chunk_id TEXT NOT NULL REFERENCES prompt_chunks(id) ON DELETE CASCADE,
            group_id INTEGER NOT NULL REFERENCES exclusive_groups(id) ON DELETE CASCADE,
            PRIMARY KEY (chunk_id, group_id)
        )
        """
    )
    # ワード選択ルール: プリセットは特定のチャンクID組み合わせを名前付きで保存し、
    # 呼び出すたびに同じ組み合わせを再現する。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS preset_chunks (
            preset_id INTEGER NOT NULL REFERENCES presets(id) ON DELETE CASCADE,
            chunk_id TEXT NOT NULL REFERENCES prompt_chunks(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            PRIMARY KEY (preset_id, chunk_id)
        )
        """
    )
    # ワード選択(/select)経由で生成した画像だけの履歴。プロンプト/設定/使ったチャンクIDと
    # 生成画像(ファイルパス)を紐付けて保存する。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS generation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt TEXT NOT NULL,
            negative_prompt TEXT,
            model TEXT NOT NULL,
            size TEXT NOT NULL,
            steps INTEGER NOT NULL,
            scale REAL NOT NULL,
            seed INTEGER,
            chunk_ids TEXT NOT NULL,
            image_paths TEXT NOT NULL,
            i2i_image_path TEXT,
            i2i_strength REAL,
            i2i_noise REAL,
            character_references TEXT,
            characters TEXT,
            based_on_id INTEGER,
            metadata_incomplete INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    # 物語生成パイプライン: 前提(premise)からOllamaでシーン分割ドラフトを作り、
    # シーンごとにNovelAI公式(Kayra)へ本文を書かせ、最終的に挿絵→漫画ページへ繋げる。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            premise TEXT NOT NULL,
            title TEXT,
            n_scenes INTEGER NOT NULL,
            panels_per_page INTEGER NOT NULL DEFAULT 4,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS story_scenes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
            scene_index INTEGER NOT NULL,
            page_index INTEGER NOT NULL,
            draft_title TEXT,
            draft_text TEXT NOT NULL DEFAULT '',
            draft_prompt_tags TEXT NOT NULL DEFAULT '',
            seed_cue TEXT,
            novelai_text TEXT,
            UNIQUE (story_id, scene_index)
        )
        """
    )
    # 1ページ = panels_per_page 個のシーンをまとめてV5に1回で生成させたコマ割り済み画像。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS manga_pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
            page_index INTEGER NOT NULL,
            image_path TEXT NOT NULL,
            scene_ids TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (story_id, page_index)
        )
        """
    )
    # 登場人物。物語をまたいで使い回せるよう story_id は持たせず、名前で一意にする
    # (同じキャラが続編にも出る、AIアシスタントのキャラ生成結果を流用する、といった使い方)。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            appearance_tags TEXT NOT NULL DEFAULT '',
            notes TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    # どのシーンに誰が出ているか。挿絵生成時にそのシーンのキャラの容姿タグを渡すために使う。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scene_characters (
            scene_id INTEGER NOT NULL REFERENCES story_scenes(id) ON DELETE CASCADE,
            character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
            PRIMARY KEY (scene_id, character_id)
        )
        """
    )
    # 挿絵生成のパラメータを名前を付けて保存しておくためのプリセット。
    # プロンプトチャンク用の presets とは別物なので、テーブルを分けている。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS image_presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            settings TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    # 漫画v2: シーン1つ = コマ1つの絵。コマ割り・吹き出しは合成時に行うので、絵だけを持つ。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS manga_panels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
            scene_id INTEGER NOT NULL UNIQUE REFERENCES story_scenes(id) ON DELETE CASCADE,
            image_path TEXT NOT NULL,
            seed INTEGER,
            width INTEGER NOT NULL,
            height INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    # 描き文字スタンプ(素材集のシートを1語ずつに切り出したもの)と、その取り込み元。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stamp_sources (
            key TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            author TEXT NOT NULL,
            url TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stamps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_key TEXT NOT NULL REFERENCES stamp_sources(key) ON DELETE CASCADE,
            sheet INTEGER NOT NULL,
            idx INTEGER NOT NULL,
            image_path TEXT NOT NULL,
            width INTEGER NOT NULL,
            height INTEGER NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    # 物語エディタ(NovelAIと対話しながら書く)の下書き。書き上げたら stories へ送る。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS story_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT '',
            memory TEXT NOT NULL DEFAULT '',
            author_note TEXT NOT NULL DEFAULT '',
            text TEXT NOT NULL DEFAULT '',
            settings TEXT NOT NULL DEFAULT '{}',
            story_id INTEGER REFERENCES stories(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    # 処理終了のプッシュ通知(Web Push)を受け取るブラウザ。endpoint はブラウザごとに一意。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            endpoint TEXT PRIMARY KEY,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            user_agent TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    # ギャラリー(画像)と本棚(物語)のブックマーク。kind は 'image' | 'book'、item_key はそれぞれの識別子
    # (画像は "history:{id}:{n}" などのギャラリーのキー、本は物語ID)。端末をまたいで共有するためDBに置く。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bookmarks (
            kind TEXT NOT NULL,
            item_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (kind, item_key)
        )
        """
    )
    # シリーズ: 巻(stories.series_id / volume_no)を束ねる。memory は全巻共通の設定(世界観・登場人物)、
    # source は出典・原作者(例: Toptoon Chat のキャラクター)、max_scenes は1巻のシーン数の目安上限。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS series (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            memory TEXT NOT NULL DEFAULT '',
            max_scenes INTEGER NOT NULL DEFAULT 20,
            created_at TEXT NOT NULL
        )
        """
    )
    # 作品の強い関連。「作品」はシリーズ(work_kind='series')か、シリーズに入っていない物語('story')。
    # 作者(出典・原作者。例: Toptoon Chat のキャラクター)は複数の作品に付けられる。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS authors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS work_authors (
            work_kind TEXT NOT NULL,
            work_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
            PRIMARY KEY (work_kind, work_id, author_id)
        )
        """
    )
    # type='spinoff' は from(派生) → to(元作品)。'crossover' は対等で、同じ組は1行だけ持つ。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS work_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            from_kind TEXT NOT NULL,
            from_id INTEGER NOT NULL,
            to_kind TEXT NOT NULL,
            to_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (type, from_kind, from_id, to_kind, to_id)
        )
        """
    )
    # 成人向けかどうかの手動指定。無い物は自動判定(タグ)に従う。kind/item_key は bookmarks と同じ。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS adult_marks (
            kind TEXT NOT NULL,
            item_key TEXT NOT NULL,
            adult INTEGER NOT NULL,
            PRIMARY KEY (kind, item_key)
        )
        """
    )
    conn.commit()
    _migrate_generation_history(conn)
    _migrate_stories(conn)
    _migrate_manga_pages(conn)
    _migrate_story_scenes(conn)
    _migrate_characters(conn)
    _migrate_stamp_sources(conn)


_STORIES_EXTRA_COLUMNS = {
    # export-manga で最終的に連結した画像のパス。ブラウザをリロードしても
    # 完成済みの漫画をDBから復元して表示できるようにするため。
    "final_image_path": "TEXT",
    # 公式サイト等から取り込んだ、シーン分割・タグ付け前の生の本文。
    # /split で分割済みになった後もそのまま残す(参照用)。
    "raw_text": "TEXT",
    # 漫画v2で手動配置(ドラッグ)した吹き出し・描き文字の位置。JSON {key: [x, y]}(コマに対する割合)
    "manga_v2_overrides": "TEXT",
    # 漫画v2で効果音ごとに使うフォント。JSON {効果音の文字列: フォントID}
    "manga_v2_sfx_fonts": "TEXT",
    # 漫画v2で効果音ごとに使うスタンプ。JSON {効果音の文字列: スタンプID}
    "manga_v2_sfx_stamps": "TEXT",
    # 漫画v2で最後に合成したときの設定(テンプレート・フォント・文字の大きさなど)。JSON。
    # ダウンロードはこの設定で作り直し、スタジオを開いたときにもこの設定に戻す。
    "manga_v2_compose_settings": "TEXT",
    # シリーズの巻。series_id が NULL なら単巻。(series_id, volume_no) は一意。
    "series_id": "INTEGER REFERENCES series(id) ON DELETE SET NULL",
    "volume_no": "INTEGER",
    # この巻のあらすじ(次の巻を書くときに「これまでのあらすじ」として渡す)。手で直せる。
    "recap": "TEXT",
    # 物語エディタのメモリ(世界観・登場人物)。登場人物の抽出で容姿の手がかりに使う。
    "memory": "TEXT",
}


# 生成に使ったシード。良いページが出たときに引き直しの当たりを再現できるようにする。
_MANGA_PAGES_EXTRA_COLUMNS = {"seed": "INTEGER"}


def _migrate_manga_pages(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(manga_pages)")}
    for column, column_type in _MANGA_PAGES_EXTRA_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE manga_pages ADD COLUMN {column} {column_type}")
    conn.commit()


# 漫画v2で描き文字にする効果音(JSONの文字列配列)。NULL は未設定(AI提案の対象)、
# 空配列は「このシーンには効果音を付けない」と決めた状態。
# narration は漫画v2のナレーション枠の文。NULL は未設定、空文字は「ナレーションなし」。
_STORY_SCENES_EXTRA_COLUMNS = {"sfx": "TEXT", "narration": "TEXT"}


def _migrate_story_scenes(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(story_scenes)")}
    for column, column_type in _STORY_SCENES_EXTRA_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE story_scenes ADD COLUMN {column} {column_type}")
    conn.commit()


# 漫画v2のキャラ参照(NovelAIのCharacter Reference)に使う画像。
_CHARACTERS_EXTRA_COLUMNS = {"reference_image_path": "TEXT"}


def _migrate_characters(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(characters)")}
    for column, column_type in _CHARACTERS_EXTRA_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE characters ADD COLUMN {column} {column_type}")
    conn.commit()


def get_manga_v2_overrides(conn: sqlite3.Connection, story_id: int) -> dict[str, list[float]]:
    row = conn.execute("SELECT manga_v2_overrides FROM stories WHERE id = ?", (story_id,)).fetchone()
    return json.loads(row["manga_v2_overrides"]) if row and row["manga_v2_overrides"] else {}


def set_manga_v2_overrides(conn: sqlite3.Connection, story_id: int, overrides: dict[str, list[float]]) -> None:
    conn.execute(
        "UPDATE stories SET manga_v2_overrides = ? WHERE id = ?",
        (json.dumps(overrides) if overrides else None, story_id),
    )
    conn.commit()


def get_manga_v2_sfx_fonts(conn: sqlite3.Connection, story_id: int) -> dict[str, str]:
    row = conn.execute("SELECT manga_v2_sfx_fonts FROM stories WHERE id = ?", (story_id,)).fetchone()
    return json.loads(row["manga_v2_sfx_fonts"]) if row and row["manga_v2_sfx_fonts"] else {}


def set_manga_v2_sfx_fonts(conn: sqlite3.Connection, story_id: int, fonts: dict[str, str]) -> None:
    conn.execute(
        "UPDATE stories SET manga_v2_sfx_fonts = ? WHERE id = ?",
        (json.dumps(fonts, ensure_ascii=False) if fonts else None, story_id),
    )
    conn.commit()


def get_manga_v2_compose_settings(conn: sqlite3.Connection, story_id: int) -> dict[str, Any] | None:
    """最後に合成したときの設定。まだ合成していなければ None。"""
    row = conn.execute("SELECT manga_v2_compose_settings FROM stories WHERE id = ?", (story_id,)).fetchone()
    return json.loads(row["manga_v2_compose_settings"]) if row and row["manga_v2_compose_settings"] else None


def set_manga_v2_compose_settings(conn: sqlite3.Connection, story_id: int, settings: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE stories SET manga_v2_compose_settings = ? WHERE id = ?",
        (json.dumps(settings, ensure_ascii=False), story_id),
    )
    conn.commit()


_STAMP_SOURCES_EXTRA_COLUMNS = {"adult": "INTEGER NOT NULL DEFAULT 0"}


def _migrate_stamp_sources(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(stamp_sources)")}
    for column, column_type in _STAMP_SOURCES_EXTRA_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE stamp_sources ADD COLUMN {column} {column_type}")
    conn.commit()


def set_stamp_source_adult(conn: sqlite3.Connection, key: str, adult: bool) -> None:
    conn.execute("UPDATE stamp_sources SET adult = ? WHERE key = ?", (int(adult), key))
    conn.commit()


def adult_stamp_sources(conn: sqlite3.Connection) -> set[str]:
    return {row["key"] for row in conn.execute("SELECT key FROM stamp_sources WHERE adult = 1")}


def set_story_memory(conn: sqlite3.Connection, story_id: int, memory: str) -> None:
    conn.execute("UPDATE stories SET memory = ? WHERE id = ?", (memory, story_id))
    conn.commit()


def get_manga_v2_sfx_stamps(conn: sqlite3.Connection, story_id: int) -> dict[str, int]:
    row = conn.execute("SELECT manga_v2_sfx_stamps FROM stories WHERE id = ?", (story_id,)).fetchone()
    return json.loads(row["manga_v2_sfx_stamps"]) if row and row["manga_v2_sfx_stamps"] else {}


def set_manga_v2_sfx_stamps(conn: sqlite3.Connection, story_id: int, stamps: dict[str, int]) -> None:
    conn.execute(
        "UPDATE stories SET manga_v2_sfx_stamps = ? WHERE id = ?",
        (json.dumps(stamps, ensure_ascii=False) if stamps else None, story_id),
    )
    conn.commit()


def replace_stamp_source(
    conn: sqlite3.Connection,
    key: str,
    title: str,
    author: str,
    url: str,
    stamps: list[dict[str, Any]],
    adult: bool = False,
) -> None:
    """取り込み元ごと入れ替える(同じ素材を取り込み直したときに重複させない)。"""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("DELETE FROM stamps WHERE source_key = ?", (key,))
    conn.execute(
        """
        INSERT INTO stamp_sources (key, title, author, url, adult, created_at) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (key) DO UPDATE SET title = excluded.title, author = excluded.author, url = excluded.url,
            adult = MAX(stamp_sources.adult, excluded.adult)
        """,
        (key, title, author, url, int(adult), now),
    )
    conn.executemany(
        """
        INSERT INTO stamps (source_key, sheet, idx, image_path, width, height, label, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (key, s["sheet"], s["idx"], s["image_path"], s["width"], s["height"], s.get("label", ""), now)
            for s in stamps
        ],
    )
    conn.commit()


def list_stamp_sources(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT src.*, COUNT(s.id) AS count FROM stamp_sources src
        LEFT JOIN stamps s ON s.source_key = src.key
        GROUP BY src.key ORDER BY src.created_at
        """
    ).fetchall()
    return [dict(row) for row in rows]


def list_stamps(conn: sqlite3.Connection, source_key: str | None = None) -> list[dict[str, Any]]:
    if source_key:
        rows = conn.execute(
            "SELECT * FROM stamps WHERE source_key = ? ORDER BY sheet, idx", (source_key,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM stamps ORDER BY source_key, sheet, idx").fetchall()
    return [dict(row) for row in rows]


def get_stamp(conn: sqlite3.Connection, stamp_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM stamps WHERE id = ?", (stamp_id,)).fetchone()
    return dict(row) if row else None


def update_stamp_label(conn: sqlite3.Connection, stamp_id: int, label: str) -> None:
    conn.execute("UPDATE stamps SET label = ? WHERE id = ?", (label, stamp_id))
    conn.commit()


def delete_stamp(conn: sqlite3.Connection, stamp_id: int) -> None:
    conn.execute("DELETE FROM stamps WHERE id = ?", (stamp_id,))
    conn.commit()


def delete_stamp_source(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM stamps WHERE source_key = ?", (key,))
    conn.execute("DELETE FROM stamp_sources WHERE key = ?", (key,))
    conn.commit()


def update_character_reference(conn: sqlite3.Connection, character_id: int, image_path: str | None) -> None:
    conn.execute("UPDATE characters SET reference_image_path = ? WHERE id = ?", (image_path, character_id))
    conn.commit()


def _migrate_stories(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(stories)")}
    for column, column_type in _STORIES_EXTRA_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE stories ADD COLUMN {column} {column_type}")
    # 同じシリーズに同じ巻番号が2つできないようにする(巻の順番を確実にするため)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_stories_series_volume"
        " ON stories(series_id, volume_no) WHERE series_id IS NOT NULL"
    )
    # 物語エディタの下書きも、どのシリーズの何巻として書いているかを持つ(「漫画にする」で巻になる)
    draft_columns = {row["name"] for row in conn.execute("PRAGMA table_info(story_drafts)")}
    for column in ("series_id", "volume_no"):
        if column not in draft_columns:
            conn.execute(f"ALTER TABLE story_drafts ADD COLUMN {column} INTEGER")
    conn.commit()


_GENERATION_HISTORY_EXTRA_COLUMNS = {
    "i2i_image_path": "TEXT",
    "i2i_strength": "REAL",
    "i2i_noise": "REAL",
    "based_on_id": "INTEGER",
    "metadata_incomplete": "INTEGER NOT NULL DEFAULT 0",
    "character_references": "TEXT",
    "characters": "TEXT",
}


def _migrate_generation_history(conn: sqlite3.Connection) -> None:
    """
    既存の data/app.db は generation_history に i2i/キャラクター系の列を持たない状態で
    作られている場合があるため、無ければ追加する（CREATE TABLE IF NOT EXISTS は既存
    テーブルには効かないため）。
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(generation_history)")}
    for column, column_type in _GENERATION_HISTORY_EXTRA_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE generation_history ADD COLUMN {column} {column_type}")
    conn.commit()


def upsert_prompt_chunks(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    """
    NovelAI 公式から取得・復号したチャンクをDBへ upsert する（同一 id は上書き）。
    items は routes/chunks.py の decrypt_object() 結果に、取得元の object id を
    remote_object_id として付加したもの。
    """
    now = datetime.now(timezone.utc).isoformat()
    for item in items:
        conn.execute(
            """
            INSERT INTO prompt_chunks
                (id, remote_object_id, container_id, label, expansion, color, is_category, child_order, version, synced_at)
            VALUES
                (:id, :remote_object_id, :container_id, :label, :expansion, :color, :is_category, :child_order, :version, :synced_at)
            ON CONFLICT(id) DO UPDATE SET
                remote_object_id = excluded.remote_object_id,
                container_id     = excluded.container_id,
                label            = excluded.label,
                expansion        = excluded.expansion,
                color            = excluded.color,
                is_category      = excluded.is_category,
                child_order      = excluded.child_order,
                version          = excluded.version,
                synced_at        = excluded.synced_at
            """,
            {
                "id": item["id"],
                "remote_object_id": item.get("remote_object_id"),
                "container_id": item.get("containerId") or None,
                "label": item.get("label", ""),
                "expansion": item.get("expansion", ""),
                "color": item.get("color"),
                "is_category": 1 if item.get("isCategory") else 0,
                "child_order": json.dumps(item["childOrder"]) if item.get("childOrder") else None,
                "version": item.get("version"),
                "synced_at": now,
            },
        )
    conn.commit()
    return len(items)


def list_prompt_chunks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM prompt_chunks ORDER BY is_category DESC, label"
    ).fetchall()
    situations_by_chunk = _situations_by_chunk(conn)
    exclusive_groups_by_chunk = _exclusive_groups_by_chunk(conn)

    result = []
    for row in rows:
        d = dict(row)
        d["is_category"] = bool(d["is_category"])
        d["child_order"] = json.loads(d["child_order"]) if d["child_order"] else None
        d["situations"] = situations_by_chunk.get(d["id"], [])
        d["exclusive_groups"] = exclusive_groups_by_chunk.get(d["id"], [])
        result.append(d)
    return result


def search_prompt_chunks(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[dict[str, Any]]:
    """ラベルの部分一致でチャンク(カテゴリ以外)を検索する。MCPツールなど、UIの検索ボックスを
    使わない呼び出し元がチャンクIDを見つけるための入り口。"""
    rows = conn.execute(
        "SELECT * FROM prompt_chunks WHERE is_category = 0 AND label LIKE ? ORDER BY label LIMIT ?",
        (f"%{query}%", limit),
    ).fetchall()
    situations_by_chunk = _situations_by_chunk(conn)
    result = []
    for row in rows:
        d = dict(row)
        d["is_category"] = bool(d["is_category"])
        d["child_order"] = None
        d["situations"] = situations_by_chunk.get(d["id"], [])
        result.append(d)
    return result


def create_custom_chunk(
    conn: sqlite3.Connection, label: str, expansion: str, situation_ids: list[int] | None = None
) -> dict[str, Any]:
    """
    NovelAI公式の同期とは独立に、自前でプロンプトチャンクを作成する（MCPの「チャンク作成」用）。
    カテゴリには属さず(container_id=NULL)、通常のカテゴリツリーには表示されない。
    """
    chunk_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO prompt_chunks
            (id, remote_object_id, container_id, label, expansion, color, is_category, child_order, version, synced_at)
        VALUES (?, NULL, NULL, ?, ?, NULL, 0, NULL, NULL, ?)
        """,
        (chunk_id, label, expansion, now),
    )
    conn.commit()
    if situation_ids:
        set_chunk_situations(conn, chunk_id, situation_ids)
    return {"id": chunk_id, "label": label, "expansion": expansion, "situations": situation_ids or []}


def _situations_by_chunk(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT pcs.chunk_id, s.id, s.name
        FROM prompt_chunk_situations pcs
        JOIN situations s ON s.id = pcs.situation_id
        ORDER BY s.name
        """
    ).fetchall()
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(row["chunk_id"], []).append({"id": row["id"], "name": row["name"]})
    return result


def list_situations(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, name FROM situations ORDER BY name").fetchall()
    return [dict(row) for row in rows]


def create_situation(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    name = name.strip()
    row = conn.execute(
        "INSERT INTO situations (name) VALUES (?) ON CONFLICT(name) DO UPDATE SET name = name RETURNING id, name",
        (name,),
    ).fetchone()
    conn.commit()
    return dict(row)


def delete_situation(conn: sqlite3.Connection, situation_id: int) -> None:
    conn.execute("DELETE FROM situations WHERE id = ?", (situation_id,))
    conn.commit()


def set_chunk_situations(conn: sqlite3.Connection, chunk_id: str, situation_ids: list[int]) -> None:
    conn.execute("DELETE FROM prompt_chunk_situations WHERE chunk_id = ?", (chunk_id,))
    conn.executemany(
        "INSERT INTO prompt_chunk_situations (chunk_id, situation_id) VALUES (?, ?)",
        [(chunk_id, sid) for sid in situation_ids],
    )
    conn.commit()


def _exclusive_groups_by_chunk(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT pceg.chunk_id, g.id, g.name
        FROM prompt_chunk_exclusive_groups pceg
        JOIN exclusive_groups g ON g.id = pceg.group_id
        ORDER BY g.name
        """
    ).fetchall()
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(row["chunk_id"], []).append({"id": row["id"], "name": row["name"]})
    return result


def list_exclusive_groups(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, name FROM exclusive_groups ORDER BY name").fetchall()
    return [dict(row) for row in rows]


def create_exclusive_group(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    name = name.strip()
    row = conn.execute(
        "INSERT INTO exclusive_groups (name) VALUES (?) ON CONFLICT(name) DO UPDATE SET name = name RETURNING id, name",
        (name,),
    ).fetchone()
    conn.commit()
    return dict(row)


def delete_exclusive_group(conn: sqlite3.Connection, group_id: int) -> None:
    conn.execute("DELETE FROM exclusive_groups WHERE id = ?", (group_id,))
    conn.commit()


def set_chunk_exclusive_groups(conn: sqlite3.Connection, chunk_id: str, group_ids: list[int]) -> None:
    conn.execute("DELETE FROM prompt_chunk_exclusive_groups WHERE chunk_id = ?", (chunk_id,))
    conn.executemany(
        "INSERT INTO prompt_chunk_exclusive_groups (chunk_id, group_id) VALUES (?, ?)",
        [(chunk_id, gid) for gid in group_ids],
    )
    conn.commit()


def find_conflicts(conn: sqlite3.Connection, chunk_ids: list[str]) -> list[dict[str, Any]]:
    """
    指定したチャンク群の中に、同じ排他グループに属するものが2件以上あれば報告する。
    ワード選択ルール実装時にこのまま流用する想定。
    """
    if not chunk_ids:
        return []
    placeholders = ",".join("?" for _ in chunk_ids)
    rows = conn.execute(
        f"""
        SELECT g.id AS group_id, g.name AS group_name, pceg.chunk_id, pc.label
        FROM prompt_chunk_exclusive_groups pceg
        JOIN exclusive_groups g ON g.id = pceg.group_id
        JOIN prompt_chunks pc ON pc.id = pceg.chunk_id
        WHERE pceg.chunk_id IN ({placeholders})
        ORDER BY g.name
        """,
        chunk_ids,
    ).fetchall()

    by_group: dict[int, dict[str, Any]] = {}
    for row in rows:
        group = by_group.setdefault(
            row["group_id"], {"group_id": row["group_id"], "group_name": row["group_name"], "members": []}
        )
        group["members"].append({"id": row["chunk_id"], "label": row["label"]})

    return [g for g in by_group.values() if len(g["members"]) > 1]


# ===== ワード選択ルール =====

def _resolve_conflicts(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同じ排他グループに属するチャンクが複数あれば、先に出てきたものだけを残す。"""
    used_groups: set[int] = set()
    result: list[dict[str, Any]] = []
    for chunk in chunks:
        group_ids = [g["id"] for g in chunk.get("exclusive_groups", [])]
        if any(gid in used_groups for gid in group_ids):
            continue
        used_groups.update(group_ids)
        result.append(chunk)
    return result


def select_by_situation(conn: sqlite3.Connection, situation_id: int) -> list[dict[str, Any]]:
    """シナリオベース選択: 指定シチュエーションが付いたチャンクを、排他グループの重複を除いて全て返す。"""
    candidates = [
        c
        for c in list_prompt_chunks(conn)
        if not c["is_category"] and any(s["id"] == situation_id for s in c["situations"])
    ]
    return _resolve_conflicts(candidates)


def select_random(
    conn: sqlite3.Connection, situation_id: int | None, count: int | None
) -> list[dict[str, Any]]:
    """ランダム選択: (任意でシチュエーション絞り込み後)排他グループの重複を除いてランダムに選ぶ。"""
    pool = [c for c in list_prompt_chunks(conn) if not c["is_category"]]
    if situation_id is not None:
        pool = [c for c in pool if any(s["id"] == situation_id for s in c["situations"])]
    random.shuffle(pool)
    resolved = _resolve_conflicts(pool)
    return resolved[:count] if count is not None else resolved


def _tag_set(expansion: str) -> set[str]:
    return {t.strip().lower() for t in expansion.split(",") if t.strip()}


def find_similar(conn: sqlite3.Connection, chunk_id: str, limit: int) -> list[dict[str, Any]]:
    """類似選択: expansion のタグ集合の Jaccard 係数でランキングする(追加インフラ不要)。"""
    all_chunks = list_prompt_chunks(conn)
    by_id = {c["id"]: c for c in all_chunks}
    ref = by_id.get(chunk_id)
    if ref is None:
        return []

    ref_tags = _tag_set(ref["expansion"])
    scored: list[dict[str, Any]] = []
    for c in all_chunks:
        if c["id"] == chunk_id or c["is_category"]:
            continue
        tags = _tag_set(c["expansion"])
        union = ref_tags | tags
        if not union:
            continue
        score = len(ref_tags & tags) / len(union)
        if score > 0:
            scored.append({**c, "score": score})

    scored.sort(key=lambda c: c["score"], reverse=True)
    return scored[:limit]


def list_presets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, name, created_at FROM presets ORDER BY name").fetchall()
    return [dict(row) for row in rows]


def _set_preset_chunks(conn: sqlite3.Connection, preset_id: int, chunk_ids: list[str]) -> None:
    conn.execute("DELETE FROM preset_chunks WHERE preset_id = ?", (preset_id,))
    conn.executemany(
        "INSERT INTO preset_chunks (preset_id, chunk_id, position) VALUES (?, ?, ?)",
        [(preset_id, cid, i) for i, cid in enumerate(chunk_ids)],
    )


def create_preset(conn: sqlite3.Connection, name: str, chunk_ids: list[str]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        "INSERT INTO presets (name, created_at) VALUES (?, ?) RETURNING id, name, created_at",
        (name.strip(), now),
    ).fetchone()
    preset = dict(row)
    _set_preset_chunks(conn, preset["id"], chunk_ids)
    conn.commit()
    return preset


def update_preset_chunks(conn: sqlite3.Connection, preset_id: int, chunk_ids: list[str]) -> None:
    _set_preset_chunks(conn, preset_id, chunk_ids)
    conn.commit()


def delete_preset(conn: sqlite3.Connection, preset_id: int) -> None:
    conn.execute("DELETE FROM presets WHERE id = ?", (preset_id,))
    conn.commit()


def get_preset(conn: sqlite3.Connection, preset_id: int) -> dict[str, Any] | None:
    preset_row = conn.execute(
        "SELECT id, name, created_at FROM presets WHERE id = ?", (preset_id,)
    ).fetchone()
    if preset_row is None:
        return None

    rows = conn.execute(
        """
        SELECT pc.id AS id, pc.label AS label, pc.expansion AS expansion, pc.color AS color
        FROM preset_chunks p
        JOIN prompt_chunks pc ON pc.id = p.chunk_id
        WHERE p.preset_id = ?
        ORDER BY p.position
        """,
        (preset_id,),
    ).fetchall()

    result = dict(preset_row)
    result["chunks"] = [dict(row) for row in rows]
    return result


def record_generation(
    conn: sqlite3.Connection,
    prompt: str,
    negative_prompt: str | None,
    model: str,
    size: str,
    steps: int,
    scale: float,
    seed: int | None,
    chunk_ids: list[str],
    image_paths: list[str],
    i2i_image_path: str | None = None,
    i2i_strength: float | None = None,
    i2i_noise: float | None = None,
    character_references: list[dict[str, Any]] | None = None,
    characters: list[dict[str, Any]] | None = None,
    based_on_id: int | None = None,
    metadata_incomplete: bool = False,
) -> dict[str, Any]:
    """
    /select 経由の画像生成だけを対象にした履歴保存。image_paths / i2i_image_path /
    character_references[].image_path はリポジトリルートからの相対パス(outputs/history/...)で、
    画像データ自体はDBに入れずファイルのまま置く。

    based_on_id: この生成が別の履歴エントリ(インポート画像やメタデータ不足のエントリなど)を
    元に再生成された場合、その元エントリの id を入れておくことで rerun の系譜を遡れるようにする。
    metadata_incomplete: インポート時にメタデータの一部/全部が読み取れず、デフォルト値で
    補完した場合に立てる。ユーザーが再生成のベースにすべきかの目印になる。
    """
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        """
        INSERT INTO generation_history
            (prompt, negative_prompt, model, size, steps, scale, seed, chunk_ids, image_paths,
             i2i_image_path, i2i_strength, i2i_noise, character_references, characters,
             based_on_id, metadata_incomplete, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id, created_at
        """,
        (
            prompt,
            negative_prompt,
            model,
            size,
            steps,
            scale,
            seed,
            json.dumps(chunk_ids),
            json.dumps(image_paths),
            i2i_image_path,
            i2i_strength,
            i2i_noise,
            json.dumps(character_references) if character_references else None,
            json.dumps(characters) if characters else None,
            based_on_id,
            1 if metadata_incomplete else 0,
            now,
        ),
    ).fetchone()
    conn.commit()
    return dict(row)


def list_generation_history(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM generation_history ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["chunk_ids"] = json.loads(d["chunk_ids"])
        d["image_paths"] = json.loads(d["image_paths"])
        d["character_references"] = json.loads(d["character_references"]) if d["character_references"] else []
        d["characters"] = json.loads(d["characters"]) if d["characters"] else []
        d["metadata_incomplete"] = bool(d["metadata_incomplete"])
        result.append(d)
    return result


def list_bookmarks(conn: sqlite3.Connection, kind: str) -> set[str]:
    rows = conn.execute("SELECT item_key FROM bookmarks WHERE kind = ?", (kind,)).fetchall()
    return {row["item_key"] for row in rows}


def set_bookmark(conn: sqlite3.Connection, kind: str, item_key: str, bookmarked: bool) -> None:
    if bookmarked:
        conn.execute(
            "INSERT OR IGNORE INTO bookmarks (kind, item_key, created_at) VALUES (?, ?, ?)",
            (kind, item_key, datetime.now(timezone.utc).isoformat()),
        )
    else:
        conn.execute("DELETE FROM bookmarks WHERE kind = ? AND item_key = ?", (kind, item_key))
    conn.commit()


def list_adult_marks(conn: sqlite3.Connection, kind: str) -> dict[str, bool]:
    rows = conn.execute("SELECT item_key, adult FROM adult_marks WHERE kind = ?", (kind,)).fetchall()
    return {row["item_key"]: bool(row["adult"]) for row in rows}


def set_adult_mark(conn: sqlite3.Connection, kind: str, item_key: str, adult: bool | None) -> None:
    """adult=None で手動指定を外し、自動判定に戻す。"""
    if adult is None:
        conn.execute("DELETE FROM adult_marks WHERE kind = ? AND item_key = ?", (kind, item_key))
    else:
        conn.execute(
            "INSERT INTO adult_marks (kind, item_key, adult) VALUES (?, ?, ?)"
            " ON CONFLICT (kind, item_key) DO UPDATE SET adult = excluded.adult",
            (kind, item_key, int(adult)),
        )
    conn.commit()


def forget_library_items(conn: sqlite3.Connection, kind: str, item_keys: list[str]) -> None:
    """削除した画像/本のブックマークと成人向けの手動指定を消す。"""
    for table in ("bookmarks", "adult_marks"):
        conn.executemany(
            f"DELETE FROM {table} WHERE kind = ? AND item_key = ?", [(kind, key) for key in item_keys]
        )
    conn.commit()


def list_scene_tags(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """成人向けの自動判定用: 全シーンの物語IDと画像生成タグ。"""
    rows = conn.execute(
        "SELECT story_id, draft_prompt_tags FROM story_scenes WHERE draft_prompt_tags != ''"
    ).fetchall()
    return [dict(row) for row in rows]


def list_story_series(conn: sqlite3.Connection) -> dict[int, int]:
    """物語ID → シリーズID(シリーズの巻だけ)。"""
    rows = conn.execute("SELECT id, series_id FROM stories WHERE series_id IS NOT NULL").fetchall()
    return {row["id"]: row["series_id"] for row in rows}


def list_story_texts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """成人向けの自動判定用: 物語ごとの本文(シーンの本文、無ければ取り込んだままの本文)。"""
    rows = conn.execute(
        """
        SELECT st.id AS story_id,
               COALESCE((SELECT GROUP_CONCAT(COALESCE(NULLIF(s.novelai_text, ''), s.draft_text), char(10))
                         FROM story_scenes s WHERE s.story_id = st.id), st.raw_text, '') AS text
        FROM stories st
        """
    ).fetchall()
    return [dict(row) for row in rows]


def get_generation_entry(conn: sqlite3.Connection, entry_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM generation_history WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        return None
    entry = dict(row)
    entry["image_paths"] = json.loads(entry["image_paths"])
    entry["character_references"] = (
        json.loads(entry["character_references"]) if entry["character_references"] else []
    )
    return entry


def set_generation_image_paths(conn: sqlite3.Connection, entry_id: int, image_paths: list[str]) -> None:
    conn.execute(
        "UPDATE generation_history SET image_paths = ? WHERE id = ?", (json.dumps(image_paths), entry_id)
    )
    conn.commit()


def delete_generation_entry(conn: sqlite3.Connection, entry_id: int) -> None:
    conn.execute("DELETE FROM generation_history WHERE id = ?", (entry_id,))
    conn.commit()


def delete_manga_panel(conn: sqlite3.Connection, panel_id: int) -> str | None:
    """漫画v2のコマを1つ消し、消したコマの画像パスを返す(無ければ None)。"""
    row = conn.execute("DELETE FROM manga_panels WHERE id = ? RETURNING image_path", (panel_id,)).fetchone()
    conn.commit()
    return row["image_path"] if row else None


def delete_manga_page(conn: sqlite3.Connection, page_id: int) -> str | None:
    """漫画v1の挿絵ページを1つ消し、消したページの画像パスを返す(無ければ None)。"""
    row = conn.execute("DELETE FROM manga_pages WHERE id = ? RETURNING image_path", (page_id,)).fetchone()
    conn.commit()
    return row["image_path"] if row else None


def delete_story(conn: sqlite3.Connection, story_id: int) -> list[str]:
    """
    物語を消す。シーン・コマ・挿絵ページの行は外部キーの CASCADE で一緒に消える
    (エディタの下書きは story_id が NULL になって残る)。消える行が指していた画像パスを返す。
    """
    paths = [
        row["image_path"]
        for row in conn.execute(
            "SELECT image_path FROM manga_panels WHERE story_id = ?"
            " UNION ALL SELECT image_path FROM manga_pages WHERE story_id = ?",
            (story_id, story_id),
        ).fetchall()
    ]
    conn.execute("DELETE FROM stories WHERE id = ?", (story_id,))
    conn.commit()
    return paths


def list_library_panels(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """ギャラリー用: 漫画v2のコマ全部と、その物語・シーンの情報。"""
    rows = conn.execute(
        """
        SELECT p.id, p.story_id, p.image_path, p.seed, p.width, p.height, p.created_at,
               s.scene_index, s.draft_title, s.draft_prompt_tags,
               COALESCE(st.title, st.premise) AS story_title
        FROM manga_panels p
        JOIN story_scenes s ON s.id = p.scene_id
        JOIN stories st ON st.id = p.story_id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def list_library_pages(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """ギャラリー/本棚用: 漫画v1(V5でページごと生成)の挿絵ページ全部と、その物語の情報。"""
    rows = conn.execute(
        """
        SELECT p.id, p.story_id, p.page_index, p.image_path, p.seed, p.created_at,
               COALESCE(st.title, st.premise) AS story_title
        FROM manga_pages p
        JOIN stories st ON st.id = p.story_id
        ORDER BY p.story_id, p.page_index
        """
    ).fetchall()
    return [dict(row) for row in rows]


def list_library_stories(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """
    本棚用: 物語ごとの概要。本文の文字数・シーン数、最初のコマの画像(表紙の候補)と、最後に手を入れた
    日時(コマ/挿絵の生成、エディタの保存のうち一番新しいもの)を付ける。

    物語ごとの相関サブクエリにすると、巻に分けて物語が増えたところで1秒近くかかったので、
    表ごとにまとめて集計してから Python で突き合わせる。
    """
    stories = [
        dict(row)
        for row in conn.execute(
            """
            SELECT st.id, st.premise, st.title, st.status, st.created_at, st.final_image_path, st.raw_text,
                   st.series_id, st.volume_no, se.title AS series_title
            FROM stories st LEFT JOIN series se ON se.id = st.series_id
            """
        ).fetchall()
    ]
    scenes = {
        row["story_id"]: row
        for row in conn.execute(
            "SELECT story_id, COUNT(*) AS n, COALESCE(SUM(LENGTH(COALESCE(NULLIF(novelai_text, ''), draft_text))), 0)"
            " AS chars FROM story_scenes GROUP BY story_id"
        ).fetchall()
    }
    first_panel: dict[int, tuple[int, str]] = {}
    for row in conn.execute(
        "SELECT p.story_id, p.image_path, s.scene_index FROM manga_panels p JOIN story_scenes s ON s.id = p.scene_id"
    ).fetchall():
        current = first_panel.get(row["story_id"])
        if current is None or row["scene_index"] < current[0]:
            first_panel[row["story_id"]] = (row["scene_index"], row["image_path"])
    latest: dict[int, str] = {}
    for query in (
        "SELECT story_id, MAX(created_at) AS t FROM manga_panels GROUP BY story_id",
        "SELECT story_id, MAX(created_at) AS t FROM manga_pages GROUP BY story_id",
        "SELECT story_id, MAX(updated_at) AS t FROM story_drafts WHERE story_id IS NOT NULL GROUP BY story_id",
    ):
        for row in conn.execute(query).fetchall():
            if row["t"] and row["t"] > latest.get(row["story_id"], ""):
                latest[row["story_id"]] = row["t"]
    for story in stories:
        counted = scenes.get(story["id"])
        story["scene_count"] = counted["n"] if counted else 0
        story["scene_chars"] = counted["chars"] if counted else 0
        story["first_panel_path"] = first_panel[story["id"]][1] if story["id"] in first_panel else None
        story["updated_at"] = max(story["created_at"], latest.get(story["id"], ""))
    return stories


def create_story(
    conn: sqlite3.Connection,
    premise: str,
    n_scenes: int,
    panels_per_page: int = 4,
    raw_text: str | None = None,
) -> dict[str, Any]:
    """
    raw_text を渡すと、シーン分割・タグ付けをまだ行っていない「取り込み済み」状態
    (status='imported')で保存する。公式サイトから取り込んだ物語を、シーン分割は
    後で(add_story_scenes を呼ぶ /split で)行いたい場合に使う。省略時は従来通り
    status='draft' で作成する。
    """
    now = datetime.now(timezone.utc).isoformat()
    status = "imported" if raw_text is not None else "draft"
    row = conn.execute(
        """
        INSERT INTO stories (premise, n_scenes, panels_per_page, status, raw_text, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        RETURNING id, premise, title, n_scenes, panels_per_page, status, raw_text, created_at
        """,
        (premise, n_scenes, panels_per_page, status, raw_text, now),
    ).fetchone()
    conn.commit()
    return dict(row)


def add_story_scenes(
    conn: sqlite3.Connection, story_id: int, scenes: list[dict[str, Any]], start_index: int = 0
) -> None:
    """
    scenes: [{"draft_title": str | None, "draft_text": str, "draft_prompt_tags": str, "novelai_text": str | None}, ...]
    scene_index は start_index からのリストの順番、page_index は stories.panels_per_page から自動算出する。
    start_index は既存シーンの後ろへ続きを追加する(冒頭だけ分割した物語の残りを分割する)ときに使う。
    novelai_text は、公式サイトからインポートした既に執筆済みの本文をそのまま使い、
    /write (Kayraによる自動執筆)をスキップする場合にのみ渡す。通常のドラフト生成
    フローでは省略し、NULLのまま/writeで埋める。
    """
    panels_per_page = conn.execute(
        "SELECT panels_per_page FROM stories WHERE id = ?", (story_id,)
    ).fetchone()["panels_per_page"]

    for i, scene in enumerate(scenes, start=start_index):
        conn.execute(
            """
            INSERT INTO story_scenes
                (story_id, scene_index, page_index, draft_title, draft_text, draft_prompt_tags, novelai_text)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                story_id,
                i,
                i // panels_per_page,
                scene.get("draft_title"),
                scene.get("draft_text", ""),
                scene.get("draft_prompt_tags", ""),
                scene.get("novelai_text"),
            ),
        )
    conn.commit()


def get_story(conn: sqlite3.Connection, story_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM stories WHERE id = ?", (story_id,)).fetchone()
    if row is None:
        return None
    story = dict(row)
    story["scenes"] = list_story_scenes(conn, story_id)
    return story


def list_story_scenes(conn: sqlite3.Connection, story_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM story_scenes WHERE story_id = ? ORDER BY scene_index", (story_id,)
    ).fetchall()
    by_scene = characters_by_scene(conn, story_id)
    return [
        {
            **dict(row),
            "sfx": json.loads(row["sfx"]) if row["sfx"] is not None else None,
            "characters": by_scene.get(row["id"], []),
        }
        for row in rows
    ]


def update_scene_narration(conn: sqlite3.Connection, scene_id: int, narration: str | None) -> None:
    """None で未設定に戻す。"""
    conn.execute("UPDATE story_scenes SET narration = ? WHERE id = ?", (narration, scene_id))
    conn.commit()


def update_scene_sfx(conn: sqlite3.Connection, scene_id: int, sfx: list[str] | None) -> None:
    """None で未設定に戻す。"""
    conn.execute(
        "UPDATE story_scenes SET sfx = ? WHERE id = ?",
        (json.dumps(sfx, ensure_ascii=False) if sfx is not None else None, scene_id),
    )
    conn.commit()


def update_scene_tags(
    conn: sqlite3.Connection, scene_id: int, draft_title: str | None, draft_prompt_tags: str
) -> None:
    """分割後に非同期で付けるタイトル/画像生成タグだけを更新する。"""
    conn.execute(
        "UPDATE story_scenes SET draft_title = ?, draft_prompt_tags = ? WHERE id = ?",
        (draft_title, draft_prompt_tags, scene_id),
    )
    conn.commit()


def update_scene_writing(conn: sqlite3.Connection, scene_id: int, seed_cue: str, novelai_text: str) -> None:
    conn.execute(
        "UPDATE story_scenes SET seed_cue = ?, novelai_text = ? WHERE id = ?",
        (seed_cue, novelai_text, scene_id),
    )
    conn.commit()


def update_story_status(conn: sqlite3.Connection, story_id: int, status: str) -> None:
    conn.execute("UPDATE stories SET status = ? WHERE id = ?", (status, story_id))
    conn.commit()


def list_characters(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM characters ORDER BY name").fetchall()
    return [dict(row) for row in rows]


def save_character(
    conn: sqlite3.Connection, name: str, appearance_tags: str, notes: str | None = None
) -> dict[str, Any]:
    """
    名前をキーに登録/更新する。抽出は同じ人物を何度も拾うので、上書きにして重複を作らない。
    容姿タグが空で来た場合は既存の値を消さない(抽出で拾えなかっただけのことがあるため)。
    """
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        """
        INSERT INTO characters (name, appearance_tags, notes, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            appearance_tags = CASE
                WHEN excluded.appearance_tags = '' THEN characters.appearance_tags
                ELSE excluded.appearance_tags
            END,
            notes = COALESCE(excluded.notes, characters.notes)
        RETURNING id, name, appearance_tags, notes, created_at
        """,
        (name, appearance_tags, notes, now),
    ).fetchone()
    conn.commit()
    return dict(row)


def delete_character(conn: sqlite3.Connection, character_id: int) -> None:
    conn.execute("DELETE FROM characters WHERE id = ?", (character_id,))
    conn.commit()


def set_scene_characters(conn: sqlite3.Connection, scene_id: int, character_ids: list[int]) -> None:
    conn.execute("DELETE FROM scene_characters WHERE scene_id = ?", (scene_id,))
    conn.executemany(
        "INSERT OR IGNORE INTO scene_characters (scene_id, character_id) VALUES (?, ?)",
        [(scene_id, character_id) for character_id in character_ids],
    )
    conn.commit()


def characters_by_scene(conn: sqlite3.Connection, story_id: int) -> dict[int, list[dict[str, Any]]]:
    """物語内の scene_id → 登場キャラの一覧。"""
    rows = conn.execute(
        """
        SELECT sc.scene_id, c.id, c.name, c.appearance_tags, c.reference_image_path
        FROM scene_characters sc
        JOIN characters c ON c.id = sc.character_id
        JOIN story_scenes s ON s.id = sc.scene_id
        WHERE s.story_id = ?
        ORDER BY c.name
        """,
        (story_id,),
    ).fetchall()

    result: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(row["scene_id"], []).append(
            {
                "id": row["id"],
                "name": row["name"],
                "appearance_tags": row["appearance_tags"],
                "reference_image_path": row["reference_image_path"],
            }
        )
    return result


def list_image_presets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM image_presets ORDER BY name").fetchall()
    return [{**dict(row), "settings": json.loads(row["settings"])} for row in rows]


def save_image_preset(conn: sqlite3.Connection, name: str, settings: dict[str, Any]) -> dict[str, Any]:
    """同じ名前のプリセットがあれば上書きする(「保存」で同名を選び直せるようにするため)。"""
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        """
        INSERT INTO image_presets (name, settings, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET settings = excluded.settings, created_at = excluded.created_at
        RETURNING id, name, settings, created_at
        """,
        (name, json.dumps(settings), now),
    ).fetchone()
    conn.commit()
    return {**dict(row), "settings": json.loads(row["settings"])}


def delete_image_preset(conn: sqlite3.Connection, preset_id: int) -> None:
    conn.execute("DELETE FROM image_presets WHERE id = ?", (preset_id,))
    conn.commit()


def update_story_layout(conn: sqlite3.Connection, story_id: int, panels_per_page: int) -> None:
    """
    1ページのコマ数を変え、既存シーンのページ割り当てを振り直す。

    実機検証: 1216x1728 のページにV5は8コマ以上を描く。4シーンしか渡さないと
    残りのコマは内容なしで埋められ、同じ構図の反復になった。8シーン渡すと
    大小のコマが混ざった漫画らしいレイアウトになったため、後から調整できるようにする。
    """
    conn.execute("UPDATE stories SET panels_per_page = ? WHERE id = ?", (panels_per_page, story_id))
    conn.execute(
        "UPDATE story_scenes SET page_index = scene_index / ? WHERE story_id = ?",
        (panels_per_page, story_id),
    )
    conn.commit()


def update_story_scene_count(conn: sqlite3.Connection, story_id: int, n_scenes: int) -> None:
    """分割の結果として決まったシーン数を記録する(取り込み時の値は暫定値のため)。"""
    conn.execute("UPDATE stories SET n_scenes = ? WHERE id = ?", (n_scenes, story_id))
    conn.commit()


def update_story_final_image(conn: sqlite3.Connection, story_id: int, final_image_path: str) -> None:
    conn.execute("UPDATE stories SET final_image_path = ? WHERE id = ?", (final_image_path, story_id))
    conn.commit()


def list_stories(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    """ブラウザ再読み込みや別セッションから過去の物語に戻れるようにするための一覧。"""
    rows = conn.execute(
        "SELECT * FROM stories ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(row) for row in rows]


def create_manga_page(
    conn: sqlite3.Connection,
    story_id: int,
    page_index: int,
    image_path: str,
    scene_ids: list[int],
    seed: int | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        """
        INSERT INTO manga_pages (story_id, page_index, image_path, scene_ids, seed, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (story_id, page_index) DO UPDATE SET image_path = excluded.image_path,
            scene_ids = excluded.scene_ids, seed = excluded.seed, created_at = excluded.created_at
        RETURNING id, story_id, page_index, image_path, scene_ids, seed, created_at
        """,
        (story_id, page_index, image_path, json.dumps(scene_ids), seed, now),
    ).fetchone()
    conn.commit()
    result = dict(row)
    result["scene_ids"] = json.loads(result["scene_ids"])
    return result


def list_manga_pages(conn: sqlite3.Connection, story_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM manga_pages WHERE story_id = ? ORDER BY page_index", (story_id,)
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["scene_ids"] = json.loads(d["scene_ids"])
        result.append(d)
    return result


def upsert_manga_panel(
    conn: sqlite3.Connection,
    story_id: int,
    scene_id: int,
    image_path: str,
    seed: int | None,
    width: int,
    height: int,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO manga_panels (story_id, scene_id, image_path, seed, width, height, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (scene_id) DO UPDATE SET image_path = excluded.image_path, seed = excluded.seed,
            width = excluded.width, height = excluded.height, created_at = excluded.created_at
        """,
        (story_id, scene_id, image_path, seed, width, height, now),
    )
    conn.commit()


def list_manga_panels(conn: sqlite3.Connection, story_id: int) -> list[dict[str, Any]]:
    """シーン順のコマ画像。scene_index も付けて返す。"""
    rows = conn.execute(
        """
        SELECT p.*, s.scene_index FROM manga_panels p
        JOIN story_scenes s ON s.id = p.scene_id
        WHERE p.story_id = ? ORDER BY s.scene_index
        """,
        (story_id,),
    ).fetchall()
    return [dict(row) for row in rows]


# ---- 物語エディタの下書き ----


def _draft_row(row: sqlite3.Row) -> dict[str, Any]:
    draft = dict(row)
    draft["settings"] = json.loads(draft["settings"] or "{}")
    return draft


def create_draft(conn: sqlite3.Connection, title: str = "") -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        "INSERT INTO story_drafts (title, created_at, updated_at) VALUES (?, ?, ?) RETURNING *",
        (title, now, now),
    ).fetchone()
    conn.commit()
    return _draft_row(row)


def duplicate_draft(conn: sqlite3.Connection, draft_id: int, title: str) -> dict[str, Any] | None:
    """下書きを丸ごと(本文・メモリ・作者メモ・設定)別の下書きとして複製する。漫画化の紐付けは引き継がない。"""
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        """
        INSERT INTO story_drafts (title, memory, author_note, text, settings, created_at, updated_at)
        SELECT ?, memory, author_note, text, settings, ?, ? FROM story_drafts WHERE id = ?
        RETURNING *
        """,
        (title, now, now, draft_id),
    ).fetchone()
    conn.commit()
    return _draft_row(row) if row else None


def get_draft(conn: sqlite3.Connection, draft_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM story_drafts WHERE id = ?", (draft_id,)).fetchone()
    return _draft_row(row) if row else None


def list_drafts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, title, story_id, created_at, updated_at, LENGTH(text) AS length,
               SUBSTR(text, 1, 60) AS preview
        FROM story_drafts ORDER BY updated_at DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def update_draft(conn: sqlite3.Connection, draft_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {"title", "memory", "author_note", "text", "settings", "story_id"}
    values = {k: (json.dumps(v, ensure_ascii=False) if k == "settings" else v) for k, v in fields.items() if k in allowed}
    if values:
        values["updated_at"] = datetime.now(timezone.utc).isoformat()
        assignments = ", ".join(f"{column} = ?" for column in values)
        conn.execute(f"UPDATE story_drafts SET {assignments} WHERE id = ?", (*values.values(), draft_id))
        conn.commit()
    return get_draft(conn, draft_id)


def delete_draft(conn: sqlite3.Connection, draft_id: int) -> None:
    conn.execute("DELETE FROM story_drafts WHERE id = ?", (draft_id,))
    conn.commit()


def upsert_push_subscription(conn: sqlite3.Connection, endpoint: str, p256dh: str, auth: str, user_agent: str) -> None:
    conn.execute(
        """
        INSERT INTO push_subscriptions (endpoint, p256dh, auth, user_agent, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET
            p256dh = excluded.p256dh, auth = excluded.auth, user_agent = excluded.user_agent
        """,
        (endpoint, p256dh, auth, user_agent, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def list_push_subscriptions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute("SELECT * FROM push_subscriptions").fetchall()]


def delete_push_subscription(conn: sqlite3.Connection, endpoint: str) -> None:
    conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    conn.commit()


# ---- シリーズ(巻) ----


def create_series(
    conn: sqlite3.Connection, title: str, source: str = "", memory: str = "", max_scenes: int = 20
) -> dict[str, Any]:
    row = conn.execute(
        "INSERT INTO series (title, source, memory, max_scenes, created_at) VALUES (?, ?, ?, ?, ?) RETURNING *",
        (title, source, memory, max_scenes, datetime.now(timezone.utc).isoformat()),
    ).fetchone()
    conn.commit()
    return dict(row)


def get_series(conn: sqlite3.Connection, series_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM series WHERE id = ?", (series_id,)).fetchone()
    return dict(row) if row else None


def list_series(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute("SELECT * FROM series ORDER BY id").fetchall()]


def update_series(conn: sqlite3.Connection, series_id: int, fields: dict[str, Any]) -> None:
    values = {k: v for k, v in fields.items() if k in {"title", "source", "memory", "max_scenes"}}
    if values:
        assignments = ", ".join(f"{column} = ?" for column in values)
        conn.execute(f"UPDATE series SET {assignments} WHERE id = ?", (*values.values(), series_id))
        conn.commit()


def delete_series_if_empty(conn: sqlite3.Connection, series_id: int) -> None:
    """最後の巻を消したシリーズを片付ける。"""
    conn.execute(
        "DELETE FROM series WHERE id = ? AND NOT EXISTS (SELECT 1 FROM stories WHERE series_id = ?)",
        (series_id, series_id),
    )
    conn.commit()


def list_series_volumes(conn: sqlite3.Connection, series_id: int) -> list[dict[str, Any]]:
    """シリーズの巻(巻番号順)。本文はあらすじ作り・結びの取り出しに使う。"""
    rows = conn.execute(
        """
        SELECT st.id, st.title, st.premise, st.volume_no, st.recap, st.status, st.raw_text,
               (SELECT COUNT(*) FROM story_scenes s WHERE s.story_id = st.id) AS scene_count
        FROM stories st
        WHERE st.series_id = ?
        ORDER BY st.volume_no
        """,
        (series_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def set_story_series(conn: sqlite3.Connection, story_id: int, series_id: int | None, volume_no: int | None) -> None:
    conn.execute(
        "UPDATE stories SET series_id = ?, volume_no = ? WHERE id = ?", (series_id, volume_no, story_id)
    )
    conn.commit()


def set_story_recap(conn: sqlite3.Connection, story_id: int, recap: str | None) -> None:
    conn.execute("UPDATE stories SET recap = ? WHERE id = ?", (recap, story_id))
    conn.commit()


def set_story_title(conn: sqlite3.Connection, story_id: int, title: str) -> None:
    conn.execute("UPDATE stories SET title = ? WHERE id = ?", (title, story_id))
    conn.commit()


def set_draft_series(conn: sqlite3.Connection, draft_id: int, series_id: int, volume_no: int) -> None:
    conn.execute(
        "UPDATE story_drafts SET series_id = ?, volume_no = ? WHERE id = ?", (series_id, volume_no, draft_id)
    )
    conn.commit()


def story_text(conn: sqlite3.Connection, story_id: int) -> str:
    """物語の本文(シーン順)。シーンが無ければ取り込んだままの本文。"""
    rows = conn.execute(
        "SELECT COALESCE(NULLIF(novelai_text, ''), draft_text) AS text FROM story_scenes"
        " WHERE story_id = ? ORDER BY scene_index",
        (story_id,),
    ).fetchall()
    if rows:
        return "\n\n".join(row["text"] for row in rows if row["text"])
    row = conn.execute("SELECT raw_text FROM stories WHERE id = ?", (story_id,)).fetchone()
    return (row["raw_text"] or "") if row else ""


# 新しい巻にそのまま写す物語の列(漫画の合成設定・吹き出しの位置などは巻ごとに同じものを使う)
_VOLUME_COPY_COLUMNS = (
    "premise",
    "panels_per_page",
    "status",
    "created_at",
    "memory",
    "manga_v2_overrides",
    "manga_v2_sfx_fonts",
    "manga_v2_sfx_stamps",
    "manga_v2_compose_settings",
)


def split_story_into_volumes(
    conn: sqlite3.Connection, story_id: int, volume_size: int, remaining_raw_text: str = ""
) -> list[int]:
    """
    シーンを volume_size ずつ別の物語(巻)へ移す。1巻目は元の物語のまま残し、2巻目以降を新しく作る。
    戻り値は巻の物語ID(巻順)。シーンの行ごと移すので、シーンに付いた登場人物・漫画v2のコマも一緒に移る。
    漫画v1の挿絵ページは、含むシーンの巻へページ番号を振り直して移す(volume_size はページのコマ数の
    倍数にしておくこと。そうでないとページが巻をまたぐ)。

    各巻の raw_text はその巻のシーンの本文にする(元の全文のままだと「未分割の残り」と誤認されるため)。
    remaining_raw_text はまだシーンにしていない残りの本文で、最後の巻に付けて「続きを分割」できるようにする。
    1つのトランザクションで行い、途中で失敗したら何も変えない。
    """
    story = conn.execute("SELECT * FROM stories WHERE id = ?", (story_id,)).fetchone()
    if story is None:
        raise ValueError("story not found")
    total = conn.execute("SELECT COUNT(*) FROM story_scenes WHERE story_id = ?", (story_id,)).fetchone()[0]
    per_page = story["panels_per_page"]
    starts = list(range(0, total, volume_size))
    ids = [story_id]
    try:
        for start in starts[1:]:
            end = min(start + volume_size, total) - 1
            columns = ", ".join(_VOLUME_COPY_COLUMNS)
            new_id = conn.execute(
                f"INSERT INTO stories ({columns}, n_scenes) SELECT {columns}, ? FROM stories WHERE id = ? RETURNING id",
                (end - start + 1, story_id),
            ).fetchone()[0]
            moved = [
                row[0]
                for row in conn.execute(
                    "SELECT id FROM story_scenes WHERE story_id = ? AND scene_index BETWEEN ? AND ?",
                    (story_id, start, end),
                ).fetchall()
            ]
            conn.execute(
                "UPDATE story_scenes SET story_id = ?, scene_index = scene_index - ?,"
                " page_index = (scene_index - ?) / ? WHERE story_id = ? AND scene_index BETWEEN ? AND ?",
                (new_id, start, start, per_page, story_id, start, end),
            )
            conn.executemany("UPDATE manga_panels SET story_id = ? WHERE scene_id = ?", [(new_id, s) for s in moved])
            first_page, last_page = start // per_page, end // per_page
            conn.execute(
                "UPDATE manga_pages SET story_id = ?, page_index = page_index - ?"
                " WHERE story_id = ? AND page_index BETWEEN ? AND ?",
                (new_id, first_page, story_id, first_page, last_page),
            )
            ids.append(new_id)
        # 1巻目は volume_size シーンだけになる。全体をつないだ完成画像は内容と合わなくなるので外す
        # (ファイルは消さない)。漫画v2は合成し直せば巻ごとの完成画像ができる。
        conn.execute(
            "UPDATE stories SET n_scenes = ?, final_image_path = NULL WHERE id = ?",
            (min(volume_size, total), story_id),
        )
        for index, volume_id in enumerate(ids):
            texts = [
                row[0]
                for row in conn.execute(
                    "SELECT draft_text FROM story_scenes WHERE story_id = ? ORDER BY scene_index", (volume_id,)
                ).fetchall()
            ]
            raw = "\n\n".join(texts)
            if index == len(ids) - 1 and remaining_raw_text.strip():
                raw += "\n\n" + remaining_raw_text.lstrip()
            conn.execute("UPDATE stories SET raw_text = ? WHERE id = ?", (raw, volume_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return ids


# ---- 作品の関連(作者・スピンオフ・クロスオーバー) ----


def create_author(conn: sqlite3.Connection, name: str, platform: str = "", url: str = "", note: str = "") -> dict[str, Any]:
    row = conn.execute(
        "INSERT INTO authors (name, platform, url, note, created_at) VALUES (?, ?, ?, ?, ?) RETURNING *",
        (name, platform, url, note, datetime.now(timezone.utc).isoformat()),
    ).fetchone()
    conn.commit()
    return dict(row)


def list_authors(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT a.*, (SELECT COUNT(*) FROM work_authors wa WHERE wa.author_id = a.id) AS work_count
        FROM authors a ORDER BY a.name
        """
    ).fetchall()
    return [dict(row) for row in rows]


def get_author(conn: sqlite3.Connection, author_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM authors WHERE id = ?", (author_id,)).fetchone()
    return dict(row) if row else None


def update_author(conn: sqlite3.Connection, author_id: int, fields: dict[str, Any]) -> None:
    values = {k: v for k, v in fields.items() if k in {"name", "platform", "url", "note"}}
    if values:
        assignments = ", ".join(f"{column} = ?" for column in values)
        conn.execute(f"UPDATE authors SET {assignments} WHERE id = ?", (*values.values(), author_id))
        conn.commit()


def delete_author(conn: sqlite3.Connection, author_id: int) -> None:
    conn.execute("DELETE FROM authors WHERE id = ?", (author_id,))
    conn.commit()


def add_work_author(conn: sqlite3.Connection, work_kind: str, work_id: int, author_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO work_authors (work_kind, work_id, author_id) VALUES (?, ?, ?)",
        (work_kind, work_id, author_id),
    )
    conn.commit()


def remove_work_author(conn: sqlite3.Connection, work_kind: str, work_id: int, author_id: int) -> None:
    conn.execute(
        "DELETE FROM work_authors WHERE work_kind = ? AND work_id = ? AND author_id = ?",
        (work_kind, work_id, author_id),
    )
    conn.commit()


def list_work_authors(conn: sqlite3.Connection) -> dict[tuple[str, int], list[dict[str, Any]]]:
    """(work_kind, work_id) → 作者の一覧。"""
    rows = conn.execute(
        """
        SELECT wa.work_kind, wa.work_id, a.id, a.name, a.platform, a.url
        FROM work_authors wa JOIN authors a ON a.id = wa.author_id
        ORDER BY a.name
        """
    ).fetchall()
    result: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault((row["work_kind"], row["work_id"]), []).append(
            {"id": row["id"], "name": row["name"], "platform": row["platform"], "url": row["url"]}
        )
    return result


def add_work_relation(
    conn: sqlite3.Connection, type_: str, from_kind: str, from_id: int, to_kind: str, to_id: int
) -> dict[str, Any] | None:
    """同じ関連が既にあれば None。"""
    row = conn.execute(
        "INSERT OR IGNORE INTO work_relations (type, from_kind, from_id, to_kind, to_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?) RETURNING *",
        (type_, from_kind, from_id, to_kind, to_id, datetime.now(timezone.utc).isoformat()),
    ).fetchone()
    conn.commit()
    return dict(row) if row else None


def delete_work_relation(conn: sqlite3.Connection, relation_id: int) -> bool:
    deleted = conn.execute("DELETE FROM work_relations WHERE id = ?", (relation_id,)).rowcount
    conn.commit()
    return deleted > 0


def find_work_relation(
    conn: sqlite3.Connection, type_: str, a_kind: str, a_id: int, b_kind: str, b_id: int
) -> dict[str, Any] | None:
    """a→b か b→a の関連(クロスオーバーの重複や、スピンオフの逆向きを見つける用)。"""
    row = conn.execute(
        "SELECT * FROM work_relations WHERE type = ? AND ("
        " (from_kind = ? AND from_id = ? AND to_kind = ? AND to_id = ?)"
        " OR (from_kind = ? AND from_id = ? AND to_kind = ? AND to_id = ?))",
        (type_, a_kind, a_id, b_kind, b_id, b_kind, b_id, a_kind, a_id),
    ).fetchone()
    return dict(row) if row else None


def list_work_relations(conn: sqlite3.Connection, work_kind: str, work_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM work_relations WHERE (from_kind = ? AND from_id = ?) OR (to_kind = ? AND to_id = ?)"
        " ORDER BY created_at",
        (work_kind, work_id, work_kind, work_id),
    ).fetchall()
    return [dict(row) for row in rows]


def forget_work(conn: sqlite3.Connection, work_kind: str, work_id: int) -> None:
    """消えた作品の作者・関連を外す(関連の相手の作品は残る)。"""
    conn.execute("DELETE FROM work_authors WHERE work_kind = ? AND work_id = ?", (work_kind, work_id))
    conn.execute(
        "DELETE FROM work_relations WHERE (from_kind = ? AND from_id = ?) OR (to_kind = ? AND to_id = ?)",
        (work_kind, work_id, work_kind, work_id),
    )
    conn.commit()


def move_work(conn: sqlite3.Connection, old_kind: str, old_id: int, new_kind: str, new_id: int) -> None:
    """
    作品の作者・関連を別の作品へ付け替える(単巻の物語がシリーズになったとき)。付け替えた結果
    自分自身との関連になるものや、既にある関連と重なるものは捨てる。
    """
    conn.execute(
        "INSERT OR IGNORE INTO work_authors (work_kind, work_id, author_id)"
        " SELECT ?, ?, author_id FROM work_authors WHERE work_kind = ? AND work_id = ?",
        (new_kind, new_id, old_kind, old_id),
    )
    for side, other in (("from", "to"), ("to", "from")):
        conn.execute(
            f"UPDATE OR IGNORE work_relations SET {side}_kind = ?, {side}_id = ?"
            f" WHERE {side}_kind = ? AND {side}_id = ? AND NOT ({other}_kind = ? AND {other}_id = ?)",
            (new_kind, new_id, old_kind, old_id, new_kind, new_id),
        )
    forget_work(conn, old_kind, old_id)
