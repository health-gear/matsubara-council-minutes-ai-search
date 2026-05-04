"""
SQLiteデータベース操作モジュール
"""

import sqlite3
import json
from contextlib import contextmanager
from pathlib import Path

from .config import DB_PATH


def init_db():
    """データベースとテーブルを初期化する"""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript("""
        -- 会議（定例会・委員会など）
        CREATE TABLE IF NOT EXISTS councils (
            council_id  INTEGER PRIMARY KEY,
            name        TEXT NOT NULL,
            year_label  TEXT NOT NULL,
            council_type TEXT,      -- '本会議' / '委員会'
            scraped_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- 各会議の開催日・号数
        CREATE TABLE IF NOT EXISTS schedules (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            council_id  INTEGER NOT NULL,
            schedule_id INTEGER NOT NULL,
            name        TEXT NOT NULL,
            UNIQUE(council_id, schedule_id),
            FOREIGN KEY (council_id) REFERENCES councils(council_id)
        );

        -- 議事録本文（生テキスト）
        CREATE TABLE IF NOT EXISTS minutes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            council_id  INTEGER NOT NULL,
            schedule_id INTEGER NOT NULL,
            raw_text    TEXT NOT NULL,
            scraped_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(council_id, schedule_id),
            FOREIGN KEY (council_id) REFERENCES councils(council_id)
        );

        -- 発言単位に分割したデータ
        CREATE TABLE IF NOT EXISTS speeches (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            minute_id    INTEGER NOT NULL,
            order_num    INTEGER NOT NULL,   -- 発言順序
            page_num     INTEGER,            -- 議事録のページ番号
            speaker_name TEXT NOT NULL,      -- 発言者名
            speaker_role TEXT,               -- 役職（議長・議員・市長・部長 等）
            speaker_type TEXT NOT NULL,      -- 'chair'/'member'/'official'
            content      TEXT NOT NULL,      -- 発言内容
            FOREIGN KEY (minute_id) REFERENCES minutes(id)
        );

        -- AI要約（発言単位 または Q&Aペア単位）
        CREATE TABLE IF NOT EXISTS summaries (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            speech_id        INTEGER,         -- speeches.id（質問発言）
            answer_speech_id INTEGER,         -- 対応する答弁 speeches.id
            topic            TEXT,            -- テーマ・議題
            question_summary TEXT,            -- 質問の要旨
            answer_summary   TEXT,            -- 答弁の要旨
            keywords         TEXT,            -- JSON配列
            model            TEXT,            -- 使用したAIモデル
            created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (speech_id) REFERENCES speeches(id)
        );

        -- 全文検索インデックス（speeches）
        CREATE VIRTUAL TABLE IF NOT EXISTS speeches_fts USING fts5(
            speaker_name,
            speaker_role,
            content,
            content='speeches',
            content_rowid='id'
        );

        -- 全文検索インデックス（summaries）
        CREATE VIRTUAL TABLE IF NOT EXISTS summaries_fts USING fts5(
            topic,
            question_summary,
            answer_summary,
            keywords,
            content='summaries',
            content_rowid='id'
        );

        -- speeches に INSERT されたら FTS を自動更新するトリガー
        CREATE TRIGGER IF NOT EXISTS speeches_ai
        AFTER INSERT ON speeches BEGIN
            INSERT INTO speeches_fts(rowid, speaker_name, speaker_role, content)
            VALUES (new.id, new.speaker_name, new.speaker_role, new.content);
        END;
        """)
    print(f"[DB] 初期化完了: {DB_PATH}")


@contextmanager
def get_conn():
    """コンテキストマネージャーでDB接続を管理"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---- councils ----

def upsert_council(council_id: int, name: str, year_label: str, council_type: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO councils (council_id, name, year_label, council_type)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(council_id) DO UPDATE SET
                name=excluded.name,
                year_label=excluded.year_label,
                council_type=excluded.council_type
        """, (council_id, name, year_label, council_type))


def get_all_councils() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM councils ORDER BY council_id DESC").fetchall()
        return [dict(r) for r in rows]


# ---- schedules ----

