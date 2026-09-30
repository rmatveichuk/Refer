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

            try:
                conn.execute("ALTER TABLE assets ADD COLUMN description TEXT DEFAULT ''")
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

    def get_all_tags(self, limit: int = 50) -> Dict[str, int]:
        """Возвращает все теги с количеством ассетов. {tag_name: count}"""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"""
                SELECT t.name, COUNT(at.asset_id) as cnt
                FROM tags t
                LEFT JOIN asset_tags at ON t.id = at.tag_id
                GROUP BY t.id
                ORDER BY cnt DESC, t.name
                LIMIT {limit}
            """)
            return {row['name']: row['cnt'] for row in cur.fetchall()}

    def get_related_tags(self, selected_tags: List[str], limit: int = 50) -> Dict[str, int]:
        """Возвращает теги, которые встречаются вместе с указанными (и их количество)."""
        if not selected_tags:
            return self.get_all_tags(limit=limit)
            
        with self.get_connection() as conn:
            cur = conn.cursor()
            placeholders = ','.join('?' for _ in selected_tags)
            cur.execute(f"""
                SELECT t.name, COUNT(at.asset_id) as cnt
                FROM tags t
                JOIN asset_tags at ON t.id = at.tag_id
                WHERE at.asset_id IN (
                    SELECT at2.asset_id 
                    FROM asset_tags at2
                    JOIN tags t2 ON t2.id = at2.tag_id
                    WHERE t2.name IN ({placeholders})
                    GROUP BY at2.asset_id
                    HAVING COUNT(DISTINCT t2.name) = ?
                )
                AND t.name NOT IN ({placeholders})
                GROUP BY t.id
                ORDER BY cnt DESC, t.name
                LIMIT {limit}
            """, (*selected_tags, len(selected_tags), *selected_tags))
            return {row['name']: row['cnt'] for row in cur.fetchall()}

    def get_contextual_suggestions(self, selected_tags: List[str], search_text: str = "", limit: int = 20) -> Dict[str, int]:
        """Умные подсказки тегов на основе контекста и ввода."""
        search_text = search_text.lower().strip()
        
        # Список тегов, которые не несут смысловой нагрузки или слишком общие
        generic_blacklist = [
            'projects', 'selected projects', 'built projects', 
            'metaverse', 'technology', 'sustainability', 
            'materials', 'all projects'
        ]
        
        if not selected_tags and not search_text:
            # Root level: Prefer main categories and curated benchmarks
            priority = ['топ', 'top', 'exterior', 'interior', 'architecture', 'render', 'furniture']
            
            # Explicitly fetch priority tag counts if they exist in DB
            priority_tags = {}
            with self.get_connection() as conn:
                cur = conn.cursor()
                p_placeholders = ','.join('?' for _ in priority)
                cur.execute(f"""
                    SELECT t.name, COUNT(at.asset_id) as cnt
                    FROM tags t
                    JOIN asset_tags at ON t.id = at.tag_id
                    WHERE t.name IN ({p_placeholders})
                    GROUP BY t.id
                """, priority)
                priority_tags = {row['name']: row['cnt'] for row in cur.fetchall()}

            all_tags = self.get_all_tags(limit=limit * 2)
            all_tags.update(priority_tags)
            
            # Filter out blacklisted tags
            filtered_tags = {k: v for k, v in all_tags.items() if k not in generic_blacklist}
            
            sorted_tags = sorted(
                filtered_tags.items(), 
                key=lambda x: (x[0] not in priority, priority.index(x[0]) if x[0] in priority else 0, -x[1])
            )
            return dict(sorted_tags[:limit])
            
        if search_text:
            # Filtering existing tags by prefix/substring
            with self.get_connection() as conn:
                cur = conn.cursor()
                # If tags are selected, search only among related tags
                if selected_tags:
                    placeholders = ','.join('?' for _ in selected_tags)
                    cur.execute(f"""
                        SELECT t.name, COUNT(at.asset_id) as cnt
                        FROM tags t
                        JOIN asset_tags at ON t.id = at.tag_id
                        WHERE at.asset_id IN (
                            SELECT at2.asset_id FROM asset_tags at2
                            JOIN tags t2 ON t2.id = at2.tag_id
                            WHERE t2.name IN ({placeholders})
                            GROUP BY at2.asset_id
                            HAVING COUNT(DISTINCT t2.name) = ?
                        )
                        AND t.name LIKE ?
                        AND t.name NOT IN ({placeholders})
                        AND t.name NOT IN ({','.join('?' for _ in generic_blacklist)})
                        GROUP BY t.id
                        ORDER BY cnt DESC
                        LIMIT {limit}
                    """, (*selected_tags, len(selected_tags), f"%{search_text}%", *selected_tags, *generic_blacklist))
                else:
                    cur.execute(f"""
                        SELECT t.name, COUNT(at.asset_id) as cnt
                        FROM tags t
                        LEFT JOIN asset_tags at ON t.id = at.tag_id
                        WHERE t.name LIKE ?
                        AND t.name NOT IN ({','.join('?' for _ in generic_blacklist)})
                        GROUP BY t.id
                        ORDER BY cnt DESC
                        LIMIT {limit}
                    """, (f"%{search_text}%", *generic_blacklist))
                return {row['name']: row['cnt'] for row in cur.fetchall()}
        
        related = self.get_related_tags(selected_tags, limit=limit + len(generic_blacklist))
        filtered = {k: v for k, v in related.items() if k not in generic_blacklist}
        return dict(list(filtered.items())[:limit])

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

    def reset_all_embeddings(self) -> int:
        """Resets all embedding_id to NULL so assets can be re-indexed with a new model."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE assets SET embedding_id = NULL")
            conn.commit()
            return cur.rowcount

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
        """Удаляет записи ассетов, у которых нет локального файла на диске.

        Returns:
            (deleted_count, deleted_ids) — количество удалённых и список их ID
        """
        deleted_ids = []
        deleted_count = 0

        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, local_path, thumbnail_path, image_type FROM assets")
            rows = cur.fetchall()

            import os
            for row in rows:
                asset_id = row['id']
                local_path = row['local_path']
                thumb_path = row['thumbnail_path']
                img_type = row['image_type'] or 'Photography'

                is_missing = False
                # Для локальных файлов проверяем оригинал
                if img_type == "Local" and local_path:
                    # Используем os.path.exists, он работает значительно быстрее Path().exists() в циклах
                    if not os.path.exists(local_path):
                        is_missing = True
                # Для веб-файлов или если нет локального пути, проверяем превью в кэше
                elif thumb_path:
                    if not os.path.exists(thumb_path):
                        is_missing = True
                elif local_path: # fallback
                    if not os.path.exists(local_path):
                        is_missing = True

                if is_missing:
                    deleted_ids.append(asset_id)
                    # Удаляем связанные теги
                    conn.execute("DELETE FROM asset_tags WHERE asset_id = ?", (asset_id,))
                    # Удаляем ассет
                    conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
                    deleted_count += 1

            conn.commit()

        # Чистим неиспользуемые теги
        if deleted_count > 0:
            with self.get_connection() as conn:
                conn.execute("DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM asset_tags)")
                conn.commit()

        logger.info(f"Cleanup: deleted {deleted_count} assets with missing files")
        return deleted_count, deleted_ids

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

    def get_all_asset_ids(self) -> set[int]:
        """Возвращает сет всех ID ассетов, существующих в базе."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM assets")
            return {row['id'] for row in cur.fetchall()}

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

    # === Description operations ===
    
    def set_description(self, asset_id: int, description: str):
        """Устанавливает текстовое описание (ИИ) для ассета."""
        with self.get_connection() as conn:
            conn.execute("UPDATE assets SET description = ? WHERE id = ?", (description, asset_id))
            conn.commit()

    def get_description(self, asset_id: int) -> str:
        """Возвращает текстовое описание ассета."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT description FROM assets WHERE id = ?", (asset_id,))
            row = cur.fetchone()
            return row['description'] if row else ""
