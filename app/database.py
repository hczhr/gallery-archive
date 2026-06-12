import sqlite3
import os
import threading

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
DB_PATH = os.path.join(DATA_DIR, "gallery.db")
SQLITE_BUSY_TIMEOUT_MS = int(os.environ.get("SQLITE_BUSY_TIMEOUT_MS", "30000"))
SQLITE_JOURNAL_SIZE_LIMIT = int(os.environ.get("SQLITE_JOURNAL_SIZE_LIMIT", "67108864"))
SQLITE_MMAP_SIZE = int(os.environ.get("SQLITE_MMAP_SIZE", "268435456"))

_local = threading.local()


def _configure_connection(conn: sqlite3.Connection):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute(f"PRAGMA journal_size_limit={SQLITE_JOURNAL_SIZE_LIMIT}")
    conn.execute(f"PRAGMA mmap_size={SQLITE_MMAP_SIZE}")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA foreign_keys=ON")


def get_db() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH, timeout=30)
        _local.conn.row_factory = sqlite3.Row
        _configure_connection(_local.conn)
    return _local.conn


def close_db():
    if hasattr(_local, "conn") and _local.conn is not None:
        _local.conn.close()
        _local.conn = None


def _artist_name_has_unique_constraint(conn: sqlite3.Connection) -> bool:
    for row in conn.execute("PRAGMA index_list(artists)").fetchall():
        index_name = row[1]
        is_unique = bool(row[2])
        if not is_unique:
            continue
        columns = [
            col[2] for col in conn.execute(f"PRAGMA index_info({index_name})").fetchall()
        ]
        if columns == ["name"]:
            return True
    return False


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_column(conn: sqlite3.Connection, table: str, column_definition: str):
    column_name = column_definition.split()[0]
    if column_name not in _table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_definition}")