def upsert_schedule(council_id: int, schedule_id: int, name: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO schedules (council_id, schedule_id, name)
            VALUES (?, ?, ?)
            ON CONFLICT(council_id, schedule_id) DO UPDATE SET name=excluded.name
        """, (council_id, schedule_id, name))


def get_schedules_for_council(council_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM schedules WHERE council_id=? ORDER BY schedule_id",
            (council_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ---- minutes ----

def has_minute(council_id: int, schedule_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM minutes WHERE council_id=? AND schedule_id=?",
            (council_id, schedule_id)
        ).fetchone()
        return row is not None


def insert_minute(council_id: int, schedule_id: int, raw_text: str) -> int:
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO minutes (council_id, schedule_id, raw_text)
            VALUES (?, ?, ?)
            ON CONFLICT(council_id, schedule_id) DO UPDATE SET raw_text=excluded.raw_text
        """, (council_id, schedule_id, raw_text))
        return cur.lastrowid


def get_unsummarized_minutes(limit: int = 50) -> list[dict]:
    """要約未処理の議事録を返す"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT m.*, c.name as council_name, s.name as schedule_name
            FROM minutes m
            JOIN councils c ON m.council_id = c.council_id
            JOIN schedules s ON m.council_id = s.council_id AND m.schedule_id = s.schedule_id
            WHERE m.id NOT IN (
                SELECT DISTINCT sp.minute_id FROM speeches sp
                WHERE sp.minute_id IS NOT NULL
            )
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


# ---- speeches ----

def insert_speeches(minute_id: int, speeches: list[dict]):
    with get_conn() as conn:
        conn.executemany("""
            INSERT INTO speeches
                (minute_id, order_num, page_num, speaker_name, speaker_role, speaker_type, content)
            VALUES (:minute_id, :order_num, :page_num, :speaker_name, :speaker_role, :speaker_type, :content)
        """, [{"minute_id": minute_id, **s} for s in speeches])


def get_speech_by_id(speech_id: int) -> dict | None:
    """発言IDから発言データを取得する（会議情報付き）"""
    with get_conn() as conn:
        row = conn.execute("""
            SELECT sp.*,
                   m.council_id, m.schedule_id,
                   c.name as council_name, s.name as schedule_name
            FROM speeches sp
            JOIN minutes m ON sp.minute_id = m.id
            JOIN councils c ON m.council_id = c.council_id
            JOIN schedules s ON m.council_id = s.council_id AND m.schedule_id = s.schedule_id
            WHERE sp.id = ?
        """, (speech_id,)).fetchone()
        return dict(row) if row else None


def get_speeches_for_minute(minute_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM speeches WHERE minute_id=? ORDER BY order_num",
            (minute_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_unsummarized_speeches(limit: int = 100) -> list[dict]:
    """要約未処理の議員発言（speaker_type='member'）を返す"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT sp.*,
                   m.council_id, m.schedule_id,
                   c.name as council_name, s.name as schedule_name
            FROM speeches sp
            JOIN minutes m ON sp.minute_id = m.id
            JOIN councils c ON m.council_id = c.council_id
            JOIN schedules s ON m.council_id = s.council_id AND m.schedule_id = s.schedule_id
            WHERE sp.speaker_type = 'member'
              AND sp.id NOT IN (SELECT DISTINCT speech_id FROM summaries WHERE speech_id IS NOT NULL)
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_unparsed_minutes() -> list[dict]:
    """speeches に分解されていない minutes を全件返す"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT m.*, c.name as council_name, s.name as schedule_name
            FROM minutes m
            JOIN councils c ON m.council_id = c.council_id
            JOIN schedules s ON m.council_id = s.council_id AND m.schedule_id = s.schedule_id
            WHERE m.id NOT IN (
                SELECT DISTINCT minute_id FROM speeches WHERE minute_id IS NOT NULL
            )
            ORDER BY m.council_id DESC
        """).fetchall()
        return [dict(r) for r in rows]


