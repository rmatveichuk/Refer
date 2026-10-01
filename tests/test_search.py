"""Run with the project's Python: python -m unittest discover -s tests -v."""
import os
import tempfile
import unittest
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_test_dir = tempfile.TemporaryDirectory(prefix="refer-search-tests-", ignore_cleanup_errors=True)
os.environ["APPDATA"] = _test_dir.name
os.environ["LOCALAPPDATA"] = _test_dir.name

import numpy as np
from PyQt6.QtWidgets import QApplication
from database.search_repository import SearchRepository, SearchFilters, source_section
from database.faiss_manager import FaissManager
from ui.main_window import MainWindow
from ui.workers.search_worker import SearchWorker
from database.source_group_store import SourceGroupStore


class FixtureDB:
    def __init__(self, *_args):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE sources(id INTEGER PRIMARY KEY, domain TEXT, url TEXT);
            CREATE TABLE projects(id INTEGER PRIMARY KEY, title TEXT, author TEXT);
            CREATE TABLE assets(id INTEGER PRIMARY KEY, source_id INTEGER, project_id INTEGER,
                local_path TEXT, thumbnail_path TEXT DEFAULT '', original_url TEXT DEFAULT '',
                phash TEXT DEFAULT '', width INTEGER DEFAULT 640, height INTEGER DEFAULT 480,
                created_at TEXT DEFAULT '2026-09-30', is_favorite INTEGER DEFAULT 0,
                description TEXT DEFAULT '', category TEXT DEFAULT 'photography', image_type TEXT DEFAULT 'Photography');
            CREATE TABLE tags(id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE asset_tags(asset_id INTEGER, tag_id INTEGER);
            INSERT INTO sources VALUES (1,'archdaily.com',''),(2,'D:\\3d models',''),(3,'D:\\3d collections\\Maxtree','');
            INSERT INTO projects VALUES(1,'Дом среди сосен','БЮРО ТЕСТ');
            INSERT INTO assets(id,source_id,project_id,local_path,is_favorite) VALUES
                (1,1,1,'',1),(2,1,NULL,'',0),(3,1,NULL,'',0),
                (4,2,NULL,'D:\\3d models\\chairs\\chair.jpg',0),
                (5,3,NULL,'D:\\3d collections\\Maxtree\\pine.jpg',0),
                (6,2,NULL,'D:\\3d models\\a_b%\\literal.jpg',0),
                (7,2,NULL,'D:\\3d models\\axbZZ\\wrong.jpg',0);
            INSERT INTO tags VALUES(1,'wood'),(2,'concrete'),(3,'топ');
            INSERT INTO asset_tags VALUES(1,1),(1,2),(1,3),(2,1),(3,2);
        """)

    def get_connection(self):
        return self.conn


class FakeAI:
    model = SimpleNamespace(config=SimpleNamespace(text_config=SimpleNamespace(hidden_size=3)))

    def get_text_query_info(self, text):
        return {"truncated": False, "token_count": 4, "token_limit": 64}

    def get_text_embedding(self, text):
        return np.array([0.2, 0.4, 0.8], dtype=np.float32)

    def get_image_embedding(self, path):
        return np.array([0.8, 0.4, 0.2], dtype=np.float32)


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.db = FixtureDB()
        self.faiss = FaissManager(Path(_test_dir.name) / "never-saved.index", dimension=3)
        vectors = np.array([[0.1, 0.3, 0.9], [0.4, 0.8, 0.1], [0.7, 0.6, 0.1]], dtype=np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        self.faiss.add_vectors_batch([1, 2, 3], vectors)
        self.repo = SearchRepository(self.db, self.faiss)

    def tearDown(self):
        self.db.conn.close()

    def ids(self, **changes):
        filters = SearchFilters(sources=("archdaily",), **changes)
        return {row["id"] for row in self.repo.candidates(filters)}

    def test_any_all_and_exclusions(self):
        self.assertEqual(self.ids(tags=("wood", "concrete"), tag_match="any"), {1, 2, 3})
        self.assertEqual(self.ids(tags=("wood", "concrete"), tag_match="all"), {1})
        self.assertEqual(self.ids(tags=("concrete",), exclude_tags=("wood",)), {3})
        self.assertEqual(self.ids(favorites=True, top_only=True), {1})

    def test_empty_source_never_becomes_all_sources(self):
        self.assertEqual(self.repo.candidates(SearchFilters()), [])

    def test_cached_web_images_keep_their_source_section(self):
        self.db.conn.execute("UPDATE assets SET local_path=? WHERE id=1", (r"C:\cache\Thumbnails\image.webp",))
        self.assertEqual(self.ids(top_only=True), {1})
        self.repo.assignments = {"archdaily": "models"}
        self.assertEqual(self.ids(section="models", top_only=True), {1})

    def test_plants_are_within_models_and_unknown_is_not_guessed(self):
        filters = SearchFilters(sources=(r"D:\3d collections", r"D:\3d models"), section="models", plants_only=True)
        self.assertEqual({row["id"] for row in self.repo.candidates(filters)}, {5})
        self.assertEqual(source_section(r"E:\Work\image.jpg"), "unassigned")
        self.assertEqual(source_section("archdaily.com", {"archdaily": "models"}), "models")

    def test_folder_wildcards_are_literal(self):
        filters = SearchFilters(sources=(r"D:\3d models\a_b%",), section="models")
        self.assertEqual({row["id"] for row in self.repo.candidates(filters)}, {6})

    def test_partial_source_keeps_parent_files_and_excludes_child(self):
        filters = SearchFilters(sources=(r"D:\3d models",), section="models",
                                excluded_sources=(r"D:\3d models\chairs",))
        self.assertEqual({row["id"] for row in self.repo.candidates(filters)}, {6, 7})

    def test_russian_metadata_without_model_and_filtered_ranking(self):
        filters = SearchFilters(sources=("archdaily",))
        assets, _ = self.repo.search(filters, text="бюро тест", metadata_only=True)
        self.assertEqual([a.id for a in assets], [1])
        vector = np.array([0.2, 0.4, 0.8], dtype=np.float32)
        assets, _ = self.repo.search(SearchFilters(sources=("archdaily",), exclude_tags=("wood",)), vector=vector)
        self.assertEqual([a.id for a in assets], [3])
        # A text/image distance above the former threshold still produces ranked results.
        self.assertGreater(np.sum((vector - self.faiss.index.index.reconstruct(2)) ** 2), 0.08317)

    def test_invalid_vectors_are_rejected(self):
        for bad in [np.zeros(3), np.array([float("nan"), 1, 0]), np.array([float("inf"), 1, 0]), np.ones(2), "invalid"]:
            with self.assertRaises(ValueError):
                self.faiss.search(bad)
        with self.assertRaises(ValueError):
            self.faiss.add_vectors_batch([8], np.zeros((1, 3)))

    def test_windows_flat_search_matches_squared_distances_across_blocks_and_filters(self):
        rng = np.random.default_rng(42)
        vectors = rng.normal(size=(2100, 3)).astype(np.float32)
        ids = np.arange(100, 2200)
        self.faiss.add_vectors_batch(ids.tolist(), vectors)
        query = np.array([0.2, 0.4, 0.8], dtype=np.float32)
        allowed = ids[::2]
        expected = np.sum((vectors[::2] - query) ** 2, axis=1)
        order = np.lexsort((allowed, expected))[:40]
        distances, found = self.faiss._search_flat_without_openmp(query, 40, allowed.tolist())
        np.testing.assert_array_equal(found, allowed[order])
        np.testing.assert_allclose(distances, expected[order], rtol=1e-5, atol=1e-6)
        distances, found = self.faiss._search_flat_without_openmp(query, 3, [100, -999])
        self.assertEqual(found.tolist(), [100, -1, -1])
        self.assertTrue(np.isinf(distances[1:]).all())

    def test_worker_normalizes_hybrid_and_reports_errors(self):
        outputs, errors = [], []
        worker = SearchWorker(FakeAI(), "дерево", "image.jpg")
        worker.signals.result.connect(lambda key, vector, info: outputs.append(vector))
        worker.signals.error.connect(lambda key, error: errors.append(error))
        worker.run()
        self.assertAlmostEqual(np.linalg.norm(outputs[0]), 1.0, places=6)
        ai = FakeAI()
        ai.get_text_embedding = lambda text: np.zeros(3)
        worker = SearchWorker(ai, "ошибка", "")
        worker.signals.error.connect(lambda key, error: errors.append(error))
        worker.run()
        self.assertTrue(errors)


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.jobs = []
        self.db = FixtureDB()
        self.pool = SimpleNamespace(start=self.jobs.append, waitForDone=lambda _timeout: True)
        self.store_dir = tempfile.TemporaryDirectory(prefix="refer-store-")
        self.store_path = Path(self.store_dir.name) / "source_groups.json"
        self.patches = [patch("ui.main_window.DatabaseManager", return_value=self.db),
                        patch("ui.main_window.SourceGroupStore", side_effect=lambda **kw: SourceGroupStore(db=self.db, store_path=self.store_path)),
                        patch.object(MainWindow, '_start_results_worker', lambda window, worker: worker.run()),
                        patch.object(MainWindow, '_start_ai_worker', lambda window, worker: self.jobs.append(worker)),
                        patch.object(MainWindow, "_background_ai_init"),
                        patch("ui.main_window.QThreadPool.globalInstance", return_value=self.pool)]
        for p in self.patches:
            p.start()
        self.window = MainWindow()
        self.window.faiss_mgr = FaissManager(Path(_test_dir.name) / "window.index", dimension=3)
        self.window.faiss_mgr.add_vectors_batch([1, 2, 3], np.eye(3, dtype=np.float32))
        self.window.ai = FakeAI()
        self.panel = self.window.search_panel
        self.jobs.clear()

    def tearDown(self):
        self.panel._search_debounce.stop()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.db.conn.close()
        for p in reversed(self.patches):
            p.stop()
        self.store_dir.cleanup()

    def search(self, text):
        self.panel.hybrid_input.text_input.setText(text)
        self.panel._emit_search()

    def test_query_clear_preserves_filters_and_discards_inflight_result(self):
        self.panel.set_selected_tags(["wood"])
        sources = self.panel.get_selected_sources()
        self.search("сосны")
        worker = self.window.active_searcher
        self.panel._clear_all()
        self.assertEqual(self.panel.selected_tags, ["wood"])
        self.assertEqual(self.panel.get_selected_sources(), sources)
        worker.run()
        self.assertIsNone(self.window._requested_search)
        self.assertEqual({a.id for a in self.window.gallery_model.assets}, {1, 2})

    def test_latest_query_wins_and_filter_change_reuses_embedding(self):
        self.search("первый")
        old = self.window.active_searcher
        self.search("второй")
        old.run()
        latest = self.window.active_searcher
        self.assertEqual(latest.text, "второй")
        latest.run()
        self.panel.set_selected_tags(["concrete"])
        self.panel._emit_search()
        self.assertIsNone(self.window.active_searcher)
        self.assertEqual({a.id for a in self.window.gallery_model.assets}, {1, 3})
        self.assertEqual(self.window._embedding_cache[0][0], "второй")

    def test_metadata_and_filter_reset_need_no_model_or_index(self):
        self.window.ai = None
        self.window.faiss_mgr = FaissManager(Path(_test_dir.name) / "empty.index", dimension=3)
        self.panel.text_mode.setCurrentIndex(1)
        self.panel.set_selected_tags(["concrete"])
        self.search("БЮРО ТЕСТ")
        self.assertEqual([a.id for a in self.window.gallery_model.assets], [1])
        self.assertEqual(self.window.library_table.model().rowCount(), 1)
        self.panel.reset_filters()
        self.assertEqual(self.panel.hybrid_input.text_input.text(), "БЮРО ТЕСТ")
        self.assertEqual(self.panel.selected_tags, [])
        self.assertIsNone(self.window.active_searcher)

    def test_empty_sources_and_view_switch_keep_query(self):
        self.panel.text_mode.setCurrentIndex(1)
        self.panel._check_all(False)
        self.search("БЮРО ТЕСТ")
        self.assertEqual(self.window.gallery_model.assets, [])
        self.panel.update_custom_folders(list(self.panel.folder_items))
        self.assertEqual(self.panel.get_selected_sources(), [])
        self.window.tabs.setCurrentIndex(1)
        self.window.tabs.setCurrentIndex(0)
        self.assertEqual(self.panel.hybrid_input.text_input.text(), "БЮРО ТЕСТ")
        self.assertEqual(self.window.tabs.count(), 2)

    def test_partial_folder_selection_in_window(self):
        from PyQt6.QtCore import Qt
        self.panel.group_store.set_source_disabled("archdaily.com", True)
        self.panel.group_store.set_source_disabled(r"D:\3d collections\Maxtree", True)
        self.panel.update_custom_folders([r"D:\3d models", r"D:\3d models\chairs"])
        self.panel.folder_items[r"D:\3d models\chairs"].setCheckState(0, Qt.CheckState.Unchecked)
        self.panel._emit_search()
        self.assertEqual({a.id for a in self.window.gallery_model.assets}, {6, 7})
        self.assertEqual(self.window.library_table.model().rowCount(), 2)

    def test_catalog_disable_preserves_data_and_survives_filter_reset(self):
        self.panel.group_store.set_source_disabled("archdaily.com", True)
        self.panel.group_store.set_source_disabled(r"D:\3d collections\Maxtree", True)
        self.panel.group_store.set_source_disabled(r"D:\3d models", True)
        self.panel.update_custom_folders([r"D:\3d models"])
        self.panel.reset_filters()
        self.assertEqual(self.window.gallery_model.assets, [])
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 7)
        self.panel.group_store.set_source_disabled(r"D:\3d models", False)
        self.panel.update_custom_folders([r"D:\3d models"])
        self.panel._emit_search()
        self.assertEqual({a.id for a in self.window.gallery_model.assets}, {4, 6, 7})

    def test_catalog_dialog_has_explicit_settings_and_no_implicit_writes(self):
        from ui.catalog_dialog import CatalogDialog, FolderImportDialog
        dialog = CatalogDialog(self.db, {}, ())
        item = dialog.find_item_by_path(r"D:\3d models")
        from PyQt6.QtCore import Qt
        item.setCheckState(0, Qt.CheckState.Unchecked)
        dialog._on_accept()
        assignments, disabled = dialog.values()
        self.assertIn(r"D:\3d models", disabled)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0], 3)
        import_dialog = FolderImportDialog()
        import_dialog.no_textures.setChecked(True)
        self.assertEqual(import_dialog.options()["mode"], "3D Models")
        self.assertTrue(import_dialog.options()["no_textures"])
        import_dialog.no_textures.setChecked(False)
        self.assertEqual(import_dialog.options()["mode"], "All")
        self.assertFalse(import_dialog.options()["no_textures"])

    def test_result_pages_are_400_and_reuse_embedding(self):
        self.search("сосны")
        self.window.active_searcher.run()
        count = len(self.jobs)
        self.assertEqual(self.window.result_limit, 400)
        self.window._show_more_results()
        self.assertEqual(self.window.result_limit, 800)
        self.assertEqual(len(self.jobs), count)


if __name__ == "__main__":
    unittest.main()
