"""Unit tests for Refer MCP search engine, metadata matching, and visibility compliance."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from database.db_manager import DatabaseManager
from database.search_repository import SearchRepository
from database.source_group_store import SourceGroupStore
from database.visibility_store import VisibilityStore
from mcp_server.search_engine import execute_search


class McpSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="refer_test_mcp_search_"))
        self.db_path = self.temp_dir / "test_search.db"
        self.hidden_path = self.temp_dir / "hidden.json"
        self.groups_path = self.temp_dir / "source_groups.json"

        self.db_mgr = DatabaseManager(self.db_path)
        with self.db_mgr.get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE,
                    domain TEXT
                );
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    url TEXT UNIQUE,
                    author TEXT
                );
                CREATE TABLE IF NOT EXISTS tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE
                );
                CREATE TABLE IF NOT EXISTS assets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    original_url TEXT,
                    local_path TEXT,
                    thumbnail_path TEXT,
                    phash TEXT,
                    width INTEGER DEFAULT 1920,
                    height INTEGER DEFAULT 1080,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    source_id INTEGER,
                    project_id INTEGER,
                    embedding_id INTEGER,
                    category TEXT DEFAULT 'photography',
                    image_type TEXT DEFAULT 'Photography',
                    is_favorite INTEGER DEFAULT 0,
                    description TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS asset_tags (
                    asset_id INTEGER,
                    tag_id INTEGER,
                    PRIMARY KEY (asset_id, tag_id)
                );
            """)

            # Seed test sources
            conn.execute("INSERT INTO sources (id, domain, url) VALUES (1, 'archdaily.com', 'https://archdaily.com')")
            conn.execute("INSERT INTO sources (id, domain, url) VALUES (2, 'behance.net', 'https://behance.net')")

            # Seed test projects
            conn.execute("INSERT INTO projects (id, title, author, url) VALUES (1, 'Villa Kloof', 'SAOTA', 'https://archdaily.com/1')")
            conn.execute("INSERT INTO projects (id, title, author, url) VALUES (2, 'Nordic Cabin', 'Other Studio', 'https://archdaily.com/2')")

            # Seed test assets
            conn.execute("""
                INSERT INTO assets (id, project_id, source_id, thumbnail_path, local_path, description)
                VALUES (1, 1, 1, 'thumb1.webp', 'photo1.jpg', 'Luxury concrete villa on cliffside')
            """)
            conn.execute("""
                INSERT INTO assets (id, project_id, source_id, thumbnail_path, local_path, description)
                VALUES (2, 2, 1, 'thumb2.webp', 'photo2.jpg', 'Timber cabin in dense forest')
            """)
            conn.commit()

        self.visibility = VisibilityStore(self.hidden_path)
        self.group_store = SourceGroupStore(store_path=self.groups_path, db=self.db_mgr)
        self.search_repo = SearchRepository(
            db=self.db_mgr,
            faiss_manager=None,
            assignments=self.group_store.get_source_assignments(),
            visibility_store=self.visibility
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_metadata_search_matches_query(self):
        """P1.1: execute_search with query='SAOTA' matches project 1, query='NO_SUCH_STUDIO' returns empty."""
        results_saota = execute_search(
            query="SAOTA",
            mode="metadata",
            search_repo=self.search_repo,
            group_store=self.group_store,
            visibility_store=self.visibility
        )
        self.assertEqual(len(results_saota), 1)
        self.assertEqual(results_saota[0]["asset_id"], 1)
        self.assertEqual(results_saota[0]["architect"], "SAOTA")

        results_none = execute_search(
            query="NO_SUCH_STUDIO",
            mode="metadata",
            search_repo=self.search_repo,
            group_store=self.group_store,
            visibility_store=self.visibility
        )
        self.assertEqual(len(results_none), 0)

    def test_metadata_search_matches_description_and_title(self):
        """Metadata search matches text across title and description."""
        res_villa = execute_search(
            query="villa",
            mode="metadata",
            search_repo=self.search_repo,
            group_store=self.group_store,
            visibility_store=self.visibility
        )
        self.assertEqual(len(res_villa), 1)
        self.assertEqual(res_villa[0]["asset_id"], 1)

        res_cabin = execute_search(
            query="cabin",
            mode="metadata",
            search_repo=self.search_repo,
            group_store=self.group_store,
            visibility_store=self.visibility
        )
        self.assertEqual(len(res_cabin), 1)
        self.assertEqual(res_cabin[0]["asset_id"], 2)

    def test_visibility_store_hidden_assets_excluded(self):
        """P1.4: Hidden assets are never returned by search."""
        # Hide asset #1
        self.visibility.hide([{"id": 1, "original_url": "", "local_path": "photo1.jpg", "thumbnail_path": "thumb1.webp", "phash": ""}])

        results = execute_search(
            query="",
            mode="metadata",
            search_repo=self.search_repo,
            group_store=self.group_store,
            visibility_store=self.visibility
        )
        ids = [r["asset_id"] for r in results]
        self.assertNotIn(1, ids)
        self.assertIn(2, ids)

    def test_disabled_source_excluded(self):
        """P1.4: Administratively disabled sources are excluded."""
        self.group_store.set_source_disabled("archdaily.com", True)

        results = execute_search(
            query="",
            mode="metadata",
            search_repo=self.search_repo,
            group_store=self.group_store,
            visibility_store=self.visibility
        )
        self.assertEqual(len(results), 0)

    def test_semantic_search_without_ai_raises_error(self):
        """P1.1: Semantic search when AI is unavailable raises RuntimeError rather than returning random assets."""
        import mcp_server.search_engine as se
        orig_ai = se._ai_client
        se._ai_client = None
        # Mock get_ai_client to return None
        try:
            se.get_ai_client = lambda: None
            with self.assertRaises(RuntimeError):
                execute_search(
                    query="luxury villa",
                    mode="semantic",
                    search_repo=self.search_repo,
                    group_store=self.group_store,
                    visibility_store=self.visibility
                )
        finally:
            se._ai_client = orig_ai
            se.get_ai_client = lambda: orig_ai


if __name__ == "__main__":
    unittest.main()
