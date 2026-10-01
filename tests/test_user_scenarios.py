"""User workflows on isolated SQLite data and a real, small FAISS index."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_search as fixtures
import numpy as np
from PyQt6.QtWidgets import QApplication

from database.search_repository import SearchFilters, source_section
from database.visibility_store import VisibilityStore
from ui.workers.search_worker import SearchWorker, embedding_key


class SearchScenarioTests(unittest.TestCase):
    def setUp(self):
        fixtures.RepositoryTests.setUp(self)

    def tearDown(self):
        fixtures.RepositoryTests.tearDown(self)

    def test_semantic_pages_keep_order_and_ignore_orphan_index_entries(self):
        ids = list(range(100, 1105))
        self.db.conn.executemany("INSERT INTO assets(id,source_id) VALUES (?,1)", [(aid,) for aid in ids])
        angles = np.linspace(0.1, 1.0, len(ids), dtype=np.float32)
        vectors = np.column_stack((np.cos(angles), np.sin(angles), np.zeros(len(ids))))
        self.faiss.add_vectors_batch(ids, vectors)
        self.faiss.add_vectors_batch([99999], [[1, 0, 0]])  # No corresponding SQLite image.
        filters = SearchFilters(sources=("archdaily",))
        pages = [self.repo.search(filters, vector=np.array([1, 0, 0]), limit=limit)
                 for limit in (400, 800, 1200)]
        first, second, last = [[asset.id for asset in assets] for assets, _ in pages]
        self.assertEqual((len(first), len(second), len(last)), (400, 800, 1008))
        self.assertEqual(first, second[:400])
        self.assertEqual(second, last[:800])
        self.assertEqual(len(set(last)), len(last))
        self.assertNotIn(99999, last)
        self.assertTrue(all(total is None for _, total in pages))

    def test_literal_metadata_match_precedes_vectors_but_obeys_exclusion(self):
        self.db.conn.execute("INSERT INTO assets(id,source_id,description) VALUES(8,1,'Дом среди сосен')")
        assets, _ = self.repo.search(SearchFilters(sources=("archdaily",), exclude_tags=("wood",)),
                                     text="ДОМ СРЕДИ СОСЕН", vector=np.array([1, 0, 0]))
        self.assertEqual([asset.id for asset in assets], [8, 3])

    def test_duplicate_tags_do_not_require_duplicate_matches_and_unknown_tags_match_nothing(self):
        self.db.conn.execute("INSERT INTO asset_tags VALUES(1,1)")
        assets, _ = self.repo.search(SearchFilters(sources=("archdaily",), tags=("wood", "wood", "concrete")))
        self.assertEqual([asset.id for asset in assets], [1])
        for mode in ("any", "all"):
            with self.subTest(mode=mode):
                assets, total = self.repo.search(SearchFilters(sources=("archdaily",),
                                                               tags=("not-a-library-tag",), tag_match=mode))
                self.assertEqual((assets, total), ([], 0))

    def test_metadata_query_with_sql_punctuation_is_literal(self):
        title = "%_ ' OR 1=1 --"
        self.db.conn.execute("UPDATE projects SET title=? WHERE id=1", (title,))
        assets, total = self.repo.search(SearchFilters(sources=("archdaily",)), text=title, metadata_only=True)
        self.assertEqual(([asset.id for asset in assets], total), ([1], 1))
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 7)

    def test_section_override_uses_longest_folder_and_path_boundary(self):
        assignments = {r"D:\library": "references", r"D:\library\models": "models"}
        self.assertEqual(source_section(r"D:\library\models\chair.jpg", assignments), "models")
        self.assertEqual(source_section(r"D:\library\models-extra\photo.jpg", assignments), "references")
        self.assertEqual(source_section(r"D:\library-old\photo.jpg", assignments), "unassigned")


class WindowScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        fixtures.WindowTests.setUp(self)
        self.temp = tempfile.TemporaryDirectory(prefix="refer-scenario-tests-")
        self.window.visibility = VisibilityStore(Path(self.temp.name) / "hidden_assets.json")

    def tearDown(self):
        fixtures.WindowTests.tearDown(self)
        self.temp.cleanup()

    def search(self, text):
        fixtures.WindowTests.search(self, text)

    def test_sidebar_filters_keep_gallery_space_and_search_state(self):
        self.window.resize(1200, 850)
        self.window.show()
        self.app.processEvents()
        before = self.window.gallery.viewport().height()
        self.panel.set_selected_tags(['wood', 'concrete'])
        self.panel.tag_match.setCurrentIndex(self.panel.tag_match.findData('all'))
        self.panel._emit_search()
        self.window.filters_button.click()
        self.app.processEvents()
        self.assertEqual(self.window.gallery.viewport().height(), before)
        self.assertTrue(self.panel.tags_widget.isVisible())
        self.assertTrue(self.panel.filters_widget.isVisible())
        self.assertTrue(self.panel.library_widget.isAncestorOf(self.panel.tags_widget))
        self.assertTrue(self.window.top_toolbar.isVisible())
        self.assertLessEqual(self.window.top_toolbar.height(), 40)
        self.assertTrue(self.window.status_label.isVisible())
        self.assertEqual([asset.id for asset in self.window.gallery_model.assets], [1])

    def test_catalog_menu_routes_hidden_actions_and_retains_web_import_controls(self):
        added, hidden = [], []
        toolbar = self.window.top_toolbar
        toolbar.add_folder_requested.disconnect()
        toolbar.hidden_assets_requested.disconnect()
        toolbar.add_folder_requested.connect(lambda: added.append(True))
        toolbar.hidden_assets_requested.connect(lambda: hidden.append(True))
        actions = {action.text(): action for action in self.window.catalog_menu.actions()}
        actions['Добавить папку…'].trigger()
        actions['Скрытые изображения…'].trigger()
        self.assertEqual((added, hidden), ([True], [True]))
        actions['Добавить с сайта…'].trigger()
        dialog = toolbar._import_dialog
        self.assertTrue(dialog.isVisible())
        toolbar.url_input.setText('https://example.test/project')
        toolbar.set_scraping_state(True)
        dialog.hide()
        actions['Добавить с сайта…'].trigger()
        self.assertEqual(toolbar.url_input.text(), 'https://example.test/project')
        self.assertTrue(toolbar.is_scraping)
        self.assertFalse(toolbar.url_input.isEnabled())
        stopped = []
        toolbar.scrape_stopped.disconnect()
        toolbar.scrape_stopped.connect(lambda: stopped.append(True))
        toolbar.btn_scrape.click()
        self.assertEqual(stopped, [True])
        dialog.close()

    def test_failed_old_query_does_not_cancel_or_erase_new_query(self):
        self.search("старый")
        old = self.window.active_searcher
        self.search("новый")
        self.window._on_embedding_error(old.key, "failure of old request")
        latest = self.window.active_searcher
        self.assertEqual(latest.text, "новый")
        latest.run()
        self.assertTrue(self.window.gallery_model.assets)
        self.assertEqual(self.window._embedding_cache[0][0], "новый")
        self.assertNotIn("failure of old request", self.window.status_label.text())

    def test_switch_to_metadata_ignores_pending_semantic_result(self):
        self.search("сосны")
        old = self.window.active_searcher
        self.panel.text_mode.setCurrentIndex(1)
        self.search("БЮРО ТЕСТ")
        self.assertEqual([asset.id for asset in self.window.gallery_model.assets], [1])
        old.run()
        self.assertEqual([asset.id for asset in self.window.gallery_model.assets], [1])
        self.assertEqual(self.window.library_table.model().rowCount(), 1)
        self.assertEqual(self.panel.hybrid_input.text_input.text(), "БЮРО ТЕСТ")

    def test_browse_more_stops_at_end_and_new_query_resets_page(self):
        self.db.conn.executemany("INSERT INTO assets(id,source_id) VALUES (?,1)",
                                 [(aid,) for aid in range(100, 1105)])
        self.panel.text_mode.setCurrentIndex(1)
        self.search("")
        self.assertEqual(len(self.window.gallery_model.assets), 400)
        self.assertFalse(self.window.more_button.isHidden())
        self.window._show_more_results()
        self.assertEqual(len(self.window.gallery_model.assets), 800)
        self.window._show_more_results()
        self.assertEqual(len(self.window.gallery_model.assets), 1012)
        self.assertTrue(self.window.more_button.isHidden())
        sources = self.panel.get_selected_sources()
        self.search("БЮРО ТЕСТ")
        self.assertEqual(self.window.result_limit, 400)
        self.assertEqual([asset.id for asset in self.window.gallery_model.assets], [1])
        self.assertEqual(self.panel.get_selected_sources(), sources)

    def test_truncation_warning_keeps_original_query_and_clear_removes_warning(self):
        text = "бетонный дом среди сосен " * 20
        self.search(text)
        worker = self.window.active_searcher
        info = {"truncated": True, "token_count": 120, "token_limit": 64, "effective_text": "бетонный дом"}
        self.window._on_embedding_result(worker.key, np.array([1, 0, 0]), info)
        self.assertEqual(self.panel.hybrid_input.text_input.text(), text)
        self.assertFalse(self.panel.query_warning.isHidden())
        self.assertIn("64", self.panel.query_warning.text())
        self.assertIn("бетонный дом", self.panel.query_warning.toolTip())
        self.panel._clear_all()
        self.assertTrue(self.panel.query_warning.isHidden())

    def test_table_hide_passes_original_local_path(self):
        self.panel.update_custom_folders([r"D:\3d models"])
        self.panel.text_mode.setCurrentIndex(1)
        self.search("chair.jpg")
        self.window.library_table.setCurrentIndex(self.window.library_table.model().index(0, 0))
        with patch.object(self.window, "_delete_assets_batch") as hide:
            self.window._delete_selected_asset()
        asset = hide.call_args.args[0][0]
        self.assertEqual(asset.id, 4)
        self.assertEqual(asset.local_path, r"D:\3d models\chairs\chair.jpg")

    def test_reset_button_clears_filters_and_preserves_query_and_section(self):
        self.panel.text_mode.setCurrentIndex(1)
        self.panel.top_check.setChecked(True)
        self.panel.set_selected_tags(['wood'])
        self.panel.exclude_input.setText('concrete')
        self.panel.favorite_check.setChecked(True)
        self.search('БЮРО ТЕСТ')
        self.window.reset_filters_button.click()
        self.assertEqual(self.panel.hybrid_input.text_input.text(), 'БЮРО ТЕСТ')
        self.assertFalse(self.panel.top_check.isChecked())
        self.assertEqual(self.panel.selected_tags, [])
        self.assertEqual(self.panel.excluded_tags(), ())
        self.assertFalse(self.panel.favorite_check.isChecked())
        self.assertEqual([asset.id for asset in self.window.gallery_model.assets], [1])

    def test_slow_source_filtering_keeps_gui_responsive_and_only_latest_selection_is_applied(self):
        from threading import Event
        from time import monotonic
        from types import SimpleNamespace
        from PyQt6.QtCore import QTimer
        from PyQt6.QtTest import QTest
        from database.search_repository import SearchRepository
        from ui.workers.results_worker import ReadOnlyDatabase
        import sqlite3

        path = Path(self.temp.name) / 'collection.db'
        connection = sqlite3.connect(path)
        self.db.conn.backup(connection)
        connection.close()
        readonly = ReadOnlyDatabase(path)
        self.window.db = SimpleNamespace(db_path=path, get_connection=readonly.get_connection)
        self.window._start_results_worker = lambda worker: self.window._results_pool.start(worker)
        self.panel.text_mode.setCurrentIndex(1)
        entered, release = Event(), Event()
        calls, ticks = [], []
        original = SearchRepository.search

        def delayed(repository, filters, **kwargs):
            calls.append(filters.tags)
            if len(calls) == 1:
                entered.set()
                if not release.wait(3):
                    raise RuntimeError('Test did not release the read worker')
            return original(repository, filters, **kwargs)

        timer = QTimer()
        timer.setInterval(5)
        timer.timeout.connect(lambda: ticks.append(True))
        timer.start()
        try:
            with patch.object(SearchRepository, 'search', delayed):
                self.search('')
                deadline = monotonic() + 2
                while not entered.is_set() and monotonic() < deadline:
                    QTest.qWait(10)
                self.assertTrue(entered.is_set())
                QTest.qWait(60)
                self.assertGreaterEqual(len(ticks), 3)
                self.panel.set_selected_tags(['wood'])
                self.search('')
                self.panel.set_selected_tags(['concrete'])
                self.search('')
                self.assertEqual(len(calls), 1)
                release.set()
                deadline = monotonic() + 3
                while self.window._active_results_worker is not None and monotonic() < deadline:
                    QTest.qWait(10)
                self.assertIsNone(self.window._active_results_worker)
                self.assertEqual(calls, [(), ('concrete',)])
                self.assertEqual({asset.id for asset in self.window.gallery_model.assets}, {1, 3})
        finally:
            release.set()
            self.window._results_pool.waitForDone(3000)
            self.app.processEvents()
            timer.stop()

    def test_old_result_error_does_not_clear_new_selection(self):
        pending = []
        self.window._start_results_worker = pending.append
        self.panel.text_mode.setCurrentIndex(1)
        self.search('')
        old = pending[-1]
        self.panel.set_selected_tags(['wood'])
        self.search('')
        self.window._on_results_error(old.revision, 'old read failed')
        latest = pending[-1]
        latest.run()
        self.assertEqual({asset.id for asset in self.window.gallery_model.assets}, {1, 2})
        self.assertNotIn('old read failed', self.window.status_label.text())

    def test_separate_tags_button_lists_tags_and_applies_selected_filter(self):
        from ui.widgets.tag_manager import TagManagerDialog
        from PyQt6.QtWidgets import QDialog
        self.db.get_contextual_suggestions = lambda selected, text='', limit=100: {'wood': 2, 'concrete': 2}
        self.window.show()
        self.app.processEvents()
        self.assertTrue(self.panel.btn_manage_tags.isVisible())
        self.assertTrue(self.panel.filters_widget.isHidden())
        self.assertNotIn('(0)', self.panel.btn_manage_tags.text())

        def choose(dialog):
            self.assertEqual(dialog.suggestions_layout.count(), 2)
            chip = next(dialog.suggestions_layout.itemAt(index).widget()
                        for index in range(dialog.suggestions_layout.count())
                        if dialog.suggestions_layout.itemAt(index).widget().tag_name == 'wood')
            chip.click()
            return QDialog.DialogCode.Accepted

        with patch.object(TagManagerDialog, 'exec', choose):
            self.panel.btn_manage_tags.click()
        self.assertEqual(self.panel.selected_tags, ['wood'])
        self.assertEqual({asset.id for asset in self.window.gallery_model.assets}, {1, 2})
        self.assertIn('1', self.panel.btn_manage_tags.text())

    def test_catalog_apply_disables_images_in_all_sections_and_reenable_restores_them(self):
        from ui.catalog_dialog import CatalogDialog
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QDialog
        self.panel.update_custom_folders([r'D:\3d models', r'D:\3d collections\Maxtree'])
        self.panel.text_mode.setCurrentIndex(1)
        self.search('')
        self.assertEqual({asset.id for asset in self.window.gallery_model.assets}, set(range(1, 8)))

        def disable(dialog):
            item = dialog.find_item_by_path(r'D:\3d models')
            item.setCheckState(0, Qt.CheckState.Unchecked)
            dialog._on_accept()
            return QDialog.DialogCode.Accepted

        with patch.object(CatalogDialog, 'exec', disable):
            self.window._open_catalogs()
        self.assertEqual({asset.id for asset in self.window.gallery_model.assets}, {1, 2, 3, 5})
        self.assertTrue(self.window.group_store.is_source_disabled(r'D:\3d models'))
        self.assertTrue(r'D:\3d models' not in self.panel.folder_items or self.panel.folder_items[r'D:\3d models'].isHidden())
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM assets').fetchone()[0], 7)

        def enable(dialog):
            item = dialog.find_item_by_path(r'D:\3d models')
            item.setCheckState(0, Qt.CheckState.Checked)
            dialog._on_accept()
            return QDialog.DialogCode.Accepted

        with patch.object(CatalogDialog, 'exec', enable):
            self.window._open_catalogs()
        self.assertEqual({asset.id for asset in self.window.gallery_model.assets}, set(range(1, 8)))
        self.assertFalse(self.window.group_store.is_source_disabled(r'D:\3d models'))
        self.assertIn(r'D:\3d models', self.panel.folder_items)
        self.assertFalse(self.panel.folder_items[r'D:\3d models'].isHidden())

    def test_disabling_all_children_hides_empty_parent_but_keeps_its_own_images(self):
        parent, child = r'D:\3d collections', r'D:\3d collections\Maxtree'
        self.panel.group_store.set_source_disabled("archdaily.com", True)
        self.panel.group_store.set_source_disabled(r"D:\3d models", True)
        self.panel.group_store.set_source_disabled(child, True)
        self.panel.update_custom_folders([parent, child], direct_folders=[child])
        self.search('')
        self.assertTrue(self.panel.folder_items[parent].isHidden())
        self.assertEqual(self.window.gallery_model.assets, [])
        self.db.conn.execute('INSERT INTO assets(id,source_id,local_path) VALUES(8,3,?)', (parent + r'\own.jpg',))
        self.panel.update_custom_folders([parent, child], direct_folders=[parent, child])
        self.search('')
        self.assertFalse(self.panel.folder_items[parent].isHidden())
        self.assertTrue(child not in self.panel.folder_items or self.panel.folder_items[child].isHidden())
        self.assertEqual([asset.id for asset in self.window.gallery_model.assets], [8])


class ImageSearchScenarioTests(unittest.TestCase):
    def test_image_only_does_not_call_text_encoder(self):
        ai = fixtures.FakeAI()
        outputs = []
        worker = SearchWorker(ai, "", "preview.jpg")
        worker.signals.result.connect(lambda _key, vector, info: outputs.append((vector, info)))
        with patch.object(ai, "get_text_embedding", side_effect=AssertionError("text encoder called")), \
             patch.object(ai, "get_text_query_info", side_effect=AssertionError("tokenizer called")):
            worker.run()
        self.assertEqual(len(outputs), 1)
        vector, info = outputs[0]
        np.testing.assert_allclose(vector, np.array([0.8, 0.4, 0.2]) / np.linalg.norm([0.8, 0.4, 0.2]), rtol=1e-6)
        self.assertEqual(info, {})

    def test_replacing_image_at_same_path_invalidates_embedding_cache_key(self):
        with tempfile.TemporaryDirectory(prefix="refer-query-image-tests-") as directory:
            path = Path(directory) / "preview.jpg"
            path.write_bytes(b"first")
            first = embedding_key("", str(path))
            path.write_bytes(b"replacement image with a different size")
            self.assertNotEqual(first, embedding_key("", str(path)))

    def test_invalid_image_or_empty_query_emits_error_without_result(self):
        for image in (np.zeros(3), np.ones(2), np.array([np.nan, 0, 1]), None):
            with self.subTest(image=image):
                ai = fixtures.FakeAI()
                ai.get_image_embedding = lambda _path: image
                worker = SearchWorker(ai, "", "preview.jpg" if image is not None else "")
                outputs, errors = [], []
                worker.signals.result.connect(lambda *args: outputs.append(args))
                worker.signals.error.connect(lambda _key, error: errors.append(error))
                worker.run()
                self.assertEqual(outputs, [])
                self.assertEqual(len(errors), 1)


if __name__ == "__main__":
    unittest.main()
