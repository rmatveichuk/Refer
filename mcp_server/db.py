"""Database access and initialization for Refer MCP server."""
from __future__ import annotations

import sqlite3
import logging
from pathlib import Path
from typing import Optional

import config
from database.db_manager import DatabaseManager
from database.collection_repository import CollectionRepository
from database.faiss_manager import FaissManager

logger = logging.getLogger(__name__)

_db_manager: Optional[DatabaseManager] = None
_collection_repo: Optional[CollectionRepository] = None
_faiss_manager: Optional[FaissManager] = None


def get_db_manager() -> DatabaseManager:
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager(config.DB_PATH)
        ensure_feature_tables(_db_manager)
    return _db_manager


def get_collection_repository() -> CollectionRepository:
    global _collection_repo
    if _collection_repo is None:
        _collection_repo = CollectionRepository(get_db_manager())
    return _collection_repo


def get_faiss_manager() -> FaissManager:
    global _faiss_manager
    if _faiss_manager is None:
        _faiss_manager = FaissManager(config.FAISS_PATH, dimension=config.VECTOR_DIMENSION)
    return _faiss_manager


def ensure_feature_tables(db: DatabaseManager) -> None:
    """Creates the asset_features table if it doesn't already exist."""
    try:
        with db.get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS asset_features (
                    asset_id INTEGER PRIMARY KEY,
                    feature_version INTEGER NOT NULL DEFAULT 1,
                    warmth_palette REAL,          -- 0.0 (холодный) .. 1.0 (теплый)
                    global_contrast REAL,         -- std(L* / 100.0) по шкале CIE L*
                    lstar_mean REAL,              -- 0.0 .. 100.0 (средняя светлота)
                    palette_json TEXT,            -- Top-5 LAB + HEX + веса
                    status TEXT NOT NULL,         -- 'completed', 'missing_file', 'error'
                    calculated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_features_warmth ON asset_features(warmth_palette);
                CREATE INDEX IF NOT EXISTS idx_features_contrast ON asset_features(global_contrast);
                CREATE INDEX IF NOT EXISTS idx_features_status ON asset_features(status);
            """)
            conn.commit()
    except Exception as e:
        logger.warning(f"Failed to ensure asset_features table: {e}")