def _ensure_move_schema(conn: sqlite3.Connection):
    _ensure_column(conn, "artists", "missing INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "artists", "missing_at REAL")

    _ensure_column(conn, "items", "content_hash TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "items", "hash_status TEXT NOT NULL DEFAULT 'pending'")
    _ensure_column(conn, "items", "hash_updated_at REAL")
    _ensure_column(conn, "items", "st_dev INTEGER")
    _ensure_column(conn, "items", "st_ino INTEGER")
    _ensure_column(conn, "items", "missing INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "items", "missing_at REAL")
    _ensure_column(conn, "items", "media_type TEXT NOT NULL DEFAULT 'image'")

    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_items_missing ON items(artist_id, missing);
        CREATE INDEX IF NOT EXISTS idx_items_hash_missing
            ON items(artist_id, content_hash, missing);
        CREATE INDEX IF NOT EXISTS idx_items_inode_missing
            ON items(artist_id, st_dev, st_ino, missing);
        CREATE INDEX IF NOT EXISTS idx_items_media
            ON items(artist_id, media_type, missing);
        CREATE INDEX IF NOT EXISTS idx_items_hash_queue
            ON items(missing, hash_status, id);
        CREATE INDEX IF NOT EXISTS idx_artists_missing ON artists(missing);

        CREATE TABLE IF NOT EXISTS scan_seen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id TEXT NOT NULL,
            artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
            file_path TEXT NOT NULL,
            media_type TEXT NOT NULL DEFAULT 'image',
            file_size INTEGER NOT NULL DEFAULT 0,
            file_mtime REAL NOT NULL DEFAULT 0,
            st_dev INTEGER,
            st_ino INTEGER,
            content_hash TEXT NOT NULL DEFAULT '',
            hash_status TEXT NOT NULL DEFAULT 'pending',
            created_at REAL NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE INDEX IF NOT EXISTS idx_scan_seen_scan_artist
            ON scan_seen(scan_id, artist_id);
        CREATE INDEX IF NOT EXISTS idx_scan_seen_hash
            ON scan_seen(scan_id, artist_id, content_hash);
        CREATE INDEX IF NOT EXISTS idx_scan_seen_path
            ON scan_seen(scan_id, file_path);

        CREATE TABLE IF NOT EXISTS scan_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id TEXT NOT NULL,
            artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
            file_path TEXT NOT NULL,
            file_name TEXT NOT NULL,
            file_size INTEGER NOT NULL DEFAULT 0,
            file_mtime REAL NOT NULL DEFAULT 0,
            folder_name TEXT NOT NULL DEFAULT '',
            date TEXT NOT NULL DEFAULT '',
            is_archive INTEGER NOT NULL DEFAULT 0,
            media_type TEXT NOT NULL DEFAULT 'image',
            content_hash TEXT NOT NULL DEFAULT '',
            hash_status TEXT NOT NULL DEFAULT 'pending',
            st_dev INTEGER,
            st_ino INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
            resolved_at REAL
        );

        CREATE INDEX IF NOT EXISTS idx_scan_candidates_status
            ON scan_candidates(status, artist_id);
        CREATE INDEX IF NOT EXISTS idx_scan_candidates_path
            ON scan_candidates(file_path);
        CREATE INDEX IF NOT EXISTS idx_scan_candidates_hash
            ON scan_candidates(artist_id, content_hash, status);
        CREATE INDEX IF NOT EXISTS idx_scan_candidates_hash_queue
            ON scan_candidates(status, hash_status, id);

        CREATE TABLE IF NOT EXISTS move_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_candidate_id INTEGER REFERENCES scan_candidates(id) ON DELETE SET NULL,
            item_id INTEGER REFERENCES items(id) ON DELETE CASCADE,
            artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
            old_path TEXT NOT NULL,
            new_path TEXT NOT NULL,
            reason TEXT NOT NULL,
            content_hash TEXT NOT NULL DEFAULT '',
            st_dev INTEGER,
            st_ino INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
            resolved_at REAL
        );

        CREATE INDEX IF NOT EXISTS idx_move_candidates_status
            ON move_candidates(status, artist_id);
        CREATE INDEX IF NOT EXISTS idx_move_candidates_new_path
            ON move_candidates(new_path);

        CREATE TABLE IF NOT EXISTS move_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
            old_path TEXT NOT NULL,
            new_path TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
            applied_at REAL,
            reverted_at REAL
        );

        CREATE INDEX IF NOT EXISTS idx_move_history_item
            ON move_history(item_id);
        CREATE INDEX IF NOT EXISTS idx_move_history_status
            ON move_history(status);
    """)
    _ensure_column(conn, "scan_seen", "media_type TEXT NOT NULL DEFAULT 'image'")
    _ensure_column(conn, "scan_candidates", "media_type TEXT NOT NULL DEFAULT 'image'")
    conn.execute("UPDATE items SET media_type='archive' WHERE is_archive=1")
    conn.execute("UPDATE scan_candidates SET media_type='archive' WHERE is_archive=1")


def _ensure_folder_rename_schema(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS folder_rename_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
            source_folder TEXT NOT NULL,
            original_folder_name TEXT NOT NULL DEFAULT '',
            original_title TEXT NOT NULL DEFAULT '',
            parsed_date TEXT NOT NULL DEFAULT '',
            selected_tag_ids TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'needs_tags',
            file_count INTEGER NOT NULL DEFAULT 0,
            total_size INTEGER NOT NULL DEFAULT 0,
            max_mtime REAL NOT NULL DEFAULT 0,
            created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
            updated_at REAL NOT NULL DEFAULT (strftime('%s','now')),
            confirmed_at REAL,
            confirmation_source TEXT NOT NULL DEFAULT '',
            UNIQUE(artist_id, source_folder)
        );

        CREATE INDEX IF NOT EXISTS idx_folder_rename_artist_status
            ON folder_rename_plans(artist_id, status);
    """)
    _ensure_column(conn, "folder_rename_plans", "target_folder TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "folder_rename_plans", "executed_at REAL")
    _ensure_column(conn, "folder_rename_plans", "execution_log TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(conn, "folder_rename_plans", "plan_kind TEXT NOT NULL DEFAULT 'rename_folder'")
    _ensure_column(conn, "folder_rename_plans", "split_actions TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(conn, "folder_rename_plans", "confirmation_source TEXT NOT NULL DEFAULT ''")


def _ensure_app_settings_schema(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )


def _migrate_artist_name_not_unique(conn: sqlite3.Connection):
    if not _artist_name_has_unique_constraint(conn):
        return

    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript("""
        CREATE TABLE artists_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            path TEXT UNIQUE NOT NULL,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        INSERT INTO artists_new (id, name, path, created_at)
        SELECT id, name, path, created_at FROM artists;

        DROP TABLE artists;
        ALTER TABLE artists_new RENAME TO artists;
    """)
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")


def _migrate_manual_roles_to_tags(conn: sqlite3.Connection):
    conn.execute("""
        INSERT OR IGNORE INTO tags (artist_id, name, sort_order)
        SELECT DISTINCT artist_id, manual_role, 0
        FROM items
        WHERE manual_role IS NOT NULL AND manual_role != ''
    """)
    conn.execute("""
        INSERT OR IGNORE INTO item_tags (item_id, tag_id)
        SELECT i.id, t.id
        FROM items i
        JOIN tags t ON t.artist_id = i.artist_id AND t.name = i.manual_role
        WHERE i.manual_role IS NOT NULL AND i.manual_role != ''
    """)


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    _configure_connection(conn)

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS artists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            path TEXT UNIQUE NOT NULL,
            missing INTEGER NOT NULL DEFAULT 0,
            missing_at REAL,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
            file_path TEXT UNIQUE NOT NULL,
            file_name TEXT NOT NULL,
            file_size INTEGER NOT NULL DEFAULT 0,
            file_mtime REAL NOT NULL DEFAULT 0,
            folder_name TEXT NOT NULL DEFAULT '',
            date TEXT NOT NULL DEFAULT '',
            auto_role TEXT NOT NULL DEFAULT '',
            manual_role TEXT DEFAULT NULL,
            tags TEXT NOT NULL DEFAULT '[]',
            is_archive INTEGER NOT NULL DEFAULT 0,
            media_type TEXT NOT NULL DEFAULT 'image',
            content_hash TEXT NOT NULL DEFAULT '',
            hash_status TEXT NOT NULL DEFAULT 'pending',
            hash_updated_at REAL,
            st_dev INTEGER,
            st_ino INTEGER,
            missing INTEGER NOT NULL DEFAULT 0,
            missing_at REAL,
            scanned_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE INDEX IF NOT EXISTS idx_items_artist ON items(artist_id);
        CREATE INDEX IF NOT EXISTS idx_items_role ON items(artist_id, manual_role);
        CREATE INDEX IF NOT EXISTS idx_items_auto_role ON items(artist_id, auto_role);
        CREATE INDEX IF NOT EXISTS idx_items_date ON items(artist_id, date);
        CREATE INDEX IF NOT EXISTS idx_items_archive ON items(artist_id, is_archive);
        CREATE INDEX IF NOT EXISTS idx_items_path ON items(file_path);

        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            UNIQUE(artist_id, name)
        );

        CREATE TABLE IF NOT EXISTS item_tags (
            item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            PRIMARY KEY (item_id, tag_id)
        );

        CREATE INDEX IF NOT EXISTS idx_tags_artist ON tags(artist_id);
        CREATE INDEX IF NOT EXISTS idx_item_tags_item ON item_tags(item_id);
        CREATE INDEX IF NOT EXISTS idx_item_tags_tag ON item_tags(tag_id);

        CREATE TABLE IF NOT EXISTS scan_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            artist_id INTEGER REFERENCES artists(id) ON DELETE SET NULL,
            status TEXT NOT NULL DEFAULT 'idle',
            phase TEXT NOT NULL DEFAULT '',
            scanned_count INTEGER NOT NULL DEFAULT 0,
            total_estimate INTEGER NOT NULL DEFAULT 0,
            current_path TEXT NOT NULL DEFAULT '',
            started_at REAL,
            updated_at REAL
        );

        INSERT OR IGNORE INTO scan_state (id, status) VALUES (1, 'idle');
    """)

    _migrate_artist_name_not_unique(conn)
    _ensure_move_schema(conn)
    _ensure_folder_rename_schema(conn)
    _ensure_app_settings_schema(conn)
    _migrate_manual_roles_to_tags(conn)

    conn.execute("""
        UPDATE scan_state
        SET status='idle',
            phase='interrupted',
            scanned_count=0,
            total_estimate=0,
            current_path='',
            updated_at=strftime('%s','now')
        WHERE id=1
          AND (
            status='scanning'
            OR (status='error' AND phase='interrupted')
          )
    """)
    conn.execute("UPDATE items SET hash_status='pending' WHERE hash_status='processing'")
    conn.execute("""
        UPDATE scan_candidates
        SET hash_status='pending'
        WHERE hash_status='processing' AND status IN ('pending', 'candidate')
    """)

    conn.commit()
    conn.close()
