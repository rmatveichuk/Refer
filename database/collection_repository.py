from __future__ import annotations

import logging
import sqlite3
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path

from database.models import Asset

logger = logging.getLogger(__name__)


def normalize_name_key(name: str) -> str:
    """Canonical Unicode normalization for duplicate detection (e.g. 'ЖК Река' == 'жк река')."""
    return unicodedata.normalize("NFKC", (name or "").strip()).casefold()


@dataclass(frozen=True)
class BoardItemSnapshot:
    id: int
    local_path: str
    thumbnail_path: str
    title: str
    author: str
    is_cover: bool
    original_url: str = ""
    project_url: str = ""
    project_location: str = ""
    source_domain: str = ""
    width: int = 0
    height: int = 0


@dataclass(frozen=True)
class BoardSnapshot:
    id: int
    name: str
    description: str
    export_dir: str
    items: Tuple[BoardItemSnapshot, ...]


class CollectionRepository:
    """Репозиторий для управления наборами (мудбордами) референсов."""

    def __init__(self, db_manager):
        self.db = db_manager
        self._ensure_tables()

    def _ensure_tables(self):
        try:
            with self.db.get_connection() as conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS collections (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        name_key TEXT NOT NULL UNIQUE,
                        description TEXT DEFAULT '',
                        cover_asset_id INTEGER DEFAULT NULL,
                        export_dir TEXT DEFAULT '',
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    );

                    CREATE TABLE IF NOT EXISTS collection_assets (
                        collection_id INTEGER NOT NULL,
                        asset_id INTEGER NOT NULL,
                        position INTEGER NOT NULL DEFAULT 0,
                        is_cover INTEGER NOT NULL DEFAULT 0,
                        added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (collection_id, asset_id)
                    );

                    CREATE TABLE IF NOT EXISTS quick_target (
                        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                        collection_id INTEGER DEFAULT NULL
                    );

                    INSERT OR IGNORE INTO quick_target (singleton, collection_id) VALUES (1, NULL);
                """)
                conn.commit()
        except Exception as e:
            logger.warning(f"Could not auto-ensure collection tables: {e}")

    def create_collection(self, name: str, description: str = "", ids: List[int] = ()) -> int:
        clean_name = (name or "").strip()
        if not clean_name or len(clean_name) > 120:
            raise ValueError("Название набора должно содержать от 1 до 120 символов.")

        name_key = normalize_name_key(clean_name)

        with self.db.get_connection() as conn:
            # Check duplicate name_key
            existing = conn.execute("SELECT id FROM collections WHERE name_key = ?", (name_key,)).fetchone()
            if existing:
                raise ValueError(f"Набор с названием '{clean_name}' уже существует.")

            cur = conn.execute(
                """INSERT INTO collections (name, name_key, description)
                   VALUES (?, ?, ?)""",
                (clean_name, name_key, description.strip())
            )
            collection_id = cur.lastrowid

            if ids:
                self._add_assets_conn(conn, collection_id, ids)

            # Auto-assign Quick Target if none is set
            target = conn.execute("SELECT collection_id FROM quick_target WHERE singleton = 1").fetchone()
            if not target or target[0] is None:
                conn.execute("UPDATE quick_target SET collection_id = ? WHERE singleton = 1", (collection_id,))

            conn.commit()
            return collection_id

    def rename_collection(self, collection_id: int, new_name: str) -> None:
        clean_name = (new_name or "").strip()
        if not clean_name or len(clean_name) > 120:
            raise ValueError("Название набора должно содержать от 1 до 120 символов.")

        name_key = normalize_name_key(clean_name)

        with self.db.get_connection() as conn:
            existing = conn.execute("SELECT id FROM collections WHERE name_key = ? AND id != ?", (name_key, collection_id)).fetchone()
            if existing:
                raise ValueError(f"Набор с названием '{clean_name}' уже существует.")

            conn.execute(
                "UPDATE collections SET name = ?, name_key = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (clean_name, name_key, collection_id)
            )
            conn.commit()

    def update_description(self, collection_id: int, description: str) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE collections SET description = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (description.strip(), collection_id)
            )
            conn.commit()

    def delete_collection(self, collection_id: int) -> None:
        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
            conn.execute("UPDATE quick_target SET collection_id = NULL WHERE collection_id = ?", (collection_id,))
            conn.commit()

    def _add_assets_conn(self, conn: sqlite3.Connection, collection_id: int, asset_ids: List[int]) -> List[int]:
        if not asset_ids:
            return []

        # Find current max position
        max_pos_row = conn.execute(
            "SELECT COALESCE(MAX(position), -1) as pos FROM collection_assets WHERE collection_id = ?",
            (collection_id,)
        ).fetchone()
        pos = max_pos_row["pos"] + 1

        added_ids = []
        unique_ids = list(dict.fromkeys(asset_ids))

        for aid in unique_ids:
            cur = conn.execute(
                """INSERT OR IGNORE INTO collection_assets (collection_id, asset_id, position)
                   VALUES (?, ?, ?)""",
                (collection_id, aid, pos)
            )
            if cur.rowcount > 0:
                added_ids.append(aid)
                pos += 1

        if added_ids:
            # If collection has no cover, pick the first added asset as default cover
            cover_check = conn.execute("SELECT cover_asset_id FROM collections WHERE id = ?", (collection_id,)).fetchone()
            if not cover_check or not cover_check["cover_asset_id"]:
                first_id = added_ids[0]
                conn.execute("UPDATE collections SET cover_asset_id = ? WHERE id = ?", (first_id, collection_id))
                conn.execute("UPDATE collection_assets SET is_cover = 1 WHERE collection_id = ? AND asset_id = ?", (collection_id, first_id))

            conn.execute("UPDATE collections SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (collection_id,))

        return added_ids

    def add_assets(self, collection_id: int, asset_ids: List[int]) -> List[int]:
        """Добавляет ассеты в набор с дедупликацией. Возвращает список фактически добавленных ID."""
        with self.db.get_connection() as conn:
            added = self._add_assets_conn(conn, collection_id, asset_ids)
            conn.commit()
            return added

    def remove_assets(self, collection_id: int, asset_ids: List[int]) -> None:
        if not asset_ids:
            return
        with self.db.get_connection() as conn:
            placeholders = ",".join("?" for _ in asset_ids)
            conn.execute(
                f"DELETE FROM collection_assets WHERE collection_id = ? AND asset_id IN ({placeholders})",
                [collection_id] + asset_ids
            )
            # Check if cover was removed
            col = conn.execute("SELECT cover_asset_id FROM collections WHERE id = ?", (collection_id,)).fetchone()
            if col and col["cover_asset_id"] in asset_ids:
                # Pick next available asset as cover
                next_item = conn.execute(
                    "SELECT asset_id FROM collection_assets WHERE collection_id = ? ORDER BY position LIMIT 1",
                    (collection_id,)
                ).fetchone()
                new_cover = next_item["asset_id"] if next_item else None
                conn.execute("UPDATE collections SET cover_asset_id = ? WHERE id = ?", (new_cover, collection_id))
                if new_cover:
                    conn.execute("UPDATE collection_assets SET is_cover = 1 WHERE collection_id = ? AND asset_id = ?", (collection_id, new_cover))

            conn.execute("UPDATE collections SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (collection_id,))
            conn.commit()

    def set_cover(self, collection_id: int, asset_id: Optional[int]) -> None:
        with self.db.get_connection() as conn:
            if asset_id is not None:
                # Verify membership
                member = conn.execute(
                    "SELECT 1 FROM collection_assets WHERE collection_id = ? AND asset_id = ?",
                    (collection_id, asset_id)
                ).fetchone()
                if not member:
                    raise ValueError("Изображение для обложки должно входить в этот набор.")

            conn.execute("UPDATE collection_assets SET is_cover = 0 WHERE collection_id = ?", (collection_id,))
            if asset_id is not None:
                conn.execute("UPDATE collection_assets SET is_cover = 1 WHERE collection_id = ? AND asset_id = ?", (collection_id, asset_id))

            conn.execute("UPDATE collections SET cover_asset_id = ? WHERE id = ?", (asset_id, collection_id))
            conn.commit()

    def reorder_assets(self, collection_id: int, asset_ids: List[int]) -> bool:
        with self.db.get_connection() as conn:
            for pos, aid in enumerate(asset_ids):
                conn.execute(
                    "UPDATE collection_assets SET position = ? WHERE collection_id = ? AND asset_id = ?",
                    (pos, collection_id, aid)
                )
            conn.execute("UPDATE collections SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (collection_id,))
            conn.commit()
            return True

    def get_quick_target(self) -> Optional[int]:
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT collection_id FROM quick_target WHERE singleton = 1").fetchone()
            return row["collection_id"] if row else None

    def get_quick_target_record(self) -> Optional[Dict[str, Any]]:
        target_id = self.get_quick_target()
        if target_id is None:
            return None
        return self.get_collection(target_id)

    def quick_add(self, asset_ids: List[int]) -> List[int]:
        target_id = self.get_quick_target()
        if target_id is None:
            raise ValueError("Быстрый набор не назначен.")
        return self.add_assets(target_id, asset_ids)

    def set_quick_target(self, collection_id: Optional[int]) -> None:
        with self.db.get_connection() as conn:
            conn.execute("UPDATE quick_target SET collection_id = ? WHERE singleton = 1", (collection_id,))
            conn.commit()

    def remember_export_dir(self, collection_id: int, export_dir: str) -> None:
        with self.db.get_connection() as conn:
            conn.execute("UPDATE collections SET export_dir = ? WHERE id = ?", (str(export_dir), collection_id))
            conn.commit()

    def get_collections_with_counts(self) -> List[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            target_id = self.get_quick_target()
            rows = conn.execute("""
                SELECT 
                    c.id,
                    c.name,
                    c.description,
                    c.cover_asset_id,
                    c.export_dir,
                    c.created_at,
                    c.updated_at,
                    COUNT(ca.asset_id) AS asset_count,
                    COALESCE(
                        (SELECT a.thumbnail_path FROM assets a WHERE a.id = c.cover_asset_id),
                        (SELECT a2.thumbnail_path FROM collection_assets ca2 JOIN assets a2 ON a2.id = ca2.asset_id WHERE ca2.collection_id = c.id ORDER BY ca2.position LIMIT 1)
                    ) AS cover_thumbnail_path
                FROM collections c
                LEFT JOIN collection_assets ca ON c.id = ca.collection_id
                GROUP BY c.id
                ORDER BY c.updated_at DESC, c.id DESC
            """).fetchall()

            result = []
            for r in rows:
                item = dict(r)
                item["is_quick_target"] = (item["id"] == target_id)
                result.append(item)
            return result

    def get_collection(self, collection_id: int) -> Optional[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()
            return dict(row) if row else None

    def get_collection_assets(self, collection_id: int) -> List[Asset]:
        """Возвращает упорядоченный список объектов Asset для отображения в галерее."""
        with self.db.get_connection() as conn:
            rows = conn.execute("""
                SELECT a.*, p.title as project_title, p.author as project_author, s.domain as source_domain
                FROM collection_assets ca
                JOIN assets a ON ca.asset_id = a.id
                LEFT JOIN projects p ON a.project_id = p.id
                LEFT JOIN sources s ON a.source_id = s.id
                WHERE ca.collection_id = ?
                ORDER BY ca.position ASC
            """, (collection_id,)).fetchall()

            assets = []
            for r in rows:
                asset = Asset(
                    id=r["id"],
                    original_url=r["original_url"],
                    local_path=r["local_path"],
                    thumbnail_path=r["thumbnail_path"],
                    phash=r["phash"],
                    width=r["width"],
                    height=r["height"],
                    created_at=r["created_at"],
                    source_id=r["source_id"],
                    project_id=r["project_id"],
                    embedding_id=r["embedding_id"],
                    category=r["category"],
                    image_type=r["image_type"] if "image_type" in r.keys() else "Photography",
                    is_favorite=bool(r["is_favorite"]),
                    description=r["description"] if "description" in r.keys() else ""
                )
                assets.append(asset)
            return assets

    def snapshot(self, collection_id: int) -> BoardSnapshot:
        """Снимок состава набора для безопасного экспорта."""
        with self.db.get_connection() as conn:
            col_row = conn.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()
            if not col_row:
                raise ValueError("Набор не найден.")

            rows = conn.execute("""
                SELECT 
                    a.id, a.local_path, a.thumbnail_path, a.original_url, a.width, a.height,
                    COALESCE(p.title, '') AS project_title,
                    COALESCE(p.author, '') AS project_author,
                    COALESCE(p.url, '') AS project_url,
                    COALESCE(p.location, '') AS project_location,
                    COALESCE(s.domain, '') AS source_domain,
                    ca.is_cover
                FROM collection_assets ca
                JOIN assets a ON a.id = ca.asset_id
                LEFT JOIN projects p ON p.id = a.project_id
                LEFT JOIN sources s ON s.id = a.source_id
                WHERE ca.collection_id = ?
                ORDER BY ca.position ASC
            """, (collection_id,)).fetchall()

            items = []
            for r in rows:
                items.append(BoardItemSnapshot(
                    id=r["id"],
                    local_path=r["local_path"] or "",
                    thumbnail_path=r["thumbnail_path"] or "",
                    title=r["project_title"],
                    author=r["project_author"],
                    is_cover=bool(r["is_cover"]),
                    original_url=r["original_url"] or "",
                    project_url=r["project_url"] or "",
                    project_location=r["project_location"] or "",
                    source_domain=r["source_domain"] or "",
                    width=r["width"] or 0,
                    height=r["height"] or 0
                ))

            return BoardSnapshot(
                id=col_row["id"],
                name=col_row["name"],
                description=col_row["description"] or "",
                export_dir=col_row["export_dir"] or "",
                items=tuple(items)
            )
