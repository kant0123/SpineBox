"""SQLite の接続とマイグレーション。

スキーマは `PRAGMA user_version` を版数にして、MIGRATIONS を先頭から順に適用する。
既存のマイグレーションは書き換えず、変更は末尾に足す。

蔵書レコード・読書位置は**消さない**のが前提(CLAUDE.md「蔵書を扱う不変条件」)。
見えなくなったルート・ソースは offline / missing の印を付けるだけで、DELETE は scan_issues
(再導出できる記録)にしか使わない。整理処理(CBZ 化)した元の巻も消さず、organized_op_id を立てて一覧から外す。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS: list[list[str]] = [
    # 1: 初期スキーマ
    [
        """
        CREATE TABLE library_roots (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            online INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            last_checked_at TEXT,
            last_online_at TEXT
        )
        """,
        """
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY,
            root_id INTEGER NOT NULL REFERENCES library_roots(id),
            rel_path TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('folder', 'archive')),
            size INTEGER,
            mtime_ns INTEGER,
            scan_version INTEGER,
            offline INTEGER NOT NULL DEFAULT 0,
            missing INTEGER NOT NULL DEFAULT 0,
            scanned_at TEXT,
            UNIQUE (root_id, rel_path)
        )
        """,
        """
        CREATE TABLE volumes (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            container_path TEXT,
            entry_prefix TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            name_raw BLOB,
            name_encoding TEXT,
            page_count INTEGER,
            missing INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE UNIQUE INDEX volumes_locator ON volumes (source_id, COALESCE(container_path, ''), entry_prefix)",
        """
        CREATE TABLE reading_progress (
            volume_id INTEGER PRIMARY KEY REFERENCES volumes(id),
            page INTEGER NOT NULL,
            finished INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE scan_issues (
            id INTEGER PRIMARY KEY,
            source_id INTEGER REFERENCES sources(id),
            container_path TEXT,
            entry_name TEXT,
            reason TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX scan_issues_source ON scan_issues (source_id)",
    ],
    # 2: 巻への分解(#30)。内側アーカイブを解析して見つけた巻の単位を子の巻として持つ。
    #    parent_id: 親(まとめ)の巻。inner_path: 親の内側アーカイブから子の単位までの入れ子のアーカイブ名
    #    (JSON 配列。子だけが持ち、親・スキャンで出る巻は NULL)。number_*: 名前から読んだ巻・話の番号の候補。
    #    analyzed_version: 解析した時点の元ファイルの版(NULL = 未解析)。analysis_error: 解析できなかった理由
    [
        "ALTER TABLE volumes ADD COLUMN parent_id INTEGER REFERENCES volumes(id)",
        "ALTER TABLE volumes ADD COLUMN inner_path TEXT",
        "ALTER TABLE volumes ADD COLUMN number_kind TEXT",
        "ALTER TABLE volumes ADD COLUMN number_start INTEGER",
        "ALTER TABLE volumes ADD COLUMN number_end INTEGER",
        "ALTER TABLE volumes ADD COLUMN number_evidence TEXT",
        "ALTER TABLE volumes ADD COLUMN analyzed_version TEXT",
        "ALTER TABLE volumes ADD COLUMN analysis_error TEXT",
        "DROP INDEX volumes_locator",
        """CREATE UNIQUE INDEX volumes_locator ON volumes
           (source_id, COALESCE(container_path, ''), COALESCE(inner_path, ''), entry_prefix)""",
        "CREATE INDEX volumes_parent ON volumes (parent_id)",
    ],
    # 3: 整理処理(CBZ 化)の操作ジャーナル(#31)。元の巻は消さず organized_op_id を立てて一覧から外す
    [
        """
        CREATE TABLE organize_ops (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL DEFAULT 'cbz',
            source_id INTEGER NOT NULL REFERENCES sources(id),
            state TEXT NOT NULL CHECK (state IN
                ('planned', 'running', 'done', 'failed', 'undoing', 'undone', 'undo_failed', 'rollback_failed')),
            plan_json TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            executed_at TEXT,
            undone_at TEXT
        )
        """,
        """
        CREATE TABLE organize_steps (
            id INTEGER PRIMARY KEY,
            op_id TEXT NOT NULL REFERENCES organize_ops(id),
            phase TEXT NOT NULL CHECK (phase IN ('execute', 'undo')),
            action TEXT NOT NULL,
            from_path TEXT NOT NULL,
            to_path TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('pending', 'done', 'reverted', 'revert_failed')),
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX organize_steps_op ON organize_steps (op_id)",
        """
        CREATE TABLE organize_volumes (
            op_id TEXT NOT NULL REFERENCES organize_ops(id),
            old_volume_id INTEGER NOT NULL REFERENCES volumes(id),
            new_volume_id INTEGER REFERENCES volumes(id),
            output_rel_path TEXT NOT NULL,
            page_count INTEGER,
            PRIMARY KEY (op_id, old_volume_id)
        )
        """,
        "ALTER TABLE volumes ADD COLUMN organized_op_id TEXT REFERENCES organize_ops(id)",
    ],
    # 4: 別スキャンの重複候補(#38)。volume_fingerprints: 巻ごとの見た目の指紋(ページ位置とハッシュの組の BLOB)。
    #    version が変わるまで作り直さない。error: 作れなかった理由(明示操作の再実行で再挑戦する)。
    #    duplicate_candidates: 重複の候補の組(volume_a < volume_b)と判定の根拠(JSON)。指紋から再導出できる。
    #    dismissed_at: 「重複ではない」と却下した日時。再計算しても消さない・復活させない
    [
        """
        CREATE TABLE volume_fingerprints (
            volume_id INTEGER PRIMARY KEY REFERENCES volumes(id),
            version TEXT NOT NULL,
            page_count INTEGER NOT NULL,
            hashed_pages INTEGER NOT NULL,
            skipped_pages INTEGER NOT NULL,
            hashes BLOB NOT NULL,
            error TEXT,
            computed_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE duplicate_candidates (
            id INTEGER PRIMARY KEY,
            volume_a INTEGER NOT NULL REFERENCES volumes(id),
            volume_b INTEGER NOT NULL REFERENCES volumes(id),
            rule TEXT NOT NULL,
            matched_pages INTEGER NOT NULL,
            compared_pages INTEGER NOT NULL,
            evidence TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            dismissed_at TEXT,
            CHECK (volume_a < volume_b),
            UNIQUE (volume_a, volume_b)
        )
        """,
        "CREATE INDEX duplicate_candidates_b ON duplicate_candidates (volume_b)",
    ],
    # 5: 公開 DB(国立国会図書館サーチ・AniList・MangaUpdates)の生応答。同じ要求は有効期限内なら使い回す(#44)。
    # request は秘匿を除いた要求の記述(JSON)、body は応答本文のバイト列そのまま。作品・巻には結び付けない
    [
        """
        CREATE TABLE metadata_responses (
            id INTEGER PRIMARY KEY,
            source TEXT NOT NULL,
            request_key TEXT NOT NULL,
            request TEXT NOT NULL,
            status INTEGER NOT NULL,
            body BLOB NOT NULL,
            fetched_at TEXT NOT NULL,
            UNIQUE (source, request_key)
        )
        """,
    ],
    # 6: 作品の名寄せ(#43)。works: 作品(merged_into: 統合されて別の作品に吸収された。行は消さない)。
    #    work_fields: 作品の項目(今は title / author)を項目ごとに持つ。source: 出所(filename / container / merge /
    #    manual / 後で public_db など)、manual: 利用者が手で編集した(再計算・外部ソースの取得で上書きしない)、
    #    confidence・evidence: 確信度と根拠(JSON)。項目を足すときは行を足すだけでよい(列は足さない)。
    #    work_aliases: 別名(norm は突き合わせ用の正規化)。dismissed_at: 利用者が外した別名(再計算で戻さない)。
    #    work_volumes: 巻 → 作品の割り当て(巻あたり 1 行)。source: filename / duplicate / alias / manual / 後で public_db、
    #    manual: 利用者が決めた割り当て(再計算で動かさない)。
    #    work_merge_proposals: 2 作品の統合の提案と、その結果(open: 確認待ち / auto: 確信度が高く自動で統合した /
    #    approved: 承認 / dismissed: 却下)。統合した後も履歴として残す(根拠を画面に出すため)。
    [
        """
        CREATE TABLE works (
            id INTEGER PRIMARY KEY,
            merged_into INTEGER REFERENCES works(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE work_fields (
            work_id INTEGER NOT NULL REFERENCES works(id),
            field TEXT NOT NULL,
            value TEXT,
            source TEXT NOT NULL,
            manual INTEGER NOT NULL DEFAULT 0,
            confidence REAL,
            evidence TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (work_id, field)
        )
        """,
        """
        CREATE TABLE work_aliases (
            id INTEGER PRIMARY KEY,
            work_id INTEGER NOT NULL REFERENCES works(id),
            alias TEXT NOT NULL,
            norm TEXT NOT NULL,
            source TEXT NOT NULL,
            manual INTEGER NOT NULL DEFAULT 0,
            confidence REAL,
            evidence TEXT,
            dismissed_at TEXT,
            created_at TEXT NOT NULL,
            UNIQUE (work_id, norm)
        )
        """,
        "CREATE INDEX work_aliases_norm ON work_aliases (norm)",
        """
        CREATE TABLE work_volumes (
            volume_id INTEGER PRIMARY KEY REFERENCES volumes(id),
            work_id INTEGER NOT NULL REFERENCES works(id),
            source TEXT NOT NULL,
            manual INTEGER NOT NULL DEFAULT 0,
            confidence REAL,
            evidence TEXT,
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX work_volumes_work ON work_volumes (work_id)",
        """
        CREATE TABLE work_merge_proposals (
            id INTEGER PRIMARY KEY,
            work_a INTEGER NOT NULL REFERENCES works(id),
            work_b INTEGER NOT NULL REFERENCES works(id),
            rule TEXT NOT NULL,
            confidence REAL NOT NULL,
            evidence TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('open', 'auto', 'approved', 'dismissed')),
            created_at TEXT NOT NULL,
            decided_at TEXT
        )
        """,
        "CREATE INDEX work_merge_proposals_status ON work_merge_proposals (status)",
    ],
    # 7: 公開 DB の情報の結線(#50)。work_links: 作品 × 取得元(ndl / anilist / mangaupdates)ごとに 1 行。
    #    status: linked(連携済み)/ pending(候補が拮抗・確信度が低く、利用者の確認待ち)/ none(見つからない・
    #    利用者が「該当なし」にした)/ NULL(取得できたことがまだ無い)。manual: 利用者が候補を選んだ(再取得で変えない)。
    #    item: 連携した候補の中身(JSON。項目の反映に使う)、candidates: 利用者に見せる候補(JSON。概要と中身)、
    #    evidence: 確信度の根拠と使った生応答の鍵(JSON)、failure: 直近の取得の失敗(JSON。成功すると NULL)。
    #    attempted_at: 最後に取得を試みた日時、checked_at: 最後に取得できた日時。
    #    volume_fields: 巻単位の書誌(publisher / label / released / isbn / volume_label / subtitle)。
    #    work_fields と同じく項目ごとに source・manual・confidence・evidence を持ち、manual は再取得で上書きしない。
    #    作品単位の新しい項目(kind / publisher / genres / tags / synopsis / total_volumes)は work_fields に行を足す
    [
        """
        CREATE TABLE work_links (
            work_id INTEGER NOT NULL REFERENCES works(id),
            source TEXT NOT NULL,
            status TEXT CHECK (status IN ('linked', 'pending', 'none')),
            source_id TEXT,
            manual INTEGER NOT NULL DEFAULT 0,
            confidence REAL,
            evidence TEXT,
            item TEXT,
            candidates TEXT,
            failure TEXT,
            attempted_at TEXT,
            checked_at TEXT,
            PRIMARY KEY (work_id, source)
        )
        """,
        """
        CREATE TABLE volume_fields (
            volume_id INTEGER NOT NULL REFERENCES volumes(id),
            field TEXT NOT NULL,
            value TEXT,
            source TEXT NOT NULL,
            manual INTEGER NOT NULL DEFAULT 0,
            confidence REAL,
            evidence TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (volume_id, field)
        )
        """,
    ],
    # 8: 小説(EPUB・テキスト)の巻(#57)。volumes.format: NULL = 画像の巻(漫画など)、'epub' / 'text' = 小説。
    #    小説のソースは 1 ファイルなので sources.kind は 'archive'(CHECK を作り直さないため。読み分けは format で行う)。
    #    小説の page_count は区切り(EPUB の spine の本文 / テキストの一定文字数ごと)の数で、開いたときに決まる。
    #    reading_progress.position: 小説の区切りの中の割合(0〜1)。page は区切りの番号。画像の巻は NULL
    [
        "ALTER TABLE volumes ADD COLUMN format TEXT",
        "ALTER TABLE reading_progress ADD COLUMN position REAL",
    ],
]


class SchemaTooNew(RuntimeError):
    """DB がこのコードより新しい版数で作られている。"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | str) -> sqlite3.Connection:
    """接続を開く。トランザクションは呼び出し側が `with transaction(conn):` で明示する。"""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


class transaction:
    """BEGIN IMMEDIATE ... COMMIT / ROLLBACK。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self) -> sqlite3.Connection:
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> None:
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")


def migrate(conn: sqlite3.Connection) -> int:
    """未適用のマイグレーションを順に適用し、適用後の版数を返す。"""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > len(MIGRATIONS):
        raise SchemaTooNew(f"DB の版数 {current} はこのコードの対応版数 {len(MIGRATIONS)} より新しい")
    for version in range(current + 1, len(MIGRATIONS) + 1):
        with transaction(conn):
            for statement in MIGRATIONS[version - 1]:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {version}")
    return len(MIGRATIONS)


def open_db(path: Path | str) -> sqlite3.Connection:
    conn = connect(path)
    migrate(conn)
    return conn
