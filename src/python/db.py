from __future__ import annotations

import json
import os
import random
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generic, TypeVar, cast
from uuid import uuid4

from .client import app_mode

# single の DB。multi では全体の DB(招待・利用者・ログインのセッション。sessions.py)になり、利用者のデータは
# 利用者ごとの DB(user_db_path)に置く(docs/multi_user_plan_jp.md のステップ 3)
_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "app.db"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 今のリクエストの利用者(multi。sessions.SessionMiddleware が入れる)。切り離した処理(asyncio のタスク・
# スレッド)にも、作ったときの値が引き継がれる
_current_user: ContextVar[int | None] = ContextVar("current_user", default=None)

K = TypeVar("K")
V = TypeVar("V")


class NoUserError(RuntimeError):
    """multi で、利用者が決まっていないのに利用者のデータを開こうとした。"""


def user_db_path(user_id: int) -> Path:
    return _DB_PATH.parent / "users" / str(int(user_id)) / "app.db"


def current_user_id() -> int | None:
    return _current_user.get()


def db_path() -> Path:
    """
    今の利用者のデータの DB。single は _DB_PATH。multi で利用者が決まっていなければ NoUserError
    (運営者の前のデータや、ほかの人のデータを誤って開かないように)。
    """
    if app_mode() != "multi":
        return _DB_PATH
    user_id = _current_user.get()
    if user_id is None:
        raise NoUserError("利用者が決まっていないので、データを開けません(APP_MODE=multi)")
    return user_db_path(user_id)


def user_root() -> Path:
    """
    今の利用者のファイル(画像・取り込んだ作品・スタンプ など)を置くフォルダ。DB に入っている相対パス
    (outputs/manga/... など)はここからのもの。single はプロジェクトのフォルダ、multi はその人の DB のとなり
    (data/users/{id}/)。multi で利用者が決まっていなければ NoUserError(共有の場所へ書かない)。
    モデル・フォント・証明書など、サーバー全体で使うものはここではなくプロジェクトのフォルダに置く。
    """
    if app_mode() != "multi":
        return _PROJECT_ROOT
    return db_path().parent


class UserPath(os.PathLike[str]):
    """
    使うたびに、今の利用者のフォルダ(user_root)の下を指すパス。各モジュールの _PROJECT_ROOT や、そこから
    つないだフォルダの定数(_PROJECT_ROOT / "outputs" / "manga" など)をこれにすると、使う側を書き換えずに
    利用者ごとのフォルダを読み書きできる(テストで本物の Path に差し替えるのもそのまま)。Path のメソッド
    (mkdir・glob・is_file・resolve など)は、その時点の本物の Path に任せる。
    """

    def __init__(self, *parts: str | os.PathLike[str]) -> None:
        self._parts = parts

    def real(self) -> Path:
        return user_root().joinpath(*self._parts)

    def __truediv__(self, other: str | os.PathLike[str]) -> UserPath:
        return UserPath(*self._parts, other)

    def __fspath__(self) -> str:
        return str(self.real())

    def __str__(self) -> str:
        return str(self.real())

    def __repr__(self) -> str:
        return f"UserPath({'/'.join(map(str, self._parts))!r})"

    def __eq__(self, other: object) -> bool:
        return self.real() == (other.real() if isinstance(other, UserPath) else other)

    def __hash__(self) -> int:
        return hash(self.real())

    def __getattr__(self, name: str) -> Any:
        return getattr(self.real(), name)


def user_files() -> Path:
    """今の利用者のフォルダを指す UserPath(型は Path として扱う)。"""
    return cast(Path, UserPath())


@contextmanager
def as_user(user_id: int | None) -> Iterator[None]:
    """この中では、その利用者のデータを開く(ミドルウェア・スクリプト・テストで使う)。"""
    token = _current_user.set(user_id)
    try:
        yield
    finally:
        _current_user.reset(token)


class PerUser(Generic[K, V]):
    """
    メモリの中の一覧(動いているジョブなど)を、利用者ごとに分ける入れ物。中では (今の利用者, キー) をキーにする。
    DB の ID(物語の ID など)をキーにするものは、multi では利用者ごとに DB が分かれて ID がぶつかるので要る。
    ランダムな ID(ジョブの ID)をキーにするものも、ID を知ったほかの人から見えないようにする。見えるのは今の利用者の
    ものだけ(single では利用者が None なので、今までどおり全部)。
    """

    def __init__(self) -> None:
        self._items: dict[tuple[int | None, K], V] = {}

    def _key(self, key: K) -> tuple[int | None, K]:
        return (_current_user.get(), key)

    def get(self, key: K) -> V | None:
        return self._items.get(self._key(key))

    def __getitem__(self, key: K) -> V:
        return self._items[self._key(key)]

    def __setitem__(self, key: K, value: V) -> None:
        self._items[self._key(key)] = value

    def __contains__(self, key: object) -> bool:
        return (_current_user.get(), key) in self._items

    def pop(self, key: K, default: V | None = None) -> V | None:
        return self._items.pop(self._key(key), default)

    def items(self) -> list[tuple[K, V]]:
        user = _current_user.get()
        return [(key, value) for (owner, key), value in self._items.items() if owner == user]

    def values(self) -> list[V]:
        return [value for _, value in self.items()]


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    return conn


def get_connection() -> sqlite3.Connection:
    """今の利用者のデータの DB を開く(db_path)。"""
    return _connect(db_path())


def get_system_connection() -> sqlite3.Connection:
    """全体の DB を開く(招待・利用者・セッション。どの利用者のリクエストからでも同じ)。"""
    return _connect(_DB_PATH)


def get_guard_connection() -> sqlite3.Connection:
    """
    コンテンツガードの設定(判定の設定・パスワード・プロファイル)の DB。multi では運営者が全員分を決めるので
    全体の DB(ステップ 4b)。single では全体の DB と利用者の DB は同じファイル。ガードの記録やキャラの年齢区分は
    その人のデータなので、利用者の DB(get_connection)のまま。
    """
    return _connect(_DB_PATH)


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
    # データセット生成のガードの上乗せ分。基本のブロックリスト(character_sheet.py)は
    # コード側で固定し、ここには利用者が追加したタグとネガティブだけを持つ。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guard_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            blocked_tags TEXT NOT NULL DEFAULT '[]',
            negative_tags TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    # コンテンツガードの定義(content_guard.py。値は JSON)。はじめの値は content_guard_defaults.json から入れる
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS content_guard_rules (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    # コンテンツガードを緩めるときのパスワード(guard_password.py)。パスワードそのものではなく、塩と scrypt の値だけ
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guard_secret (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            salt TEXT NOT NULL,
            hash TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    # 複数ユーザー(APP_MODE=multi。sessions.py): 招待・利用者・ログインのセッション。メールアドレスは保存せず、
    # サーバーの秘密鍵で変換した値(email_hash)で見分ける。トークンと keystore の鍵は利用者ごとの鍵で暗号化して持つ
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_invites (
            email_hash TEXT PRIMARY KEY,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    # 管理者(開発管理者のページを使える人。ステップ 4)。招待に付けるので、ログインの前から決めておける
    if "is_admin" not in {row["name"] for row in conn.execute("PRAGMA table_info(user_invites)")}:
        conn.execute("ALTER TABLE user_invites ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_hash TEXT NOT NULL UNIQUE,
            wrapped_key BLOB NOT NULL,
            novelai_api_token BLOB,
            created_at TEXT NOT NULL,
            last_login_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            access_token BLOB NOT NULL,
            encryption_key BLOB,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
        """
    )
    # GPU の利用の回数(multi。usage.py。全体の DB に置く): 利用者・日付・種類ごと
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_counts (
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            kind TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, day, kind)
        )
        """
    )
    # 監査ログ(multi。sessions.audit。全体の DB に置く): ログイン・招待や管理者の変更・管理の操作。作品の中身は残さない
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            user_id INTEGER,
            actor TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL,
            target TEXT NOT NULL DEFAULT '',
            detail TEXT NOT NULL DEFAULT '{}',
            ip TEXT NOT NULL DEFAULT ''
        )
        """
    )
    # うごイラ(ugoira.py): 動き(描き直す範囲・コマごとのタグと表示時間)と、作ったもの(コマの並び。JSON)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ugoira_motions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            region TEXT NOT NULL,
            frames TEXT NOT NULL,
            delays TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE TABLE IF NOT EXISTS ugoira_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ugoira_projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            source_key TEXT,
            method TEXT NOT NULL,
            frames TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    # 画像・作品データのバックアップの設定(backup.py。写し先・データセットを入れるか・自動か)。1行だけ
    conn.execute("CREATE TABLE IF NOT EXISTS backup_config (id INTEGER PRIMARY KEY CHECK (id = 1), value TEXT NOT NULL)")
    # ごみ箱(trash.py): 消したものの行(JSON)と、預かったファイル([元の場所, 預けた名前, 大きさ] の並び)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trash_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            label TEXT NOT NULL,
            payload TEXT NOT NULL,
            files TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
        """
    )
    # 未成年キャラの安全対策で止めた・全年齢にした記録(minor_guard.py)。どこで止まったかを管理ページで見る
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guard_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            action TEXT NOT NULL,
            story_id INTEGER,
            place TEXT NOT NULL,
            detail TEXT NOT NULL
        )
        """
    )
    # 出版物(publish.py): 選んだ画像・本から NovelAI の生成情報を取り除いて別に保存し、ZIP にまとめたもの。
    # dir / zip_path はリポジトリのルートからのパス。items は入れたもの(JSON)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS publications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            dir TEXT,
            zip_path TEXT,
            bytes INTEGER NOT NULL DEFAULT 0,
            file_count INTEGER NOT NULL DEFAULT 0,
            items TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        )
        """
    )
    # アプリの既定値のうち、開発管理者のページで変えたもの(app_settings.py。値は JSON)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
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
    # 漫画の取り込み(構成の参考)。ページ画像は data/imports/{id}/ に置き、コマの位置と読み取った
    # 構成(人数・構図・セリフ量・役割・感情)を analysis(JSON)に持つ。セリフや筋書きは持たない。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS manga_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            page_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'imported',
            analysis TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    # 漫画のジャンル(漫画ドラフトのジャンル欄と、取り込みの割り当ての候補)。画面から足したり消したりできる。
    # 初めて表を作るときだけ、既定のジャンルを入れる(消したものが、起動のたびに戻らないように)
    first_genres = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'manga_genres'").fetchone() is None
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS manga_genres (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """
    )
    if first_genres:
        for name in DEFAULT_MANGA_GENRES:
            add_manga_genre(conn, name)
    # キャラ別データセットのバリエーション(構図・ポーズ・服装・表情・場所)の候補。画面から足したり消したりできる。
    # 初めて表を作るときだけ、既定の候補を入れる(消したものが、起動のたびに戻らないように)
    first_variations = (
        conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'dataset_variations'").fetchone() is None
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dataset_variations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            axis TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (axis, value)
        )
        """
    )
    if first_variations:
        from .character_sheet import EXPRESSIONS, FRAMINGS, LOCATIONS, OUTFITS, POSES

        for axis, values in (
            ("framings", FRAMINGS), ("poses", POSES), ("outfits", OUTFITS), ("expressions", EXPRESSIONS), ("locations", LOCATIONS)
        ):
            add_dataset_variations(conn, axis, values)
    # キャラ別データセットのシチュエーション(場所・服装・ポーズなどをひとまとまりにしたもの)と状態(容姿を一時的に
    # 変えるもの)。初めて表を作るときだけ、例を入れる(消したものが、起動のたびに戻らないように)
    first_scenes = (
        conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'dataset_situations'").fetchone() is None
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dataset_situations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            axes TEXT NOT NULL DEFAULT '{}',
            extra_tags TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dataset_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            add_tags TEXT NOT NULL DEFAULT '',
            remove_tags TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    # キャラシートの素体(body_base.py)。キャラは characters.body_base_id でどれか1つを当てる
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS body_bases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL DEFAULT 'suit',
            sex TEXT NOT NULL DEFAULT 'female',
            skin_tone TEXT NOT NULL DEFAULT '',
            build TEXT NOT NULL DEFAULT '',
            height TEXT NOT NULL DEFAULT '',
            proportion TEXT NOT NULL DEFAULT '',
            chest TEXT NOT NULL DEFAULT '',
            muscle TEXT NOT NULL DEFAULT '',
            extra_tags TEXT NOT NULL DEFAULT '',
            negative_tags TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    # 素体の設定の選択肢(性別・肌の色・体格 などのプルダウン)。素体の設定のページで編集する。
    # rear_tags は真後ろの部位で tags の代わりに使うタグ(NULL なら tags のまま、空なら何も入れない)。
    # 初めて表を作るときだけ、はじめの選択肢を入れる(消したものが、起動のたびに戻らないように)
    first_body_options = (
        conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'body_base_options'").fetchone() is None
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS body_base_options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            field TEXT NOT NULL,
            value TEXT NOT NULL,
            label TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '',
            rear_tags TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE (field, value)
        )
        """
    )
    if first_body_options:
        now = datetime.now(timezone.utc).isoformat()
        for field, choices in DEFAULT_BODY_BASE_OPTIONS.items():
            for order, (value, label, tags, rear_tags) in enumerate(choices):
                conn.execute(
                    "INSERT INTO body_base_options (field, value, label, tags, rear_tags, sort_order, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (field, value, label, tags, rear_tags, order, now),
                )
    # 衣装(wardrobe.py): 部品(部位ごとの服の種類・色・柄)と、キャラごとの衣装。部品は衣装の設定のページで編集する。
    # 初めて表を作るときだけ、はじめの部品を入れる(消したものが、起動のたびに戻らないように)
    first_wardrobe = (
        conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'wardrobe_parts'").fetchone() is None
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wardrobe_parts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slot TEXT NOT NULL,
            value TEXT NOT NULL,
            label TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '',
            colorable INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE (slot, value)
        )
        """
    )
    # parts は [{slot, value, color, pattern}](JSON)。キャラの普段の衣装は characters.outfit_id
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS character_outfits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            character_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            parts TEXT NOT NULL DEFAULT '[]',
            extra_tags TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_character_outfits_character ON character_outfits(character_id)")
    if first_wardrobe:
        from .wardrobe import DEFAULT_PARTS

        now = datetime.now(timezone.utc).isoformat()
        for slot, choices in DEFAULT_PARTS.items():
            for order, (value, label, tags, colorable) in enumerate(choices):
                conn.execute(
                    "INSERT INTO wardrobe_parts (slot, value, label, tags, colorable, sort_order, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (slot, value, label, tags, int(colorable), order, now),
                )
    # 状態で描き直す部位(body_parts.PARTS のキー。none はプロンプトだけで作る)
    if "part" not in {row["name"] for row in conn.execute("PRAGMA table_info(dataset_states)")}:
        conn.execute("ALTER TABLE dataset_states ADD COLUMN part TEXT NOT NULL DEFAULT 'none'")
        # 例として入れた状態には、描き直す部位を入れる
        for item in DEFAULT_DATASET_STATES:
            conn.execute("UPDATE dataset_states SET part = ? WHERE name = ?", (item.get("part", "none"), item["name"]))
    if first_scenes:
        for item in DEFAULT_DATASET_SITUATIONS:
            save_dataset_situation(conn, None, item["name"], item["axes"], item.get("extra_tags", ""))
        for item in DEFAULT_DATASET_STATES:
            save_dataset_state(conn, None, item["name"], item["add_tags"], item["remove_tags"], item.get("part", "none"))
    # 描き直しで作る人物の状態(透明 など)。初めて表を作るときだけ、例を入れる
    first_redraw_states = (
        conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'panel_redraw_states'").fetchone() is None
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS panel_redraw_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            match_tags TEXT NOT NULL DEFAULT '',
            redraw_tags TEXT NOT NULL DEFAULT '',
            look TEXT NOT NULL DEFAULT 'none',
            created_at TEXT NOT NULL
        )
        """
    )
    if first_redraw_states:
        for item in DEFAULT_REDRAW_STATES:
            save_redraw_state(conn, None, item["name"], item["match_tags"], item["redraw_tags"], item["look"])
    # キャストのセット(よく使うキャラの組み合わせ)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cast_presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            character_ids TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        )
        """
    )
    # 話の流れのセット(夏休み など、アニメでよくある巻ごとの流れ)。初めて表を作るときだけ、既定のセットを入れる
    first_arcs = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'story_arcs'").fetchone() is None
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS story_arcs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            beats TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        )
        """
    )
    if first_arcs:
        for arc in DEFAULT_STORY_ARCS:
            save_story_arc(conn, None, arc["name"], arc["description"], arc["beats"])
    conn.commit()
    _migrate_generation_history(conn)
    _migrate_stories(conn)
    _migrate_manga_pages(conn)
    _migrate_story_scenes(conn)
    _migrate_manga_panels(conn)
    _migrate_scene_characters(conn)
    _migrate_characters(conn)
    _migrate_stamp_sources(conn)
    _migrate_manga_imports(conn)


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
    # 漫画v2のページごとのコマ割り(取り込んだ作品から写したもの)。JSON [[[x0, y0, x1, y1], ...], ...]
    # (ページに対する割合、読む順)。あればテンプレートの代わりに使う。
    "manga_v2_page_layouts": "TEXT",
    # 漫画v2で動かすコマ(うごイラを嵌め込むシーン)。JSON {シーンID: うごイラのID}
    "manga_v2_ugoira": "TEXT",
    # シリーズの巻。series_id が NULL なら単巻。(series_id, volume_no) は一意。
    "series_id": "INTEGER REFERENCES series(id) ON DELETE SET NULL",
    "volume_no": "INTEGER",
    # この巻のあらすじ(次の巻を書くときに「これまでのあらすじ」として渡す)。手で直せる。
    "recap": "TEXT",
    # 物語エディタのメモリ(世界観・登場人物)。登場人物の抽出で容姿の手がかりに使う。
    "memory": "TEXT",
    # ページの区切りをシーンごとに決めた(編集画面で個別に区切った)か。1 なら漫画v2の合成も
    # story_scenes.page_index のページごとに分ける。0 なら panels_per_page / テンプレートのコマ数で割り付ける
    "custom_pages": "INTEGER NOT NULL DEFAULT 0",
    # 漫画v2のコマを描くときのネガティブ(物語全体)と、ページごとのネガティブ(JSON {ページ番号: 文})。
    # 入れてあれば、細かい方(コマ > ページ > 物語 > 生成の設定)を使う
    "manga_v2_negative": "TEXT",
    "manga_v2_page_negatives": "TEXT",
    # 成長段階: この巻で使う人物の年齢ごとのキャラシート。JSON {"age": 歳 か null, "stages": {人物のID: 段階のキャラID}}。
    # シーンの登場キャラがその人物(元のキャラかどの段階)なら、選んだ段階の見た目で描く(characters_by_scene)
    "manga_v2_growth": "TEXT",
}


# 生成に使ったシード。良いページが出たときに引き直しの当たりを再現できるようにする。
_MANGA_PAGES_EXTRA_COLUMNS = {"seed": "INTEGER"}

# stale: 絵を描いたあとでシーンのタグ・登場人物を編集した(描き直し待ち)。絵は使えるが、コマの生成で
# 「生成済みを飛ばす」ときも描き直しの対象にする。描き直すと 0 に戻る
_MANGA_PANELS_EXTRA_COLUMNS = {"stale": "INTEGER NOT NULL DEFAULT 0"}


def _migrate_manga_panels(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(manga_panels)")}
    for column, column_type in _MANGA_PANELS_EXTRA_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE manga_panels ADD COLUMN {column} {column_type}")
    conn.commit()


def _migrate_manga_pages(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(manga_pages)")}
    for column, column_type in _MANGA_PAGES_EXTRA_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE manga_pages ADD COLUMN {column} {column_type}")
    conn.commit()


# 漫画v2で描き文字にする効果音(JSONの文字列配列)。NULL は未設定(AI提案の対象)、
# 空配列は「このシーンには効果音を付けない」と決めた状態。
# narration は漫画v2のナレーション枠の文。NULL は未設定、空文字は「ナレーションなし」。
# dialogue は編集画面で1行ずつ決めたセリフ(JSON [{"text": str, "speaker_id": int | null}])。NULL なら
# 本文の「」から取り出し、話し手も推定する(従来どおり)。
# panel_size は漫画v2のコマの生成サイズ(portrait / landscape / square)。NULL なら入る枠の形から自動で決める。
# negative_prompt はそのコマを描くときのネガティブ。入れてあればページ・物語のネガティブより優先する。
# overlays は漫画v2でコマの絵に重ねるもの(文字・別のコマの絵)。JSON [{kind, quad, ...}](manga_v2/overlay.py)
# absent_tags はそのコマに出てはいけない物(まだもらっていない小物など。英語タグ)。描くときにネガティブへ足し
# (コマのネガティブと違い、ページ・物語のネガティブを置き換えない)、コマの確かめで描かれていないかを見る。
_STORY_SCENES_EXTRA_COLUMNS = {
    "sfx": "TEXT", "narration": "TEXT", "dialogue": "TEXT", "panel_size": "TEXT", "negative_prompt": "TEXT",
    "overlays": "TEXT", "absent_tags": "TEXT",
}


def _migrate_story_scenes(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(story_scenes)")}
    for column, column_type in _STORY_SCENES_EXTRA_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE story_scenes ADD COLUMN {column} {column_type}")
    conn.commit()


def _migrate_scene_characters(conn: sqlite3.Connection) -> None:
    """action_tags: そのコマでのそのキャラの表情・動作(英語タグ)。コマ生成でキャラごとのプロンプトに入れる。"""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(scene_characters)")}
    if "action_tags" not in existing:
        conn.execute("ALTER TABLE scene_characters ADD COLUMN action_tags TEXT")
    # outfit_tags: そのコマでのそのキャラの服装(水着・浴衣など)。あれば、キャラシートのふだんの服装の代わりに使う
    if "outfit_tags" not in existing:
        conn.execute("ALTER TABLE scene_characters ADD COLUMN outfit_tags TEXT")
    # state_id: そのコマでのそのキャラの状態(キャラ別データセットの状態。容姿を一時的に変える)
    if "state_id" not in existing:
        conn.execute("ALTER TABLE scene_characters ADD COLUMN state_id INTEGER")
    conn.commit()


# reference_image_path は漫画v2のキャラ参照(NovelAIのCharacter Reference)に使う画像。
# 残りはキャラシート(同じ見た目で安定して出すための設定)。appearance_tags は変えない容姿、
# outfit_tags は普段の服装(場面やデータセットで差し替える)、style_tags は画風/品質、
# negative_tags はそのキャラで出てほしくない要素、seed は基準シード。
_CHARACTERS_EXTRA_COLUMNS = {
    "reference_image_path": "TEXT",
    # 標準の体型(キャラ別データセットの状態は、ここを置き換える。顔・髪・目は appearance_tags)
    "body_tags": "TEXT",
    "trigger_word": "TEXT",
    "outfit_tags": "TEXT",
    "style_tags": "TEXT",
    "negative_tags": "TEXT",
    "seed": "INTEGER",
    # 成人キャラ。1 のときだけデータセットのR18生成を許す(未成年を示すタグがない場合に限る)
    "is_adult": "INTEGER NOT NULL DEFAULT 0",
    # 未成年キャラ(定義してあれば、性的な場面には出さない)。is_adult と同時には立たない
    "is_minor": "INTEGER NOT NULL DEFAULT 0",
    # 成長段階: 同じ人物の年齢ごとのキャラシート(新生児・1歳 など)を、元のキャラにつなぐ。
    # growth_of は元のキャラの ID(元のキャラ自身は NULL)、growth_label は段階の名前、growth_order は並べる順
    "growth_of": "INTEGER",
    "growth_label": "TEXT",
    "growth_order": "INTEGER",
    # 年齢(歳)。入れてあれば、18 歳未満は未成年・18 歳以上は成人として扱う(年齢区分をこれに合わせる)
    "age": "INTEGER",
    # 当てる素体(body_bases の id)。キャラシートの「素体」で選ぶ
    "body_base_id": "INTEGER",
    # 普段の衣装(character_outfits の id)。選ぶと、その衣装のタグを outfit_tags に入れる
    "outfit_id": "INTEGER",
}


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


def get_manga_v2_ugoira(conn: sqlite3.Connection, story_id: int) -> dict[int, int]:
    """動かすコマ。{シーンID: うごイラのID}"""
    row = conn.execute("SELECT manga_v2_ugoira FROM stories WHERE id = ?", (story_id,)).fetchone()
    raw = json.loads(row["manga_v2_ugoira"]) if row and row["manga_v2_ugoira"] else {}
    return {int(scene_id): int(project_id) for scene_id, project_id in raw.items()}


def set_manga_v2_ugoira(conn: sqlite3.Connection, story_id: int, links: dict[int, int]) -> None:
    conn.execute(
        "UPDATE stories SET manga_v2_ugoira = ? WHERE id = ?",
        (json.dumps({str(k): v for k, v in links.items()}) if links else None, story_id),
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


def get_manga_v2_page_layouts(conn: sqlite3.Connection, story_id: int) -> list[list[list[float]]]:
    row = conn.execute("SELECT manga_v2_page_layouts FROM stories WHERE id = ?", (story_id,)).fetchone()
    return json.loads(row["manga_v2_page_layouts"]) if row and row["manga_v2_page_layouts"] else []


def set_manga_v2_page_layouts(conn: sqlite3.Connection, story_id: int, layouts: list[list[list[float]]] | None) -> None:
    """None か空でテンプレートに戻す。"""
    conn.execute(
        "UPDATE stories SET manga_v2_page_layouts = ? WHERE id = ?",
        (json.dumps(layouts) if layouts else None, story_id),
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


CHARACTER_SHEET_FIELDS = (
    "appearance_tags", "body_tags", "trigger_word", "outfit_tags", "style_tags", "negative_tags", "seed", "is_adult",
    "is_minor", "growth_of", "growth_label", "growth_order", "age",
)


def update_character_sheet(conn: sqlite3.Connection, character_id: int, sheet: dict[str, Any]) -> dict[str, Any] | None:
    """キャラシートの項目をまとめて書き換える。渡されなかった項目はそのまま。"""
    fields = [f for f in CHARACTER_SHEET_FIELDS if f in sheet]
    if fields:
        assignments = ", ".join(f"{f} = ?" for f in fields)
        conn.execute(
            f"UPDATE characters SET {assignments} WHERE id = ?",
            (*(sheet[f] for f in fields), character_id),
        )
        conn.commit()
    return get_character(conn, character_id)


def get_character(conn: sqlite3.Connection, character_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM characters WHERE id = ?", (character_id,)).fetchone()
    return dict(row) if row else None


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
    # ギャラリーの別の画像から作ったとき、その画像のキー(拡大表示の範囲を描き直した画像など)
    "source_key": "TEXT",
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
    source_key: str | None = None,
) -> dict[str, Any]:
    """
    /select 経由の画像生成だけを対象にした履歴保存。image_paths / i2i_image_path /
    character_references[].image_path はリポジトリルートからの相対パス(outputs/history/...)で、
    画像データ自体はDBに入れずファイルのまま置く。

    based_on_id: この生成が別の履歴エントリ(インポート画像やメタデータ不足のエントリなど)を
    元に再生成された場合、その元エントリの id を入れておくことで rerun の系譜を遡れるようにする。
    metadata_incomplete: インポート時にメタデータの一部/全部が読み取れず、デフォルト値で
    補完した場合に立てる。ユーザーが再生成のベースにすべきかの目印になる。
    source_key: ギャラリーの別の画像から作った場合、その画像のキー(コマ・挿絵なども指せる)。
    """
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        """
        INSERT INTO generation_history
            (prompt, negative_prompt, model, size, steps, scale, seed, chunk_ids, image_paths,
             i2i_image_path, i2i_strength, i2i_noise, character_references, characters,
             based_on_id, metadata_incomplete, source_key, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            source_key,
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
            "dialogue": json.loads(row["dialogue"]) if row["dialogue"] is not None else None,
            "overlays": json.loads(row["overlays"]) if row["overlays"] else [],
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


def update_scene_content(
    conn: sqlite3.Connection,
    scene_id: int,
    *,
    draft_title: str | None = None,
    text: str | None = None,
    draft_prompt_tags: str | None = None,
) -> None:
    """シーンの手直し。None の項目は変えない。本文は draft_text と novelai_text の両方を差し替える。"""
    if draft_title is not None:
        conn.execute("UPDATE story_scenes SET draft_title = ? WHERE id = ?", (draft_title, scene_id))
    if text is not None:
        conn.execute("UPDATE story_scenes SET draft_text = ?, novelai_text = ? WHERE id = ?", (text, text, scene_id))
    if draft_prompt_tags is not None:
        conn.execute("UPDATE story_scenes SET draft_prompt_tags = ? WHERE id = ?", (draft_prompt_tags, scene_id))
    conn.commit()


def update_scene_negative(conn: sqlite3.Connection, scene_id: int, negative: str | None) -> None:
    """コマのネガティブ。空なら未設定(ページ・物語のネガティブを使う)に戻す。"""
    conn.execute("UPDATE story_scenes SET negative_prompt = ? WHERE id = ?", ((negative or "").strip() or None, scene_id))
    conn.commit()


def update_scene_absent(conn: sqlite3.Connection, scene_id: int, tags: str | None) -> None:
    """コマに出てはいけない物(英語タグ)。空なら無し。"""
    conn.execute("UPDATE story_scenes SET absent_tags = ? WHERE id = ?", ((tags or "").strip() or None, scene_id))
    conn.commit()


def get_manga_v2_negatives(conn: sqlite3.Connection, story_id: int) -> dict[str, Any]:
    """物語とページごとのネガティブ。{"story": 文 か None, "pages": {ページ番号(0始まり): 文}}。"""
    row = conn.execute(
        "SELECT manga_v2_negative, manga_v2_page_negatives FROM stories WHERE id = ?", (story_id,)
    ).fetchone()
    if row is None:
        return {"story": None, "pages": {}}
    pages = json.loads(row["manga_v2_page_negatives"]) if row["manga_v2_page_negatives"] else {}
    return {"story": row["manga_v2_negative"], "pages": {int(k): v for k, v in pages.items() if v}}


def set_manga_v2_negatives(conn: sqlite3.Connection, story_id: int, story: str | None, pages: dict[int, str]) -> None:
    cleaned = {str(k): v.strip() for k, v in pages.items() if v and v.strip()}
    conn.execute(
        "UPDATE stories SET manga_v2_negative = ?, manga_v2_page_negatives = ? WHERE id = ?",
        ((story or "").strip() or None, json.dumps(cleaned, ensure_ascii=False) if cleaned else None, story_id),
    )
    conn.commit()


def set_scene_overlays(conn: sqlite3.Connection, scene_id: int, overlays: list[dict[str, Any]]) -> None:
    conn.execute(
        "UPDATE story_scenes SET overlays = ? WHERE id = ?",
        (json.dumps(overlays, ensure_ascii=False) if overlays else None, scene_id),
    )
    conn.commit()


def get_story_growth(conn: sqlite3.Connection, story_id: int) -> dict[str, Any]:
    """この巻の成長段階の選び方。{"age": 歳 か None, "stages": {人物のID: 段階のキャラID}}。"""
    row = conn.execute("SELECT manga_v2_growth FROM stories WHERE id = ?", (story_id,)).fetchone()
    data = json.loads(row["manga_v2_growth"]) if row and row["manga_v2_growth"] else {}
    return {"age": data.get("age"), "stages": {int(k): int(v) for k, v in (data.get("stages") or {}).items()}}


def set_story_growth(conn: sqlite3.Connection, story_id: int, age: float | None, stages: dict[int, int]) -> None:
    value = {"age": age, "stages": {str(k): v for k, v in stages.items()}} if age is not None or stages else None
    conn.execute(
        "UPDATE stories SET manga_v2_growth = ? WHERE id = ?",
        (json.dumps(value, ensure_ascii=False) if value else None, story_id),
    )
    conn.commit()


def growth_choices(conn: sqlite3.Connection, story_id: int) -> dict[int, dict[str, Any]]:
    """
    人物のID → この巻で使う段階のキャラ(キャラシートの行)。段階を直接選んでいればそれ、無ければ年齢(age)以下で
    一番大きい段階(同じ年齢なら順番の大きい方)。どちらも無い人物は入れない。
    """
    growth = get_story_growth(conn, story_id)
    if growth["age"] is None and not growth["stages"]:
        return {}
    characters = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM characters")}
    stages_of: dict[int, list[dict[str, Any]]] = {}
    for character in characters.values():
        if character.get("growth_of") is not None:
            stages_of.setdefault(character["growth_of"], []).append(character)
    chosen: dict[int, dict[str, Any]] = {}
    for person_id, stages in stages_of.items():
        picked = growth["stages"].get(person_id)
        if picked is not None and picked in characters and characters[picked].get("growth_of") == person_id:
            chosen[person_id] = characters[picked]
            continue
        if growth["age"] is not None:
            fitting = [s for s in stages if s.get("age") is not None and s["age"] <= growth["age"]]
            if fitting:
                chosen[person_id] = max(fitting, key=lambda s: (s["age"], s.get("growth_order") or 0))
    return chosen


# 成長段階の見た目として段階のキャラシートから写す項目(名前・ID はシーンのキャラのまま。話し手の判定が変わらないように)
_GROWTH_LOOK_FIELDS = (
    "appearance_tags", "body_tags", "outfit_tags", "style_tags", "negative_tags", "seed", "reference_image_path",
    "is_adult", "is_minor", "age",
)


def get_scene(conn: sqlite3.Connection, scene_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM story_scenes WHERE id = ?", (scene_id,)).fetchone()
    return dict(row) if row else None


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
        RETURNING *
        """,
        (name, appearance_tags, notes, now),
    ).fetchone()
    conn.commit()
    return dict(row)


def character_works(conn: sqlite3.Connection) -> dict[int, list[dict[str, Any]]]:
    """キャラID → そのキャラが出ている物語(新しい順)。シーンの数と、シリーズの巻なら題と巻の番号も。"""
    rows = conn.execute(
        """
        SELECT sc.character_id, st.id AS story_id,
               COALESCE(NULLIF(st.title, ''), NULLIF(st.premise, ''), '(無題 #' || st.id || ')') AS title,
               COUNT(*) AS scenes, st.created_at, st.volume_no, se.id AS series_id, se.title AS series_title
        FROM scene_characters sc
        JOIN story_scenes s ON s.id = sc.scene_id
        JOIN stories st ON st.id = s.story_id
        LEFT JOIN series se ON se.id = st.series_id
        GROUP BY sc.character_id, st.id
        ORDER BY st.created_at DESC
        """
    ).fetchall()
    result: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        work = dict(row)
        result.setdefault(work.pop("character_id"), []).append(work)
    return result


def character_usage(conn: sqlite3.Connection, character_id: int) -> dict[str, Any]:
    """そのキャラを割り当てている物語とシーンの数(削除前の確認用)。"""
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT s.story_id) AS stories, COUNT(*) AS scenes
        FROM scene_characters sc
        JOIN story_scenes s ON s.id = sc.scene_id
        WHERE sc.character_id = ?
        """,
        (character_id,),
    ).fetchone()
    titles = [
        r["title"]
        for r in conn.execute(
            """
            SELECT DISTINCT COALESCE(st.title, '(無題 #' || st.id || ')') AS title FROM scene_characters sc
            JOIN story_scenes s ON s.id = sc.scene_id
            JOIN stories st ON st.id = s.story_id
            WHERE sc.character_id = ?
            ORDER BY st.title
            """,
            (character_id,),
        )
    ]
    return {"stories": row["stories"], "scenes": row["scenes"], "story_titles": titles}


def character_story_ids(conn: sqlite3.Connection, character_id: int) -> set[int]:
    """そのキャラをシーンに割り当てている物語のID。"""
    rows = conn.execute(
        """
        SELECT DISTINCT s.story_id FROM scene_characters sc
        JOIN story_scenes s ON s.id = sc.scene_id
        WHERE sc.character_id = ?
        """,
        (character_id,),
    )
    return {int(row[0]) for row in rows}


def related_story_ids(conn: sqlite3.Connection, story_id: int) -> set[int]:
    """その物語と、同じシリーズのほかの巻のID(シリーズでなければ、その物語だけ)。"""
    row = conn.execute("SELECT series_id FROM stories WHERE id = ?", (story_id,)).fetchone()
    if row is None or row["series_id"] is None:
        return {story_id}
    return {int(r[0]) for r in conn.execute("SELECT id FROM stories WHERE series_id = ?", (row["series_id"],))} | {story_id}


def story_characters(conn: sqlite3.Connection, story_id: int) -> list[dict[str, Any]]:
    """物語のシーンに割り当てられているキャラと、そのシーン数(付け替えの元を選ぶ用)。"""
    rows = conn.execute(
        """
        SELECT c.id, c.name, COUNT(*) AS scenes
        FROM scene_characters sc
        JOIN story_scenes s ON s.id = sc.scene_id
        JOIN characters c ON c.id = sc.character_id
        WHERE s.story_id = ?
        GROUP BY c.id
        ORDER BY c.name
        """,
        (story_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def replace_story_character(conn: sqlite3.Connection, story_id: int, from_id: int, to_id: int) -> int:
    """
    物語内で from_id を割り当てているシーンを、すべて to_id に付け替える。付け替えたシーン数を返す。
    to_id が既に同じシーンにいる場合は重複させず、from_id を外すだけにする。
    """
    scene_ids = [
        row["scene_id"]
        for row in conn.execute(
            """
            SELECT sc.scene_id FROM scene_characters sc
            JOIN story_scenes s ON s.id = sc.scene_id
            WHERE s.story_id = ? AND sc.character_id = ?
            """,
            (story_id, from_id),
        )
    ]
    for scene_id in scene_ids:
        conn.execute(
            "INSERT OR IGNORE INTO scene_characters (scene_id, character_id) VALUES (?, ?)", (scene_id, to_id)
        )
        conn.execute("DELETE FROM scene_characters WHERE scene_id = ? AND character_id = ?", (scene_id, from_id))
    conn.commit()
    return len(scene_ids)


def delete_character(conn: sqlite3.Connection, character_id: int) -> None:
    conn.execute("DELETE FROM character_outfits WHERE character_id = ?", (character_id,))
    conn.execute("DELETE FROM characters WHERE id = ?", (character_id,))
    conn.commit()


def set_scene_characters(
    conn: sqlite3.Connection,
    scene_id: int,
    character_ids: list[int],
    actions: dict[int, str] | None = None,
    outfits: dict[int, str] | None = None,
    states: dict[int, int | None] | None = None,
) -> None:
    """
    シーンに出るキャラを設定し直す。actions はキャラごとの表情・動作、outfits はその場面の服装(英語タグ)、
    states はその場面の状態(dataset_states の id。None で状態なし)。
    渡さなければ、残るキャラの動作・服装・状態はそのまま残す(登場人物の付け替えで、書いたものが消えないように)。
    """
    rows = conn.execute(
        "SELECT character_id, action_tags, outfit_tags, state_id FROM scene_characters WHERE scene_id = ?", (scene_id,)
    ).fetchall()
    kept_actions = {row["character_id"]: row["action_tags"] for row in rows}
    kept_outfits = {row["character_id"]: row["outfit_tags"] for row in rows}
    kept_states = {row["character_id"]: row["state_id"] for row in rows}
    if actions is not None:
        kept_actions = {character_id: (actions.get(character_id) or "").strip() or None for character_id in character_ids}
    if outfits is not None:
        kept_outfits = {character_id: (outfits.get(character_id) or "").strip() or None for character_id in character_ids}
    if states is not None:
        kept_states = {character_id: states.get(character_id) for character_id in character_ids}
    conn.execute("DELETE FROM scene_characters WHERE scene_id = ?", (scene_id,))
    conn.executemany(
        "INSERT OR IGNORE INTO scene_characters (scene_id, character_id, action_tags, outfit_tags, state_id)"
        " VALUES (?, ?, ?, ?, ?)",
        [
            (scene_id, character_id, kept_actions.get(character_id), kept_outfits.get(character_id), kept_states.get(character_id))
            for character_id in character_ids
        ],
    )
    conn.commit()


def characters_by_scene(conn: sqlite3.Connection, story_id: int) -> dict[int, list[dict[str, Any]]]:
    """物語内の scene_id → 登場キャラの一覧。"""
    rows = conn.execute(
        """
        SELECT sc.scene_id, sc.action_tags AS scene_action_tags, sc.outfit_tags AS scene_outfit_tags,
               sc.state_id AS scene_state_id, c.*
        FROM scene_characters sc
        JOIN characters c ON c.id = sc.character_id
        JOIN story_scenes s ON s.id = sc.scene_id
        WHERE s.story_id = ?
        ORDER BY c.name
        """,
        (story_id,),
    ).fetchall()

    from .character_sheet import look_tags

    states = {state["id"]: state for state in list_dataset_states(conn)}
    # この巻で使う成長段階(人物のID → 段階のキャラ)
    growth = growth_choices(conn, story_id)
    result: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        character = dict(row)
        del character["scene_id"]
        # 成長段階: その人物(元のキャラかどの段階)なら、この巻で選んだ段階の見た目にする(その場面の服装・状態は後で当てる)
        stage = growth.get(character.get("growth_of") or character["id"])
        if stage is not None and stage["id"] != character["id"]:
            for key in _GROWTH_LOOK_FIELDS:
                character[key] = stage.get(key)
            character["growth_stage_id"] = stage["id"]
            character["growth_stage_label"] = stage.get("growth_label")
        # そのシーンでのこのキャラの表情・動作(キャラシートの列と名前がぶつからないよう別名で取り出す)
        character["action_tags"] = character.pop("scene_action_tags")
        # その場面の服装(水着・浴衣など)があれば、ふだんの服装の代わりに使う(コマの生成・確かめ・描き直しのどれにも効く)
        outfit = character.pop("scene_outfit_tags")
        character["scene_outfit_tags"] = outfit
        if outfit:
            character["outfit_tags"] = outfit
        # その場面の状態(食べ過ぎでお腹が出る など)があれば、キャラ別データセットと同じく容姿+体型を状態の見た目にする。
        # 消された状態は無視する
        state = states.get(character.pop("scene_state_id"))
        character["state_id"] = state["id"] if state else None
        character["state_name"] = state["name"] if state else None
        # 状態で外したタグ(髪を下ろす → シュシュ など)。コマの確かめで、描かれていないかを見る
        character["state_remove_tags"] = state["remove_tags"] if state else ""
        if state:
            character["appearance_tags"] = look_tags(character, state)
            character["body_tags"] = ""
        result.setdefault(row["scene_id"], []).append(character)
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


def _guard_row(row: sqlite3.Row) -> dict[str, Any]:
    return {**dict(row), "blocked_tags": json.loads(row["blocked_tags"])}


def list_guard_profiles(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM guard_profiles ORDER BY name").fetchall()
    return [_guard_row(row) for row in rows]


def get_guard_profile(conn: sqlite3.Connection, profile_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM guard_profiles WHERE id = ?", (profile_id,)).fetchone()
    return _guard_row(row) if row else None


def save_guard_profile(
    conn: sqlite3.Connection, name: str, blocked_tags: list[str], negative_tags: str
) -> dict[str, Any]:
    """同じ名前があれば上書きする(画像プリセットと同じ扱い)。"""
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        """
        INSERT INTO guard_profiles (name, blocked_tags, negative_tags, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            blocked_tags = excluded.blocked_tags, negative_tags = excluded.negative_tags
        RETURNING *
        """,
        (name, json.dumps(blocked_tags, ensure_ascii=False), negative_tags, now),
    ).fetchone()
    conn.commit()
    return _guard_row(row)


def delete_guard_profile(conn: sqlite3.Connection, profile_id: int) -> None:
    conn.execute("DELETE FROM guard_profiles WHERE id = ?", (profile_id,))
    conn.commit()


def update_story_layout(conn: sqlite3.Connection, story_id: int, panels_per_page: int) -> None:
    """
    1ページのコマ数を変え、既存シーンのページ割り当てを振り直す。

    実機検証: 1216x1728 のページにV5は8コマ以上を描く。4シーンしか渡さないと
    残りのコマは内容なしで埋められ、同じ構図の反復になった。8シーン渡すと
    大小のコマが混ざった漫画らしいレイアウトになったため、後から調整できるようにする。
    """
    # コマ数で割り付け直すので、個別に決めたページの区切りはやめる
    conn.execute("UPDATE stories SET panels_per_page = ?, custom_pages = 0 WHERE id = ?", (panels_per_page, story_id))
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
            width = excluded.width, height = excluded.height, created_at = excluded.created_at, stale = 0
        """,
        (story_id, scene_id, image_path, seed, width, height, now),
    )
    conn.commit()


def set_manga_panel_image(conn: sqlite3.Connection, scene_id: int, image_path: str) -> None:
    """コマの絵のファイルだけを差し替える(左右反転など。シード・大きさ・描き直し待ちの印はそのまま)。"""
    conn.execute("UPDATE manga_panels SET image_path = ? WHERE scene_id = ?", (image_path, scene_id))
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


def list_story_characters(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """似ている作品の計算用: 物語ごとの登場人物(シーンに割り当てたもの)。"""
    rows = conn.execute(
        """
        SELECT DISTINCT s.story_id, c.id AS character_id, c.name
        FROM scene_characters sc
        JOIN story_scenes s ON s.id = sc.scene_id
        JOIN characters c ON c.id = sc.character_id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def list_panel_characters(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """似ている画像の計算用: 漫画のコマごとの登場人物。"""
    rows = conn.execute(
        """
        SELECT p.id AS panel_id, c.id AS character_id, c.name
        FROM manga_panels p
        JOIN scene_characters sc ON sc.scene_id = p.scene_id
        JOIN characters c ON c.id = sc.character_id
        """
    ).fetchall()
    return [dict(row) for row in rows]


# ---- 漫画の取り込み(構成の参考) ----


# 取り込みの使い方。structure: 構成だけ(この列ができる前の取り込み)、similar: 似た漫画を作る、
# rebuild: 自分の作品を作り直す(セリフの文面も持つ)
# genre: 漫画ドラフトのジャンル(日常コメディ など)への割り当て。空は割り当てなし
# adult: R18(成人誌の漫画)の取り込み。似た漫画を作るとき、性的なタグ・体つきのタグも使う(manga_similar.py)
_MANGA_IMPORTS_EXTRA_COLUMNS = {
    "purpose": "TEXT NOT NULL DEFAULT 'structure'",
    "genre": "TEXT NOT NULL DEFAULT ''",
    "adult": "INTEGER NOT NULL DEFAULT 0",
}


def _migrate_manga_imports(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(manga_imports)")}
    for column, column_type in _MANGA_IMPORTS_EXTRA_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE manga_imports ADD COLUMN {column} {column_type}")
    conn.commit()


def create_manga_import(
    conn: sqlite3.Connection, title: str, page_count: int, purpose: str = "structure", genre: str = "", adult: bool = False
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO manga_imports (title, page_count, purpose, genre, adult, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (title, page_count, purpose, genre, int(adult), now),
    )
    conn.commit()
    return get_manga_import(conn, int(cur.lastrowid or 0)) or {}


def get_manga_import(conn: sqlite3.Connection, import_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM manga_imports WHERE id = ?", (import_id,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["analysis"] = json.loads(item["analysis"]) if item["analysis"] else None
    return item


def list_manga_imports(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id FROM manga_imports ORDER BY id DESC").fetchall()
    return [item for row in rows if (item := get_manga_import(conn, row["id"])) is not None]


def update_manga_import(
    conn: sqlite3.Connection,
    import_id: int,
    *,
    status: str | None = None,
    analysis: Any = None,
    purpose: str | None = None,
    genre: str | None = None,
    adult: bool | None = None,
) -> None:
    if adult is not None:
        conn.execute("UPDATE manga_imports SET adult = ? WHERE id = ?", (int(adult), import_id))
    if genre is not None:
        conn.execute("UPDATE manga_imports SET genre = ? WHERE id = ?", (genre, import_id))
    if purpose is not None:
        conn.execute("UPDATE manga_imports SET purpose = ? WHERE id = ?", (purpose, import_id))
    if status is not None:
        conn.execute("UPDATE manga_imports SET status = ? WHERE id = ?", (status, import_id))
    if analysis is not None:
        conn.execute(
            "UPDATE manga_imports SET analysis = ? WHERE id = ?", (json.dumps(analysis, ensure_ascii=False), import_id)
        )
    conn.commit()


def delete_manga_import(conn: sqlite3.Connection, import_id: int) -> None:
    conn.execute("DELETE FROM manga_imports WHERE id = ?", (import_id,))
    conn.commit()


# ---- キャラ別データセットのバリエーション ----


def list_dataset_variations(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """軸(framings / poses / outfits / expressions / locations)ごとの候補(足した順)。"""
    result: dict[str, list[str]] = {axis: [] for axis in ("framings", "poses", "outfits", "expressions", "locations")}
    for row in conn.execute("SELECT axis, value FROM dataset_variations ORDER BY id"):
        result.setdefault(row["axis"], []).append(row["value"])
    return result


def add_dataset_variations(conn: sqlite3.Connection, axis: str, values: list[str]) -> int:
    """候補を足す(すでにあるものは飛ばす)。足した数を返す。"""
    now = datetime.now(timezone.utc).isoformat()
    added = 0
    for value in values:
        cur = conn.execute(
            "INSERT OR IGNORE INTO dataset_variations (axis, value, created_at) VALUES (?, ?, ?)", (axis, value, now)
        )
        added += cur.rowcount
    conn.commit()
    return added


def delete_dataset_variation(conn: sqlite3.Connection, axis: str, value: str) -> bool:
    cur = conn.execute("DELETE FROM dataset_variations WHERE axis = ? AND value = ?", (axis, value))
    conn.commit()
    return cur.rowcount > 0


# ---- キャラ別データセットのシチュエーション・状態 ----

# 初めて表を作るときに入れる例(そのあとは、画面や API で足したり直したり消したりする)
DEFAULT_DATASET_SITUATIONS: list[dict[str, Any]] = [
    {
        "name": "ジムで運動",
        "axes": {
            "locations": ["gym, indoors"],
            # 全年齢のガード(dataset.blocked_tags)に当たるタグ(sports bra など)は入れない
            "outfits": ["sportswear, track jacket, track pants", "tank top, shorts, sneakers"],
            "poses": ["running on treadmill", "lifting dumbbell", "stretching", "jumping rope"],
            "expressions": ["sweat, determined", "sweat, smile"],
        },
    },
    {
        "name": "カフェで休憩",
        "axes": {
            "locations": ["cafe, indoors, table"],
            "poses": ["sitting, holding cup", "sitting, drinking", "sitting, hand on own cheek"],
            "expressions": ["smile", "light smile, closed eyes", "relaxed"],
        },
    },
    {
        "name": "体育の授業で立位体前屈",
        "axes": {
            "locations": ["gymnasium, indoors"],
            "outfits": ["gym uniform, gym shirt, shorts"],
            "poses": ["standing forward bend, bent over, touching toes, legs straight"],
            "framings": ["full body, from side", "full body"],
            "expressions": ["closed mouth, strained", "smile"],
        },
    },
    {
        "name": "昼寝",
        "axes": {
            "locations": ["bedroom, on bed", "living room, on couch"],
            "poses": ["sleeping, lying on back", "sleeping, lying on side"],
            "expressions": ["closed eyes, open mouth", "closed eyes, peaceful"],
            "framings": ["from above", "full body", "upper body"],
        },
        "extra_tags": "daytime, sunlight",
    },
]
DEFAULT_DATASET_STATES: list[dict[str, Any]] = [
    {
        "name": "食べ過ぎでお腹が出る",
        "add_tags": "plump, round belly, belly, stomach bulge",
        "remove_tags": "slim, slender, thin, skinny, flat stomach, narrow waist, toned, abs",
        "part": "belly",
    },
    {
        "name": "妊娠してお腹が大きい",
        "add_tags": "pregnant, big belly, hand on own stomach",
        "remove_tags": "slim, slender, thin, skinny, flat stomach, narrow waist, toned, abs",
        "part": "belly",
    },
]

_SITUATION_AXES = ("framings", "poses", "outfits", "expressions", "locations")


def _situation(row: sqlite3.Row) -> dict[str, Any]:
    axes = json.loads(row["axes"] or "{}")
    return {
        "id": row["id"],
        "name": row["name"],
        **{axis: [str(v) for v in axes.get(axis) or []] for axis in _SITUATION_AXES},
        "extra_tags": row["extra_tags"] or "",
    }


def list_dataset_situations(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [_situation(row) for row in conn.execute("SELECT * FROM dataset_situations ORDER BY id")]


def get_dataset_situation(conn: sqlite3.Connection, situation_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM dataset_situations WHERE id = ?", (situation_id,)).fetchone()
    return _situation(row) if row else None


def save_dataset_situation(
    conn: sqlite3.Connection, situation_id: int | None, name: str, axes: dict[str, list[str]], extra_tags: str
) -> dict[str, Any]:
    """シチュエーションを作る(situation_id が None)か、書き換える。同じ名前がほかにあれば sqlite3.IntegrityError。"""
    data = json.dumps({axis: list(axes.get(axis) or []) for axis in _SITUATION_AXES}, ensure_ascii=False)
    if situation_id is None:
        cur = conn.execute(
            "INSERT INTO dataset_situations (name, axes, extra_tags, created_at) VALUES (?, ?, ?, ?)",
            (name, data, extra_tags, datetime.now(timezone.utc).isoformat()),
        )
        situation_id = int(cur.lastrowid or 0)
    else:
        conn.execute(
            "UPDATE dataset_situations SET name = ?, axes = ?, extra_tags = ? WHERE id = ?",
            (name, data, extra_tags, situation_id),
        )
    conn.commit()
    return get_dataset_situation(conn, situation_id) or {}


def delete_dataset_situation(conn: sqlite3.Connection, situation_id: int) -> bool:
    cur = conn.execute("DELETE FROM dataset_situations WHERE id = ?", (situation_id,))
    conn.commit()
    return cur.rowcount > 0


def _state(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "add_tags": row["add_tags"] or "",
        "remove_tags": row["remove_tags"] or "",
        "part": row["part"] if "part" in row.keys() else "none",
    }


def list_dataset_states(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [_state(row) for row in conn.execute("SELECT * FROM dataset_states ORDER BY id")]


def get_dataset_state(conn: sqlite3.Connection, state_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM dataset_states WHERE id = ?", (state_id,)).fetchone()
    return _state(row) if row else None


def save_dataset_state(
    conn: sqlite3.Connection, state_id: int | None, name: str, add_tags: str, remove_tags: str, part: str = "none"
) -> dict[str, Any]:
    """
    状態を作る(state_id が None)か、書き換える。part は、ふだんの絵から描き直す部位(none ならプロンプトだけで作る)。
    同じ名前がほかにあれば sqlite3.IntegrityError。
    """
    if state_id is None:
        cur = conn.execute(
            "INSERT INTO dataset_states (name, add_tags, remove_tags, part, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, add_tags, remove_tags, part, datetime.now(timezone.utc).isoformat()),
        )
        state_id = int(cur.lastrowid or 0)
    else:
        conn.execute(
            "UPDATE dataset_states SET name = ?, add_tags = ?, remove_tags = ?, part = ? WHERE id = ?",
            (name, add_tags, remove_tags, part, state_id),
        )
    conn.commit()
    return get_dataset_state(conn, state_id) or {}


def delete_dataset_state(conn: sqlite3.Connection, state_id: int) -> bool:
    cur = conn.execute("DELETE FROM dataset_states WHERE id = ?", (state_id,))
    conn.commit()
    return cur.rowcount > 0


# ---- キャラシートの素体(body_base.py) ----

BODY_BASE_FIELDS = (
    "name", "kind", "sex", "skin_tone", "build", "height", "proportion", "chest", "muscle", "extra_tags", "negative_tags",
    "notes",
)

# 素体の設定の、はじめの選択肢: 項目 → [(値, 画面の名前, タグ, 真後ろの部位のタグ(None なら同じ))]。値の空は「指定しない」。
# 胸は、正面の胸のタグ(large breasts など)が背面の絵を正面・振り返りに引き戻すので(2026-10 実機: 栄子の背面)、
# 真後ろでは後ろから脇に見える形で書く(小さめ・胸筋は後ろからは見えないので入れない)
DEFAULT_BODY_BASE_OPTIONS: dict[str, list[tuple[str, str, str, str | None]]] = {
    "sex": [("female", "女性", "1girl, female", None), ("male", "男性", "1boy, male", None)],
    "skin_tone": [
        ("", "キャラシートのまま", "", None),
        ("pale", "色白", "pale skin", None),
        ("fair", "明るい肌", "fair skin", None),
        ("tan", "小麦色", "tan, tanned skin", None),
        ("olive", "オリーブ", "olive skin", None),
        ("brown", "褐色", "brown skin, dark-skinned", None),
        ("dark", "濃い褐色", "dark skin, dark-skinned", None),
    ],
    "build": [
        ("", "ふつう", "", None),
        ("slender", "細身", "slender, slim", None),
        ("athletic", "引き締まった", "athletic, toned", None),
        ("muscular", "筋肉質", "muscular, muscular build", None),
        ("curvy", "曲線的(メリハリ)", "curvy", None),
        ("plump", "ふくよか", "plump, soft body", None),
        ("stocky", "がっしり", "stocky, broad shoulders", None),
    ],
    "height": [("", "ふつう", "", None), ("short", "低め", "short stature", None), ("tall", "高め", "tall, long legs", None)],
    "proportion": [
        ("", "絵柄のまま", "", None),
        ("realistic", "写実(7.5頭身)", "realistic proportions", None),
        ("model", "モデル体型(8頭身)", "model proportions, long legs", None),
    ],
    "chest": [
        ("", "指定しない", "", None),
        ("small", "小さめ", "small breasts", ""),
        ("medium", "ふつう", "medium breasts", "breasts peek"),
        ("large", "大きめ", "large breasts", "sideboob, breasts peek"),
        ("pectorals", "胸筋", "pectorals", ""),
    ],
    "muscle": [
        ("", "指定しない", "", None),
        ("soft", "柔らかい", "soft body", None),
        ("toned", "適度", "toned, slight muscle definition", None),
        ("defined", "はっきり", "defined muscles, abs", None),
    ],
}


def list_wardrobe_parts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """衣装の部品(部位ごと、並べる順)。"""
    return [dict(row) for row in conn.execute("SELECT * FROM wardrobe_parts ORDER BY slot, sort_order, id")]


def get_wardrobe_part(conn: sqlite3.Connection, part_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM wardrobe_parts WHERE id = ?", (part_id,)).fetchone()
    return dict(row) if row else None


def add_wardrobe_part(conn: sqlite3.Connection, slot: str, value: str, label: str, tags: str, colorable: bool) -> dict[str, Any]:
    """部品を足す(その部位の最後に並べる)。同じ部位に同じ値があれば sqlite3.IntegrityError。"""
    last = conn.execute("SELECT MAX(sort_order) FROM wardrobe_parts WHERE slot = ?", (slot,)).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO wardrobe_parts (slot, value, label, tags, colorable, sort_order, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (slot, value, label, tags, int(colorable), (last or 0) + 1, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return get_wardrobe_part(conn, int(cur.lastrowid or 0)) or {}


def update_wardrobe_part(conn: sqlite3.Connection, part_id: int, label: str, tags: str, colorable: bool) -> dict[str, Any] | None:
    """部品の名前・タグを書き換える(値は衣装が使っているので変えない)。"""
    conn.execute(
        "UPDATE wardrobe_parts SET label = ?, tags = ?, colorable = ? WHERE id = ?", (label, tags, int(colorable), part_id)
    )
    conn.commit()
    return get_wardrobe_part(conn, part_id)


def delete_wardrobe_part(conn: sqlite3.Connection, part_id: int) -> bool:
    cur = conn.execute("DELETE FROM wardrobe_parts WHERE id = ?", (part_id,))
    conn.commit()
    return cur.rowcount > 0


def reorder_wardrobe_parts(conn: sqlite3.Connection, slot: str, ids: list[int]) -> None:
    """部位の部品を ids の順に並べる(ほかの部位の id は無視する)。"""
    for order, part_id in enumerate(ids):
        conn.execute("UPDATE wardrobe_parts SET sort_order = ? WHERE id = ? AND slot = ?", (order, part_id, slot))
    conn.commit()


def outfits_using_part(conn: sqlite3.Connection, slot: str, value: str) -> list[str]:
    """その部品を使っている衣装(「キャラ名: 衣装名」)。色・柄は、どの部位に付けていても使っているとみなす。"""
    rows = conn.execute(
        "SELECT o.name, o.parts, c.name AS character_name FROM character_outfits o"
        " JOIN characters c ON c.id = o.character_id ORDER BY o.id"
    )
    used = []
    for row in rows:
        for part in json.loads(row["parts"] or "[]"):
            hit = part.get(slot) == value if slot in ("color", "pattern") else (part.get("slot"), part.get("value")) == (slot, value)
            if hit:
                used.append(f"{row['character_name']}: {row['name']}")
                break
    return used


def _outfit_row(row: sqlite3.Row) -> dict[str, Any]:
    outfit = dict(row)
    outfit["parts"] = json.loads(outfit["parts"] or "[]")
    return outfit


def list_character_outfits(conn: sqlite3.Connection, character_id: int) -> list[dict[str, Any]]:
    """キャラの衣装(並べる順)。"""
    rows = conn.execute(
        "SELECT * FROM character_outfits WHERE character_id = ? ORDER BY sort_order, id", (character_id,)
    )
    return [_outfit_row(row) for row in rows]


def list_all_outfits(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """全員の衣装(キャラ・並べる順)。"""
    rows = conn.execute("SELECT * FROM character_outfits ORDER BY character_id, sort_order, id")
    return [_outfit_row(row) for row in rows]


def get_character_outfit(conn: sqlite3.Connection, outfit_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM character_outfits WHERE id = ?", (outfit_id,)).fetchone()
    return _outfit_row(row) if row else None


def save_character_outfit(
    conn: sqlite3.Connection, outfit_id: int | None, character_id: int, name: str, parts: list[dict[str, Any]], extra_tags: str,
) -> dict[str, Any]:
    """衣装を作る(outfit_id が None。そのキャラの最後に並べる)か、書き換える。"""
    now = datetime.now(timezone.utc).isoformat()
    body = json.dumps(parts, ensure_ascii=False)
    if outfit_id is None:
        last = conn.execute(
            "SELECT MAX(sort_order) FROM character_outfits WHERE character_id = ?", (character_id,)
        ).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO character_outfits (character_id, name, parts, extra_tags, sort_order, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (character_id, name, body, extra_tags, (last or 0) + 1, now, now),
        )
        outfit_id = int(cur.lastrowid or 0)
    else:
        conn.execute(
            "UPDATE character_outfits SET name = ?, parts = ?, extra_tags = ?, updated_at = ? WHERE id = ?",
            (name, body, extra_tags, now, outfit_id),
        )
    conn.commit()
    return get_character_outfit(conn, outfit_id) or {}


def delete_character_outfit(conn: sqlite3.Connection, outfit_id: int) -> None:
    """衣装を消す。普段の衣装だったキャラは、普段の衣装なし(服装のタグはそのまま残す)にする。"""
    conn.execute("UPDATE characters SET outfit_id = NULL WHERE outfit_id = ?", (outfit_id,))
    conn.execute("DELETE FROM character_outfits WHERE id = ?", (outfit_id,))
    conn.commit()


def set_character_outfit(conn: sqlite3.Connection, character_id: int, outfit_id: int | None, outfit_tags: str | None) -> None:
    """普段の衣装を選ぶ(outfit_tags はその衣装のタグ。None なら普段の服装のタグは変えない)。"""
    if outfit_tags is None:
        conn.execute("UPDATE characters SET outfit_id = ? WHERE id = ?", (outfit_id, character_id))
    else:
        conn.execute("UPDATE characters SET outfit_id = ?, outfit_tags = ? WHERE id = ?", (outfit_id, outfit_tags, character_id))
    conn.commit()


def list_body_base_options(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """素体の設定の選択肢(項目ごと、並べる順)。"""
    return [dict(row) for row in conn.execute("SELECT * FROM body_base_options ORDER BY field, sort_order, id")]


def get_body_base_option(conn: sqlite3.Connection, option_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM body_base_options WHERE id = ?", (option_id,)).fetchone()
    return dict(row) if row else None


def add_body_base_option(conn: sqlite3.Connection, field: str, value: str, label: str, tags: str, rear_tags: str | None) -> dict[str, Any]:
    """選択肢を足す(その項目の最後に並べる)。同じ項目に同じ値があれば sqlite3.IntegrityError。"""
    last = conn.execute("SELECT MAX(sort_order) FROM body_base_options WHERE field = ?", (field,)).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO body_base_options (field, value, label, tags, rear_tags, sort_order, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (field, value, label, tags, rear_tags, (last or 0) + 1, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return get_body_base_option(conn, int(cur.lastrowid or 0)) or {}


def update_body_base_option(conn: sqlite3.Connection, option_id: int, label: str, tags: str, rear_tags: str | None) -> dict[str, Any] | None:
    """選択肢の名前・タグを書き換える(値は、素体が使っているので変えない)。"""
    conn.execute(
        "UPDATE body_base_options SET label = ?, tags = ?, rear_tags = ? WHERE id = ?", (label, tags, rear_tags, option_id)
    )
    conn.commit()
    return get_body_base_option(conn, option_id)


def delete_body_base_option(conn: sqlite3.Connection, option_id: int) -> bool:
    cur = conn.execute("DELETE FROM body_base_options WHERE id = ?", (option_id,))
    conn.commit()
    return cur.rowcount > 0


def reorder_body_base_options(conn: sqlite3.Connection, field: str, ids: list[int]) -> None:
    """項目の選択肢を ids の順に並べる(ほかの項目の id は無視する)。"""
    for order, option_id in enumerate(ids):
        conn.execute("UPDATE body_base_options SET sort_order = ? WHERE id = ? AND field = ?", (order, option_id, field))
    conn.commit()


def body_bases_using(conn: sqlite3.Connection, field: str, value: str) -> list[str]:
    """その選択肢を使っている素体の名前。"""
    if field not in BODY_BASE_FIELDS:
        return []
    return [row["name"] for row in conn.execute(f"SELECT name FROM body_bases WHERE {field} = ? ORDER BY id", (value,))]


def list_body_bases(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """素体の一覧と、それぞれを当てているキャラの数。"""
    rows = conn.execute(
        "SELECT b.*, (SELECT COUNT(*) FROM characters c WHERE c.body_base_id = b.id) AS used_by"
        " FROM body_bases b ORDER BY b.id"
    )
    return [dict(row) for row in rows]


def get_body_base(conn: sqlite3.Connection, base_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM body_bases WHERE id = ?", (base_id,)).fetchone()
    return dict(row) if row else None


def save_body_base(conn: sqlite3.Connection, base_id: int | None, base: dict[str, Any]) -> dict[str, Any]:
    """素体を作る(base_id が None)か、書き換える。同じ名前がほかにあれば sqlite3.IntegrityError。"""
    values = [base.get(f) or "" for f in BODY_BASE_FIELDS]
    if base_id is None:
        cur = conn.execute(
            f"INSERT INTO body_bases ({', '.join(BODY_BASE_FIELDS)}, created_at)"
            f" VALUES ({', '.join('?' for _ in BODY_BASE_FIELDS)}, ?)",
            (*values, datetime.now(timezone.utc).isoformat()),
        )
        base_id = int(cur.lastrowid or 0)
    else:
        conn.execute(
            f"UPDATE body_bases SET {', '.join(f'{f} = ?' for f in BODY_BASE_FIELDS)} WHERE id = ?",
            (*values, base_id),
        )
    conn.commit()
    return get_body_base(conn, base_id) or {}


def delete_body_base(conn: sqlite3.Connection, base_id: int) -> bool:
    """素体を消す。当てていたキャラは、素体なしに戻す。"""
    conn.execute("UPDATE characters SET body_base_id = NULL WHERE body_base_id = ?", (base_id,))
    cur = conn.execute("DELETE FROM body_bases WHERE id = ?", (base_id,))
    conn.commit()
    return cur.rowcount > 0


def set_character_body_base(conn: sqlite3.Connection, character_id: int, base_id: int | None) -> None:
    conn.execute("UPDATE characters SET body_base_id = ? WHERE id = ?", (base_id, character_id))
    conn.commit()


def character_family(conn: sqlite3.Connection, character: dict[str, Any]) -> list[dict[str, Any]]:
    """同じ人物の成長段階(元のキャラと、その段階のキャラ全部。成長段階が無ければそのキャラだけ)。"""
    root = character.get("growth_of") or character["id"]
    rows = conn.execute("SELECT * FROM characters WHERE id = ? OR growth_of = ?", (root, root))
    return [dict(row) for row in rows]


# ---- 描き直しで作る人物の状態 ----

# 初めて表を作るときに入れる例。look は、描き直しに入れるその人物の見た目(none / outfit: 服だけ / full: 容姿・体型・服装)
DEFAULT_REDRAW_STATES: list[dict[str, str]] = [
    {
        "name": "透明",
        "match_tags": "invisible, invisibility, invisible person",
        "redraw_tags": "empty clothes, floating clothes, invisible, no head, no hands",
        "look": "outfit",
    },
    {
        "name": "幽霊",
        "match_tags": "ghost, spirit",
        "redraw_tags": "ghost, translucent body, floating, faded, glowing outline",
        "look": "full",
    },
    {
        "name": "石化",
        "match_tags": "petrification, petrified, stone statue",
        "redraw_tags": "petrification, stone statue, grey stone body, cracked",
        "look": "full",
    },
]


def _redraw_state(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in ("id", "name", "match_tags", "redraw_tags", "look")}


def list_redraw_states(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [_redraw_state(row) for row in conn.execute("SELECT * FROM panel_redraw_states ORDER BY id")]


def get_redraw_state(conn: sqlite3.Connection, state_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM panel_redraw_states WHERE id = ?", (state_id,)).fetchone()
    return _redraw_state(row) if row else None


def save_redraw_state(
    conn: sqlite3.Connection, state_id: int | None, name: str, match_tags: str, redraw_tags: str, look: str
) -> dict[str, Any]:
    """状態を作る(state_id が None)か、書き換える。同じ名前がほかにあれば sqlite3.IntegrityError。"""
    if state_id is None:
        cur = conn.execute(
            "INSERT INTO panel_redraw_states (name, match_tags, redraw_tags, look, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, match_tags, redraw_tags, look, datetime.now(timezone.utc).isoformat()),
        )
        state_id = int(cur.lastrowid or 0)
    else:
        conn.execute(
            "UPDATE panel_redraw_states SET name = ?, match_tags = ?, redraw_tags = ?, look = ? WHERE id = ?",
            (name, match_tags, redraw_tags, look, state_id),
        )
    conn.commit()
    return get_redraw_state(conn, state_id) or {}


def delete_redraw_state(conn: sqlite3.Connection, state_id: int) -> bool:
    cur = conn.execute("DELETE FROM panel_redraw_states WHERE id = ?", (state_id,))
    conn.commit()
    return cur.rowcount > 0


# ---- キャストのセット ----


def _cast_preset(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "character_ids": [int(i) for i in json.loads(row["character_ids"] or "[]")]}


def list_cast_presets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [_cast_preset(row) for row in conn.execute("SELECT * FROM cast_presets ORDER BY name")]


def save_cast_preset(conn: sqlite3.Connection, name: str, character_ids: list[int]) -> dict[str, Any]:
    """キャストのセットを保存する(同じ名前なら上書き)。"""
    conn.execute(
        """
        INSERT INTO cast_presets (name, character_ids, created_at) VALUES (?, ?, ?)
        ON CONFLICT (name) DO UPDATE SET character_ids = excluded.character_ids
        """,
        (name, json.dumps(character_ids), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return _cast_preset(conn.execute("SELECT * FROM cast_presets WHERE name = ?", (name,)).fetchone())


def delete_cast_preset(conn: sqlite3.Connection, preset_id: int) -> bool:
    cur = conn.execute("DELETE FROM cast_presets WHERE id = ?", (preset_id,))
    conn.commit()
    return cur.rowcount > 0


# ---- 漫画のジャンル ----

# 初めて表を作るときに入れるジャンル(そのあとは、画面や API で足したり消したりする)
DEFAULT_MANGA_GENRES = ("日常コメディ", "夫婦の日常コメディ", "学園コメディ", "ラブコメ", "ほのぼの", "ちょっといい話", "ドタバタギャグ")


def list_manga_genres(conn: sqlite3.Connection) -> list[str]:
    """ジャンルの名前(足した順)。"""
    return [row["name"] for row in conn.execute("SELECT name FROM manga_genres ORDER BY id")]


def add_manga_genre(conn: sqlite3.Connection, name: str) -> bool:
    """ジャンルを足す。すでにあれば何もしない。足したら True。"""
    cur = conn.execute(
        "INSERT OR IGNORE INTO manga_genres (name, created_at) VALUES (?, ?)",
        (name, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return cur.rowcount > 0


def delete_manga_genre(conn: sqlite3.Connection, name: str) -> bool:
    """ジャンルを候補から消す(取り込みへの割り当てはそのまま残る)。消したら True。"""
    cur = conn.execute("DELETE FROM manga_genres WHERE name = ?", (name,))
    conn.commit()
    return cur.rowcount > 0


# ---- 話の流れのセット ----

# 既定のセット。beats は巻ごとの流れ(1つが1巻)。description には、セットの後に続く流れなども書く
DEFAULT_STORY_ARCS: tuple[dict[str, Any], ...] = (
    {
        "name": "夏休み(日本アニメの定番)",
        "description": "仲間と過ごす夏休みの定番の流れ。少しずつ仲良くなり、最後は夏の終わりを惜しむ。この後は受験シーズンに入る。",
        "beats": [
            "星座観測。夜空を見ながら語り合い、仲良くなる。最後に、次はプールに行く計画を立てる",
            "プールに着ていく水着が要るので、メンバーでショッピングに行く",
            "プール当日。みんなでワイワイ楽しむ。すっかり仲良くなっている。最後に、次は花火大会の計画を立てる",
            "花火大会当日。屋台を回り、花火を見る。「夏ももう終わりか」と感傷に浸り、来年も来ようと話す",
        ],
    },
)


def _arc_row(row: Any) -> dict[str, Any]:
    arc = dict(row)
    arc["beats"] = json.loads(arc["beats"] or "[]")
    return arc


def list_story_arcs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """話の流れのセット(足した順)。"""
    return [_arc_row(row) for row in conn.execute("SELECT * FROM story_arcs ORDER BY id")]


def get_story_arc(conn: sqlite3.Connection, arc_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM story_arcs WHERE id = ?", (arc_id,)).fetchone()
    return _arc_row(row) if row else None


def save_story_arc(
    conn: sqlite3.Connection, arc_id: int | None, name: str, description: str, beats: list[str]
) -> dict[str, Any]:
    """セットを足す(arc_id が None)か書き換える。名前が重なれば sqlite3.IntegrityError。"""
    beats_json = json.dumps([b.strip() for b in beats if b.strip()], ensure_ascii=False)
    if arc_id is None:
        row = conn.execute(
            "INSERT INTO story_arcs (name, description, beats, created_at) VALUES (?, ?, ?, ?) RETURNING *",
            (name, description, beats_json, datetime.now(timezone.utc).isoformat()),
        ).fetchone()
    else:
        row = conn.execute(
            "UPDATE story_arcs SET name = ?, description = ?, beats = ? WHERE id = ? RETURNING *",
            (name, description, beats_json, arc_id),
        ).fetchone()
    conn.commit()
    return _arc_row(row) if row else {}


def delete_story_arc(conn: sqlite3.Connection, arc_id: int) -> bool:
    cur = conn.execute("DELETE FROM story_arcs WHERE id = ?", (arc_id,))
    conn.commit()
    return cur.rowcount > 0


# ---- 出版物 ----


def _publication(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    item["items"] = json.loads(item["items"] or "[]")
    return item


def create_publication(conn: sqlite3.Connection, title: str) -> dict[str, Any]:
    """出版物の記録を作る(中身はまだ無い。作り終えたら update_publication で埋める)。"""
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute("INSERT INTO publications (title, created_at) VALUES (?, ?)", (title, now))
    conn.commit()
    return get_publication(conn, int(cur.lastrowid or 0)) or {}


def get_publication(conn: sqlite3.Connection, publication_id: int) -> dict[str, Any] | None:
    return _publication(conn.execute("SELECT * FROM publications WHERE id = ?", (publication_id,)).fetchone())


def list_publications(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """作り終えた出版物(新しい順)。"""
    rows = conn.execute("SELECT * FROM publications WHERE zip_path IS NOT NULL ORDER BY id DESC").fetchall()
    return [item for row in rows if (item := _publication(row)) is not None]


def update_publication(
    conn: sqlite3.Connection,
    publication_id: int,
    *,
    dir: str,  # noqa: A002
    zip_path: str,
    bytes: int,  # noqa: A002
    file_count: int,
    items: list[dict[str, Any]],
) -> dict[str, Any] | None:
    conn.execute(
        "UPDATE publications SET dir = ?, zip_path = ?, bytes = ?, file_count = ?, items = ? WHERE id = ?",
        (dir, zip_path, bytes, file_count, json.dumps(items, ensure_ascii=False), publication_id),
    )
    conn.commit()
    return get_publication(conn, publication_id)


def delete_publication(conn: sqlite3.Connection, publication_id: int) -> None:
    conn.execute("DELETE FROM publications WHERE id = ?", (publication_id,))
    conn.commit()
