"""Unit tests for Collections (Moodboards) subsystem and export engine."""
import os
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from database.db_manager import DatabaseManager
from database.collection_repository import CollectionRepository
from database.search_repository import SearchRepository, SearchFilters
from export.moodboard_exporter import export_moodboard


class TestCollections(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db = DatabaseManager(db_path=self.db_path)
        self.repo = CollectionRepository(self.db)
        self.search_repo = SearchRepository(self.db)

        # Create sample assets
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            for i in range(1, 11):
                cur.execute("""
                    INSERT INTO assets (id, original_url, local_path, thumbnail_path, phash, width, height)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (i, f"https://example.com/{i}.jpg", f"C:/test/img_{i}.jpg", f"C:/test/thumb_{i}.webp", f"phash_{i}", 1920, 1080))
            conn.commit()

    def tearDown(self):
        try:
            shutil.rmtree(self.temp_dir)
        except OSError:
            pass

    def test_collection_crud(self):
        # 1. Create collection
        col_id = self.repo.create_collection("Villa Moderna", description="Modern villas reference board")
        self.assertGreater(col_id, 0)

        # 2. Get collection
        col = self.repo.get_collection(col_id)
        self.assertIsNotNone(col)
        self.assertEqual(col["name"], "Villa Moderna")
        self.assertEqual(col["description"], "Modern villas reference board")

        # 3. Duplicate prevention (case/unicode insensitive)
        with self.assertRaises(ValueError):
            self.repo.create_collection("villa moderna")

        # 4. Rename collection
        self.repo.rename_collection(col_id, "Villa Minimalist")
        renamed = self.repo.get_collection(col_id)
        self.assertEqual(renamed["name"], "Villa Minimalist")

        # 5. List collections with counts
        cols = self.repo.get_collections_with_counts()
        self.assertEqual(len(cols), 1)
        self.assertEqual(cols[0]["name"], "Villa Minimalist")
        self.assertEqual(cols[0]["asset_count"], 0)

    def test_collection_assets_and_order(self):
        col_id = self.repo.create_collection("Concrete Details")

        # Add assets 1, 2, 3
        added = self.repo.add_assets(col_id, [1, 2, 3])
        self.assertEqual(len(added), 3)

        # Add duplicate assets (should not duplicate)
        added_dup = self.repo.add_assets(col_id, [2, 3, 4])
        self.assertEqual(len(added_dup), 1)

        # Check asset count and list
        assets = self.repo.get_collection_assets(col_id)
        self.assertEqual(len(assets), 4)
        self.assertEqual([a.id for a in assets], [1, 2, 3, 4])

        # Check collection record has updated count
        cols = self.repo.get_collections_with_counts()
        self.assertEqual(cols[0]["asset_count"], 4)

        # Remove asset 2
        self.repo.remove_assets(col_id, [2])
        assets_after = self.repo.get_collection_assets(col_id)
        self.assertEqual([a.id for a in assets_after], [1, 3, 4])

        # Reorder assets: put 4 first
        self.assertTrue(self.repo.reorder_assets(col_id, [4, 1, 3]))
        assets_reordered = self.repo.get_collection_assets(col_id)
        self.assertEqual([a.id for a in assets_reordered], [4, 1, 3])

    def test_quick_target(self):
        c1 = self.repo.create_collection("Project Alpha")
        c2 = self.repo.create_collection("Project Beta")

        # Set c1 as quick target
        self.repo.set_quick_target(c1)
        self.assertEqual(self.repo.get_quick_target(), c1)

        target = self.repo.get_quick_target_record()
        self.assertIsNotNone(target)
        self.assertEqual(target["id"], c1)
        self.assertEqual(target["name"], "Project Alpha")

        # Quick add
        added = self.repo.quick_add([5, 6])
        self.assertEqual(len(added), 2)
        self.assertEqual(len(self.repo.get_collection_assets(c1)), 2)

        # Switch to c2
        self.repo.set_quick_target(c2)
        self.assertEqual(self.repo.get_quick_target(), c2)

        # Clear target
        self.repo.set_quick_target(None)
        self.assertIsNone(self.repo.get_quick_target())

    def test_search_filtering_by_collection(self):
        col_id = self.repo.create_collection("Curated Selection")
        self.repo.add_assets(col_id, [2, 5, 8])

        # Search with filter
        filters = SearchFilters(sources=("C:/test",), collection_id=col_id)
        results = self.search_repo.candidates(filters)

        result_ids = [row["id"] for row in results]
        self.assertEqual(set(result_ids), {2, 5, 8})

    def test_moodboard_export(self):
        col_id = self.repo.create_collection("Export Board")

        # Create 2 mock image files on disk
        img1 = Path(self.temp_dir) / "photo1.jpg"
        img1.write_bytes(b"\xFF\xD8\xFF\xE0" + b"\x00" * 100)
        img2 = Path(self.temp_dir) / "photo2.jpg"
        img2.write_bytes(b"\xFF\xD8\xFF\xE0" + b"\x00" * 200)

        # Point assets 1 and 2 to these files in the DB
        with self.db.get_connection() as conn:
            conn.execute("UPDATE assets SET local_path = ? WHERE id = 1", (str(img1),))
            conn.execute("UPDATE assets SET local_path = ? WHERE id = 2", (str(img2),))
            conn.commit()

        self.repo.add_assets(col_id, [1, 2])

        export_dir = Path(self.temp_dir) / "exported_boards"
        snapshot = self.repo.snapshot(col_id)

        # Test folder export
        result = export_moodboard(
            board=snapshot,
            target_dir=export_dir,
            format="folder",
            mode="copy"
        )

        self.assertEqual(result["count"], 2)
        self.assertTrue(Path(result["directory"]).exists())
        self.assertTrue((Path(result["directory"]) / "manifest.json").exists())

        # Read manifest
        with open(Path(result["directory"]) / "manifest.json", "r", encoding="utf-8") as f:
            manifest = json.load(f)
            self.assertEqual(manifest["board_name"], "Export Board")
            self.assertEqual(len(manifest["items"]), 2)

        # Test HTML export
        html_result = export_moodboard(
            board=snapshot,
            target_dir=export_dir,
            format="html",
            mode="copy"
        )

        self.assertEqual(html_result["count"], 2)
        self.assertTrue(Path(html_result["directory"]).exists())
        self.assertTrue(Path(html_result["entrypoint"]).exists())
        self.assertTrue((Path(html_result["directory"]) / "index.html").exists())

        # Verify HTML contents
        index_html = Path(html_result["entrypoint"]).read_text(encoding="utf-8")
        self.assertIn("Export Board", index_html)
        self.assertIn("Референсы", index_html)


if __name__ == "__main__":
    unittest.main()
