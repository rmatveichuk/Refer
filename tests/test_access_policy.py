"""Unit tests for the unified access and visibility policy.

Verifies consistent enforcement of:
- Hidden assets (VisibilityStore)
- Disabled website domains (SourceGroupStore)
- Disabled parent directories (recursive folder exclusion)
- Disabled specific subfolders
- Separation of reasons ("hidden" vs "source_disabled")
- Integration with inspect_asset_crop and search_engine
"""
import shutil
import tempfile
import unittest
from pathlib import Path
from PIL import Image

from database.db_manager import DatabaseManager
from database.source_group_store import SourceGroupStore
from database.visibility_store import VisibilityStore
from mcp_server.access_policy import (
    is_source_or_path_disabled,
    check_asset_access,
    is_asset_accessible,
)
from mcp_server.crop_inspector import inspect_asset_crop


class AccessPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="refer_test_access_"))
        self.db_path = self.temp_dir / "test_access.db"
        self.hidden_path = self.temp_dir / "hidden.json"
        self.groups_path = self.temp_dir / "source_groups.json"

        # Create dummy image
        self.img_path = self.temp_dir / "sample.jpg"
        Image.new("RGB", (100, 100), color=(200, 200, 200)).save(self.img_path)

        self.db_mgr = DatabaseManager(self.db_path)
        with self.db_mgr.get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sources (id INTEGER PRIMARY KEY, url TEXT, domain TEXT);
                CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY, title TEXT, url TEXT, author TEXT);
                CREATE TABLE IF NOT EXISTS assets (
                    id INTEGER PRIMARY KEY,
                    original_url TEXT,
                    local_path TEXT,
                    thumbnail_path TEXT,
                    phash TEXT,
                    width INTEGER,
                    height INTEGER,
                    source_id INTEGER,
                    project_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    image_type TEXT,
                    description TEXT
                );
            """)
            conn.execute("INSERT INTO sources (id, domain, url) VALUES (1, 'archdaily.com', 'https://archdaily.com')")
            conn.execute("INSERT INTO sources (id, domain, url) VALUES (2, 'behance.net', 'https://behance.net')")
            conn.execute("INSERT INTO projects (id, title, author) VALUES (1, 'Test Project', 'Tester')")

            # 1: archdaily asset
            conn.execute("""
                INSERT INTO assets (id, project_id, source_id, local_path, thumbnail_path, phash, width, height)
                VALUES (1, 1, 1, ?, ?, 'h1', 100, 100)
            """, (str(self.img_path), str(self.img_path)))

            # 2: local parent catalog asset (C:/Refs/Villa/img1.jpg)
            conn.execute("""
                INSERT INTO assets (id, project_id, source_id, local_path, thumbnail_path, phash, width, height)
                VALUES (2, 1, NULL, 'C:/Refs/Villa/img1.jpg', ?, 'h2', 100, 100)
            """, (str(self.img_path),))

            # 3: local subfolder asset (C:/Refs/Interiors/living.jpg)
            conn.execute("""
                INSERT INTO assets (id, project_id, source_id, local_path, thumbnail_path, phash, width, height)
                VALUES (3, 1, NULL, 'C:/Refs/Interiors/living.jpg', ?, 'h3', 100, 100)
            """, (str(self.img_path),))

            # 4: local sibling subfolder asset (C:/Refs/Exteriors/facade.jpg)
            conn.execute("""
                INSERT INTO assets (id, project_id, source_id, local_path, thumbnail_path, phash, width, height)
                VALUES (4, 1, NULL, 'C:/Refs/Exteriors/facade.jpg', ?, 'h4', 100, 100)
            """, (str(self.img_path),))

            conn.commit()

        self.visibility = VisibilityStore(self.hidden_path)
        self.group_store = SourceGroupStore(store_path=self.groups_path, db=self.db_mgr)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_is_source_or_path_disabled_rules(self):
        """Verifies exact domain match, exact folder match, and recursive parent folder prefix match."""
        disabled = ["archdaily.com", "c:/refs/interiors", "d:/archived_projects"]

        # Exact domain
        self.assertTrue(is_source_or_path_disabled("archdaily.com", disabled))
        self.assertTrue(is_source_or_path_disabled("ARCHDAILY.COM", disabled))
        self.assertFalse(is_source_or_path_disabled("behance.net", disabled))

        # Nested folder under disabled subfolder
        self.assertTrue(is_source_or_path_disabled("c:/refs/interiors/living.jpg", disabled))
        self.assertTrue(is_source_or_path_disabled("C:\\Refs\\Interiors\\kitchen\\shot1.png", disabled))

        # Sibling folder not disabled
        self.assertFalse(is_source_or_path_disabled("c:/refs/exteriors/facade.jpg", disabled))

        # Nested folder under disabled parent catalog
        self.assertTrue(is_source_or_path_disabled("D:/Archived_Projects/2022/Villa/plan.jpg", disabled))
        self.assertFalse(is_source_or_path_disabled("D:/Active_Projects/Villa/plan.jpg", disabled))

    def test_check_asset_access_separation_of_reasons(self):
        """Separates 'hidden' from 'source_disabled' reasons cleanly."""
        # 1. Normal asset: accessible
        row1 = {"id": 1, "source_domain": "archdaily.com", "local_path": str(self.img_path), "thumbnail_path": str(self.img_path), "phash": "h1"}
        acc, reason = check_asset_access(row1, self.visibility, self.group_store)
        self.assertTrue(acc)
        self.assertIsNone(reason)

        # 2. Hide asset #1 individually
        self.visibility.hide([{"id": 1, "original_url": "", "local_path": str(self.img_path), "thumbnail_path": str(self.img_path), "phash": "h1"}])
        acc, reason = check_asset_access(row1, self.visibility, self.group_store)
        self.assertFalse(acc)
        self.assertEqual(reason, "hidden")

        # 3. Disable archdaily.com
        self.visibility.restore([1])
        self.group_store.set_source_disabled("archdaily.com", True)
        acc, reason = check_asset_access(row1, self.visibility, self.group_store)
        self.assertFalse(acc)
        self.assertEqual(reason, "source_disabled")

    def test_parent_catalog_and_subfolder_disabling(self):
        """Parent directory disabling recursively disables all child files, subfolder disabling is selective."""
        row_villa = {"id": 2, "source_domain": "", "local_path": "C:/Refs/Villa/img1.jpg"}
        row_int = {"id": 3, "source_domain": "", "local_path": "C:/Refs/Interiors/living.jpg"}
        row_ext = {"id": 4, "source_domain": "", "local_path": "C:/Refs/Exteriors/facade.jpg"}

        # Disable only Interiors subfolder
        self.group_store.set_source_disabled("C:/Refs/Interiors", True)
        self.assertFalse(is_asset_accessible(row_int, self.visibility, self.group_store))
        self.assertTrue(is_asset_accessible(row_ext, self.visibility, self.group_store))
        self.assertTrue(is_asset_accessible(row_villa, self.visibility, self.group_store))

        # Now disable the entire parent C:/Refs
        self.group_store.set_source_disabled("C:/Refs", True)
        self.assertFalse(is_asset_accessible(row_int, self.visibility, self.group_store))
        self.assertFalse(is_asset_accessible(row_ext, self.visibility, self.group_store))
        self.assertFalse(is_asset_accessible(row_villa, self.visibility, self.group_store))

    def test_inspect_asset_crop_enforces_access_policy(self):
        """inspect_asset_crop raises explicit ValueError when asset is hidden or source is disabled."""
        # 1. Normal access succeeds
        bytes_data, meta = inspect_asset_crop(
            asset_id=1,
            db=self.db_mgr,
            visibility_store=self.visibility,
            group_store=self.group_store
        )
        self.assertGreater(len(bytes_data), 0)

        # 2. Hide asset #1 -> raises ValueError citing visibility policy
        self.visibility.hide([{"id": 1, "original_url": "", "local_path": str(self.img_path), "thumbnail_path": str(self.img_path), "phash": "h1"}])
        with self.assertRaises(ValueError) as ctx:
            inspect_asset_crop(
                asset_id=1,
                db=self.db_mgr,
                visibility_store=self.visibility,
                group_store=self.group_store
            )
        self.assertIn("hidden by library visibility policy", str(ctx.exception))

        # 3. Restore, then disable source -> raises ValueError citing disabled source
        self.visibility.restore([1])
        self.group_store.set_source_disabled("archdaily.com", True)
        with self.assertRaises(ValueError) as ctx:
            inspect_asset_crop(
                asset_id=1,
                db=self.db_mgr,
                visibility_store=self.visibility,
                group_store=self.group_store
            )
        self.assertIn("disabled source or directory", str(ctx.exception))

    def test_image_asset_id_rejected_if_source_disabled_or_hidden(self):
        """execute_search with image_asset_id raises explicit ValueError when asset is hidden or source is disabled."""
        from database.search_repository import SearchRepository
        from mcp_server.search_engine import execute_search

        search_repo = SearchRepository(
            db=self.db_mgr,
            faiss_manager=None,
            assignments=self.group_store.get_source_assignments(),
            visibility_store=self.visibility
        )

        # 1. Hide asset #1 -> execute_search with image_asset_id=1 raises ValueError
        self.visibility.hide([{"id": 1, "original_url": "", "local_path": str(self.img_path), "thumbnail_path": str(self.img_path), "phash": "h1"}])
        with self.assertRaises(ValueError) as ctx:
            execute_search(
                image_asset_id=1,
                search_repo=search_repo,
                group_store=self.group_store,
                visibility_store=self.visibility
            )
        self.assertIn("hidden by library visibility policy", str(ctx.exception))

        # 2. Restore, disable archdaily.com -> execute_search raises ValueError
        self.visibility.restore([1])
        self.group_store.set_source_disabled("archdaily.com", True)
        with self.assertRaises(ValueError) as ctx:
            execute_search(
                image_asset_id=1,
                search_repo=search_repo,
                group_store=self.group_store,
                visibility_store=self.visibility
            )
        self.assertIn("disabled source or directory", str(ctx.exception))

    def test_search_excludes_nested_disabled_folder_recursively(self):
        """Search candidates filter out assets under disabled parent directory or specific subfolder."""
        from database.search_repository import SearchRepository, SearchFilters

        search_repo = SearchRepository(
            db=self.db_mgr,
            faiss_manager=None,
            assignments=self.group_store.get_source_assignments(),
            visibility_store=self.visibility
        )

        # Disable only C:/Refs/Interiors
        self.group_store.set_source_disabled("C:/Refs/Interiors", True)
        filters = SearchFilters(
            sources=("C:/Refs",),
            excluded_sources=tuple(self.group_store.get_disabled_sources())
        )
        candidates = search_repo.candidates(filters)
        candidate_ids = [c["id"] for c in candidates]

        # Asset #3 is under C:/Refs/Interiors -> excluded
        self.assertNotIn(3, candidate_ids)
        # Asset #4 is under C:/Refs/Exteriors -> included
        self.assertIn(4, candidate_ids)
        # Asset #2 is under C:/Refs/Villa -> included
        self.assertIn(2, candidate_ids)

        # Now disable the entire parent C:/Refs
        self.group_store.set_source_disabled("C:/Refs", True)
        filters_all = SearchFilters(
            sources=("C:/Refs",),
            excluded_sources=tuple(self.group_store.get_disabled_sources())
        )
        candidates_all = search_repo.candidates(filters_all)
        candidate_ids_all = [c["id"] for c in candidates_all]

        self.assertNotIn(3, candidate_ids_all)
        self.assertNotIn(4, candidate_ids_all)
        self.assertNotIn(2, candidate_ids_all)


if __name__ == "__main__":
    unittest.main()
