import sqlite3
import logging
from pathlib import Path
from typing import List, Optional, Dict, Set

from database.models import Asset, Tag, Source

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        """Returns a new DB connection configured for WAL mode."""
        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=10.0 # Wait up to 10s if db is locked
        )
        conn.row_factory = sqlite3.Row
        # Enable WAL mode for concurrent reads/writes
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self):
        """Initializes database schema."""
        with self.get_connection() as conn:
            # Sources table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE,
                    domain TEXT
                )
            ''')
            
            # Projects table (Семантическая группировка)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    url TEXT UNIQUE,
                    author TEXT
                )
            ''')
            
            # Tags table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE
                )
            ''')
            
            # Assets table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS assets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    original_url TEXT,
                    local_path TEXT,
                    thumbnail_path TEXT,
                    phash TEXT,
                    width INTEGER,
                    height INTEGER,
                    created_at TIMESTAMP,
                    source_id INTEGER,
                    FOREIGN KEY(source_id) REFERENCES sources(id)
                )
            ''')
            
            # Asset-Tags relation
            conn.execute('''
                CREATE TABLE IF NOT EXISTS asset_tags (
                    asset_id INTEGER,
                    tag_id INTEGER,
                    PRIMARY KEY (asset_id, tag_id),
                    FOREIGN KEY(asset_id) REFERENCES assets(id),
                    FOREIGN KEY(tag_id) REFERENCES tags(id)
                )
            ''')
            
            # Indexes for faster lookup
            conn.execute('CREATE INDEX IF NOT EXISTS idx_phash ON assets(phash)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_original_url ON assets(original_url)')

            # Миграция: добавляем колонку is_favorite
            try:
                conn.execute("ALTER TABLE assets ADD COLUMN is_favorite INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass  # Колонка уже существует

            # Таблица удалённых картинок (чтобы не скачивать снова)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS deleted_assets (
                    original_url TEXT PRIMARY KEY,
                    deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reason TEXT
                )
            ''')

            conn.execute('CREATE INDEX IF NOT EXISTS idx_deleted_url ON deleted_assets(original_url)')

            try:
                conn.execute('ALTER TABLE deleted_assets ADD COLUMN phash TEXT')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_deleted_phash ON deleted_assets(phash)')
            except sqlite3.OperationalError:
                pass

            # Миграция: динамическое добавление колонок (безопасно для существующих данных)
            try:
                conn.execute('ALTER TABLE assets ADD COLUMN project_id INTEGER REFERENCES projects(id)')
            except sqlite3.OperationalError:
                pass # Колонка уже существует
                
            try:
                conn.execute('ALTER TABLE assets ADD COLUMN embedding_id INTEGER')
            except sqlite3.OperationalError:
                pass

            try:
                conn.execute("ALTER TABLE assets ADD COLUMN category TEXT DEFAULT '3d_render'")
            except sqlite3.OperationalError:
                pass

            try:
                conn.execute("ALTER TABLE projects ADD COLUMN location TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
                
            try:
                conn.execute("ALTER TABLE assets ADD COLUMN image_type TEXT DEFAULT 'Photography'")
            except sqlite3.OperationalError:
                pass

            conn.commit()

    # === Tag operations ===

    def add_tags_to_asset(self, asset_id: int, tags: List[str]):
        """Добавляет теги к ассету (создаёт новые, если не существуют)."""
        with self.get_connection() as conn:
            for tag_name in tags:
                tag_name = tag_name.lower().strip()
                if not tag_name:
                    continue
                # Создаём тег если нет
                conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))
                # Получаем ID
                cur = conn.cursor()
                cur.execute("SELECT id FROM tags WHERE name = ?", (tag_name,))
                row = cur.fetchone()
                if row:
                    tag_id = row['id']
                    conn.execute("INSERT OR IGNORE INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (asset_id, tag_id))

    def get_asset_tags(self, asset_id: int) -> List[str]:
        """Возвращает список тегов ассета."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT t.name FROM tags t
                JOIN asset_tags at ON t.id = at.tag_id
                WHERE at.asset_id = ?
                ORDER BY t.name
            """, (asset_id,))
            return [row['name'] for row in cur.fetchall()]

    def get_all_tags(self) -> Dict[str, int]:
        """Возвращает все теги с количеством ассетов. {tag_name: count}"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT t.name, COUNT(at.asset_id) as cnt
                FROM tags t
                LEFT JOIN asset_tags at ON t.id = at.tag_id
                GROUP BY t.id
                ORDER BY cnt DESC, t.name
            """)
            return {row['name']: row['cnt'] for row in cur.fetchall()}

    def get_assets_by_tag(self, tag_names: List[str]) -> List[int]:
        """Возвращает ID ассетов, у которых есть ВСЕ указанные теги."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            placeholders = ','.join('?' for _ in tag_names)
            cur.execute(f"""
                SELECT at.asset_id
                FROM asset_tags at
                JOIN tags t ON at.tag_id = t.id
                WHERE t.name IN ({placeholders})
                GROUP BY at.asset_id
                HAVING COUNT(DISTINCT t.name) = ?
            """, (*tag_names, len(tag_names)))
            return [row['asset_id'] for row in cur.fetchall()]

    def remove_tags_from_asset(self, asset_id: int, tag_names: List[str]):
        """Удаляет указанные теги у ассета."""
        with self.get_connection() as conn:
            for tag_name in tag_names:
                conn.execute("""
                    DELETE FROM asset_tags WHERE asset_id = ? AND tag_id = (SELECT id FROM tags WHERE name = ?)
                """, (asset_id, tag_name.lower().strip()))

    def has_tags(self, asset_id: int) -> bool:
        """Проверяет, есть ли у ассета теги."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as cnt FROM asset_tags WHERE asset_id = ?", (asset_id,))
            return cur.fetchone()['cnt'] > 0

    def get_untagged_assets(self, category: str = None) -> List[int]:
        """Возвращает ID ассетов без тегов."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            if category:
                cur.execute("""
                    SELECT a.id FROM assets a
                    WHERE a.id NOT IN (SELECT asset_id FROM asset_tags)
                    AND a.category = ?
                """, (category,))
            else:
                cur.execute("""
                    SELECT a.id FROM assets a
                    WHERE a.id NOT IN (SELECT asset_id FROM asset_tags)
                """)
            return [row['id'] for row in cur.fetchall()]

    def set_embedding_id(self, asset_id: int, embedding_id: int):
        """Associates a FAISS embedding ID with an asset."""
        with self.get_connection() as conn:
            conn.execute("UPDATE assets SET embedding_id = ? WHERE id = ?", (embedding_id, asset_id))
            conn.commit()

    def set_embedding_ids_batch(self, asset_ids: List[int]):
        """Associates FAISS embedding IDs with multiple assets (ID = ID)."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            data = [(aid, aid) for aid in asset_ids]
            cur.executemany("UPDATE assets SET embedding_id = ? WHERE id = ?", data)
            conn.commit()

    def get_unindexed_assets(self) -> List[int]:
        """Returns IDs of assets that have thumbnails but no embedding_id."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id FROM assets
                WHERE thumbnail_path IS NOT NULL
                  AND thumbnail_path != ''
                  AND embedding_id IS NULL
            """)
            return [row['id'] for row in cur.fetchall()]

    def cleanup_missing_files(self) -> tuple:
        """Удаляет записи ассетов, у которых нет локального файла.

        Returns:
            (deleted_count, missing_paths) — количество удалённых и список отсутствующих путей
        """
        missing_paths = []
        deleted_count = 0

        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, local_path FROM assets")
            rows = cur.fetchall()

            for row in rows:
                asset_id = row['id']
                local_path = row['local_path']
                if local_path and not Path(local_path).exists():
                    missing_paths.append(local_path)
                    # Удаляем связанные теги
                    conn.execute("DELETE FROM asset_tags WHERE asset_id = ?", (asset_id,))
                    # Удаляем ассет
                    conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
                    deleted_count += 1

            conn.commit()

        # Также чистим неиспользуемые теги
        with self.get_connection() as conn:
            conn.execute("""
                DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM asset_tags)
            """)
            conn.commit()

        logger.info(f"Cleanup: deleted {deleted_count} assets with missing files")
        return deleted_count, missing_paths

    # === Favorite operations ===

    def toggle_favorite(self, asset_id: int) -> bool:
        """Переключает статус избранного. Возвращает новый статус."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT is_favorite FROM assets WHERE id = ?", (asset_id,))
            row = cur.fetchone()
            if row:
                new_status = 0 if row['is_favorite'] else 1
                conn.execute("UPDATE assets SET is_favorite = ? WHERE id = ?", (new_status, asset_id))
                conn.commit()
                return bool(new_status)
        return False

    def get_favorite_assets(self) -> List[int]:
        """Возвращает ID ассетов в избранном."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM assets WHERE is_favorite = 1")
            return [row['id'] for row in cur.fetchall()]

    def is_favorite(self, asset_id: int) -> bool:
        """Проверяет, в избранном ли ассет."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT is_favorite FROM assets WHERE id = ?", (asset_id,))
            row = cur.fetchone()
            return bool(row and row['is_favorite'])

    # === Deleted assets operations ===

    def mark_as_deleted(self, original_url: str, reason: str = "user_deleted", phash: str = None):
        """Помечает URL как удаленный, чтобы не парсить заново."""
        with self.get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO deleted_assets (original_url, phash, reason) VALUES (?, ?, ?)",
                (original_url, phash, reason)
            )
            conn.commit()

    def is_deleted(self, original_url: str = None, phash: str = None) -> bool:
        """Проверяет, был ли этот URL или pHash удален."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            if phash:
                cur.execute("SELECT 1 FROM deleted_assets WHERE phash = ?", (phash,))
                if cur.fetchone():
                    return True
            if original_url:
                cur.execute("SELECT 1 FROM deleted_assets WHERE original_url = ?", (original_url,))
                if cur.fetchone():
                    return True
            return False

    def get_deleted_count(self) -> int:
        """Возвращает количество удалённых ассетов."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as cnt FROM deleted_assets")
            return cur.fetchone()['cnt']

    # CRUD operations will follow:
    # def insert_asset(self, asset: Asset) -> int: ...
    # def get_asset(self, asset_id: int) -> Optional[Asset]: ...
    # def get_all_assets(self) -> List[Asset]: ...
