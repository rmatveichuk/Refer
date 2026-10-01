"""Reversible visibility and read-only diagnostics on isolated library data."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import test_search as fixtures
from database.visibility_store import VisibilityStore
from database.search_repository import SearchRepository, SearchFilters
from database.availability import inspect_files, file_path
from ui.hidden_assets_dialog import HiddenAssetsDialog
from PyQt6.QtWidgets import QMessageBox, QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest


class VisibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="refer-visibility-tests-")
        self.store = VisibilityStore(Path(self.temp.name) / "hidden_assets.json")
        self.db = fixtures.FixtureDB()
        self.db.conn.execute("UPDATE assets SET original_url='https://example.com/image.jpg' WHERE id=1")

    def tearDown(self):
        self.db.conn.close()
        self.temp.cleanup()

    def rows(self, ids=(1,)):
        return [dict(row) for row in self.db.conn.execute("SELECT * FROM assets WHERE id IN (%s)" % ",".join("?" for _ in ids), ids)]

    def test_hide_and_restore_preserve_database_and_filter_all_search_modes(self):
        before = list(self.db.conn.iterdump())
        self.store.hide(self.rows())
        repo = SearchRepository(self.db, None, visibility_store=self.store)
        for filters in [SearchFilters(sources=("archdaily",)), SearchFilters(sources=("archdaily",), top_only=True),
                        SearchFilters(sources=("archdaily",), favorites=True), SearchFilters(sources=("archdaily",), tags=("wood",))]:
            self.assertNotIn(1, {row["id"] for row in repo.candidates(filters)})
        assets, _ = repo.search(SearchFilters(sources=("archdaily",)), text="БЮРО ТЕСТ", metadata_only=True)
        self.assertEqual(assets, [])
        self.assertEqual(list(self.db.conn.iterdump()), before)
        self.store.restore([1])
        self.assertIn(1, {row["id"] for row in repo.candidates(SearchFilters(sources=("archdaily",), top_only=True))})
        self.assertEqual(list(self.db.conn.iterdump()), before)

    def test_write_error_retains_previous_state_and_removes_temp_file(self):
        self.store.hide(self.rows())
        before = self.store.path.read_bytes()
        with patch("database.visibility_store.os.replace", side_effect=OSError("fixture disk failure")):
            with self.assertRaises(RuntimeError):
                self.store.hide(self.rows((2,)))
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(list(self.store.path.parent.glob("*.tmp")), [])

    def test_corrupt_state_is_reported_and_not_silently_reset(self):
        self.store.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.store.entries()
        with self.assertRaises(RuntimeError):
            self.store.hide(self.rows())
        self.assertEqual(self.store.path.read_text(encoding="utf-8"), "{broken")

    def test_reused_id_of_another_image_is_visible(self):
        row = self.rows()[0]
        self.store.hide([row])
        reloaded = VisibilityStore(self.store.path)
        self.assertTrue(reloaded.is_hidden(row))
        row["original_url"] = "https://example.com/another.jpg"
        self.assertFalse(reloaded.is_hidden(row))


class VisibilityWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        fixtures.WindowTests.setUp(self)
        self.visibility_temp = tempfile.TemporaryDirectory(prefix="refer-hidden-ui-tests-")
        self.window.visibility = VisibilityStore(Path(self.visibility_temp.name) / "hidden_assets.json")

    def tearDown(self):
        fixtures.WindowTests.tearDown(self)
        self.visibility_temp.cleanup()

    def test_hiding_selected_image_does_not_touch_index_or_rows_and_restores(self):
        shared = Path(self.visibility_temp.name) / "shared.webp"
        shared.write_bytes(b"fixture original; preserve both references")
        self.db.conn.execute("UPDATE assets SET thumbnail_path=? WHERE id IN (1,2)", (str(shared),))
        before = list(self.db.conn.iterdump())
        ids = self.window.faiss_mgr.get_all_ids().tolist()
        asset = next(asset for asset in self.window.gallery_model.assets if asset.id == 1)
        with patch("ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.assertTrue(self.window._delete_assets_batch([asset]))
        self.panel._clear_all()
        self.panel.reset_filters()
        self.assertNotIn(1, {asset.id for asset in self.window.gallery_model.assets})
        dialog = HiddenAssetsDialog(self.db, self.window.visibility, self.window)
        self.assertEqual(dialog.available_ids, [1])
        self.window._restore_hidden_assets([1])
        self.assertIn(1, {asset.id for asset in self.window.gallery_model.assets})
        self.assertEqual(self.window.faiss_mgr.get_all_ids().tolist(), ids)
        self.assertEqual(list(self.db.conn.iterdump()), before)
        self.assertEqual(shared.read_bytes(), b"fixture original; preserve both references")

    def test_failed_hide_has_no_success_and_does_not_change_gallery(self):
        before = {asset.id for asset in self.window.gallery_model.assets}
        with patch("ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes), \
             patch.object(self.window.visibility, "hide", side_effect=RuntimeError("fixture write failure")), \
             patch("ui.main_window.QMessageBox.critical"):
            self.assertFalse(self.window._delete_assets_batch(self.window.gallery_model.assets[:1]))
        self.assertEqual({asset.id for asset in self.window.gallery_model.assets}, before)

    def test_restoring_image_does_not_enable_its_disabled_source(self):
        self.window.visibility.hide([dict(self.db.conn.execute("SELECT * FROM assets WHERE id=4").fetchone())])
        self.panel.group_store.set_source_disabled("archdaily.com", True)
        self.panel.group_store.set_source_disabled(r"D:\3d collections\Maxtree", True)
        self.panel.group_store.set_source_disabled(r"D:\3d models", True)
        self.panel.update_custom_folders([r"D:\3d models"])
        self.window._restore_hidden_assets([4])
        self.assertEqual(self.window.gallery_model.assets, [])
        self.assertTrue(self.panel.group_store.is_source_disabled(r"D:\3d models"))

    def test_availability_error_ends_progress(self):
        self.window._cleanup_missing_files()
        self.assertIsNotNone(self.window._availability_worker)
        self.window._on_availability_error("fixture read error")
        self.assertTrue(self.window.progress_bar.isHidden())
        self.assertIsNone(self.window._availability_worker)

    def test_second_check_click_requests_cancellation(self):
        self.window._cleanup_missing_files()
        worker = self.window._availability_worker
        self.window._cleanup_missing_files()
        self.assertTrue(worker.cancellation.is_set())
        self.assertIs(self.window._availability_worker, worker)

    def test_hidden_image_is_excluded_from_semantic_ranking(self):
        self.window.visibility.hide([dict(self.db.conn.execute("SELECT * FROM assets WHERE id=1").fetchone())])
        fixtures.WindowTests.search(self, "сосны")
        self.window.active_searcher.run()
        self.assertNotIn(1, {asset.id for asset in self.window.gallery_model.assets})
        self.assertTrue(self.window.gallery_model.assets)

    def test_hidden_preview_is_owned_by_its_dialog_and_does_not_offer_hide_again(self):
        self.db.is_favorite = lambda aid: False
        self.db.get_description = lambda aid: ""
        self.window.visibility.hide([dict(self.db.conn.execute("SELECT * FROM assets WHERE id=1").fetchone())])
        dialog = HiddenAssetsDialog(self.db, self.window.visibility, self.window)
        dialog._open_image(dialog.table.item(0, 0))
        self.assertIs(dialog._viewer.parent(), dialog)
        self.assertFalse(dialog._viewer.btn_delete.isEnabled())
        dialog._viewer.activateWindow()
        self.app.processEvents()
        with patch.object(self.window, '_delete_assets_batch') as hide:
            QTest.keyClick(dialog._viewer, Qt.Key.Key_Delete)
            self.app.processEvents()
            hide.assert_not_called()
        self.assertEqual(len(dialog._viewer.assets), 1)
        dialog._viewer.close()
        dialog.close()

    def test_viewer_prefers_original_and_clears_previous_image_on_missing_file(self):
        from database.models import Asset
        from PyQt6.QtGui import QImage
        from ui.widgets.image_viewer import ImageViewerWindow
        original = Path(self.visibility_temp.name) / "original image.png"
        thumb = Path(self.visibility_temp.name) / "thumb.png"
        for path, width in [(original, 500), (thumb, 100)]:
            image = QImage(width, 100, QImage.Format.Format_RGB32)
            image.fill(0)
            self.assertTrue(image.save(str(path)))
        viewer = ImageViewerWindow()
        viewer.set_assets([Asset(id=1, original_url=None, local_path=original.as_uri(), thumbnail_path=str(thumb)),
                           Asset(id=2, local_path=str(original.parent / "missing.png"), thumbnail_path=None)], 0)
        self.assertEqual(viewer.viewer._pixmap_item.pixmap().width(), 500)
        viewer.show_next()
        self.assertIsNone(viewer.viewer._pixmap_item)
        self.assertIn("недоступно", viewer.status.currentMessage())
        viewer.close()


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="refer-availability-tests-")
        self.root = Path(self.temp.name)
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("CREATE TABLE sources(id INTEGER, domain TEXT); CREATE TABLE assets(id INTEGER, local_path TEXT, thumbnail_path TEXT, image_type TEXT, source_id INTEGER);")
        self.conn.execute("INSERT INTO sources VALUES(1,?)", (str(self.root),))

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def get_connection(self):
        return self.conn

    def test_missing_file_in_available_source_and_offline_source_are_distinct(self):
        self.conn.execute("INSERT INTO sources VALUES(2,?)", (str(self.root / "offline"),))
        self.conn.executemany("INSERT INTO assets VALUES(?,?,?,?,?)", [(1,str(self.root / "missing.jpg"),"","Local",1), (2,str(self.root / "offline" / "file.jpg"),"","Local",2)])
        before = list(self.conn.iterdump())
        report = inspect_files(self)
        self.assertEqual([row["status"] for row in report.issues], ["file_missing", "source_unavailable"])
        self.assertEqual(list(self.conn.iterdump()), before)

    def test_permission_error_and_missing_preview_with_original_do_not_remove_rows(self):
        original = self.root / "original.jpg"
        original.write_bytes(b"fixture")
        self.conn.executemany("INSERT INTO assets VALUES(?,?,?,?,?)", [(1,str(original),"","Local",1), (2,str(original),str(self.root / "missing.webp"),"Photography",None)])
        real_stat = os.stat
        def stat_path(path, *args, **kwargs):
            if str(path) == str(original):
                raise PermissionError("fixture denied")
            return real_stat(path, *args, **kwargs)
        with patch("database.availability.os.stat", side_effect=stat_path):
            report = inspect_files(self)
        self.assertEqual(report.issues[0]["status"], "file_unavailable")
        report = inspect_files(self)
        self.assertEqual(report.issues[0]["status"], "preview_missing")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 2)

    def test_file_uri_decoding(self):
        self.assertEqual(file_path("file:///D:/3d%20models/chair.jpg"), "D:/3d models/chair.jpg")
        self.assertEqual(file_path("file://server/share/a.jpg"), "//server/share/a.jpg")

    def test_cancellation_returns_partial_report_and_leaves_data(self):
        from threading import Event
        cancellation = Event()
        self.conn.executemany("INSERT INTO assets VALUES(?,?,?,?,?)", [(i,"","","Photography",None) for i in range(300)])
        before = list(self.conn.iterdump())
        def progress(checked, total):
            if checked >= 250:
                cancellation.set()
        report = inspect_files(self, cancellation, progress)
        self.assertTrue(report.cancelled)
        self.assertEqual(report.checked, 250)
        self.assertEqual(report.total, 300)
        self.assertEqual(list(self.conn.iterdump()), before)