def get_distinct_years() -> list[str]:
    """DB に存在する year_label を古い順（平成30年→令和7年）で返す"""
    year_order = [
        "平成30年",
        "令和元年", "令和1年", "令和元年/平成31年",
        "令和2年", "令和3年", "令和4年",
        "令和5年", "令和6年", "令和7年",
    ]
    with get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT year_label FROM councils").fetchall()
    labels = {r["year_label"] for r in rows if r["year_label"]}
    ordered = [y for y in year_order if y in labels]
    # year_order 未定義のものは末尾に追加
    ordered += sorted(labels - set(year_order))
    return ordered


def search_speeches(query: str, limit: int = 20,
                    year_labels: list[str] | None = None) -> list[dict]:
    """
    キーワードで発言テキストを検索する（LIKE検索）。
    日本語はスペース区切りがないためFTSではなくLIKEを使用する。
    議員発言（member）のみを返し、会議情報も付与する。
    year_labels を指定すると、その年度だけに絞り込む。
    """
    like = f"%{query}%"

    # 年度フィルター
    year_clause = ""
    year_params: list = []
    if year_labels:
        placeholders = ",".join(["?" for _ in year_labels])
        year_clause = f"AND c.year_label IN ({placeholders})"
        year_params = list(year_labels)

    with get_conn() as conn:
        if limit and limit > 0:
            rows = conn.execute(f"""
                SELECT sp.*,
                       m.council_id, m.schedule_id,
                       c.name as council_name, s.name as schedule_name
                FROM speeches sp
                JOIN minutes m ON sp.minute_id = m.id
                JOIN councils c ON m.council_id = c.council_id
                JOIN schedules s ON m.council_id = s.council_id AND m.schedule_id = s.schedule_id
                WHERE (sp.content LIKE ? OR sp.speaker_name LIKE ?)
                  AND sp.speaker_type = 'member'
                  {year_clause}
                ORDER BY m.council_id DESC, sp.order_num ASC
                LIMIT ?
            """, (like, like, *year_params, limit)).fetchall()
        else:
            # limit=0 のとき全件取得
            rows = conn.execute(f"""
                SELECT sp.*,
                       m.council_id, m.schedule_id,
                       c.name as council_name, s.name as schedule_name
                FROM speeches sp
                JOIN minutes m ON sp.minute_id = m.id
                JOIN councils c ON m.council_id = c.council_id
                JOIN schedules s ON m.council_id = s.council_id AND m.schedule_id = s.schedule_id
                WHERE (sp.content LIKE ? OR sp.speaker_name LIKE ?)
                  AND sp.speaker_type = 'member'
                  {year_clause}
                ORDER BY m.council_id DESC, sp.order_num ASC
            """, (like, like, *year_params)).fetchall()
        return [dict(r) for r in rows]


def get_summary_for_speech(speech_id: int) -> dict | None:
    """指定の発言 ID の要約キャッシュを返す（なければ None）"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM summaries WHERE speech_id = ?",
            (speech_id,)
        ).fetchone()
        return dict(row) if row else None


def rebuild_speeches_fts():
    """speeches_fts を speeches テーブルから全件再構築する"""
    with get_conn() as conn:
        conn.execute("INSERT INTO speeches_fts(speeches_fts) VALUES('rebuild')")


# ---- summaries ----

def insert_summary(speech_id: int, answer_speech_id: int | None,
                   topic: str, question_summary: str, answer_summary: str,
                   keywords: list[str], model: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO summaries
                (speech_id, answer_speech_id, topic, question_summary,
                 answer_summary, keywords, model)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (speech_id, answer_speech_id, topic, question_summary,
              answer_summary, json.dumps(keywords, ensure_ascii=False), model))


# ---- 検索 ----

def search_summaries(query: str, limit: int = 20) -> list[dict]:
    """全文検索（キーワード）"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT su.*, sp.speaker_name, sp.speaker_role,
                   m.council_id, m.schedule_id,
                   c.name as council_name, s.name as schedule_name
            FROM summaries_fts sf
            JOIN summaries su ON sf.rowid = su.id
            JOIN speeches sp ON su.speech_id = sp.id
            JOIN minutes m ON sp.minute_id = m.id
            JOIN councils c ON m.council_id = c.council_id
            JOIN schedules s ON m.council_id = s.council_id AND m.schedule_id = s.schedule_id
            WHERE summaries_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (query, limit)).fetchall()
        return [dict(r) for r in rows]
