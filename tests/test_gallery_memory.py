"""Thumbnail bounds and cancellation, without opening the real collection."""
import os
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_settings_dir = tempfile.TemporaryDirectory(prefix="refer-gallery-tests-", ignore_cleanup_errors=True)
# Tests may be discovered together with test_search, whose config is already isolated.
import sys
if "config" not in sys.modules:
    os.environ["APPDATA"] = _settings_dir.name
    os.environ["LOCALAPPDATA"] = _settings_dir.name

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import QApplication
from database.models import Asset
from ui.widgets.lazy_model import AssetListModel
from ui.workers.image_loader import ImageLoaderWorker


class GalleryMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def model(self, count=200, **kwargs):
        model = AssetListModel(**kwargs)
        model.setAssets([Asset(id=i, thumbnail_path=f"fixture-{i}.png") for i in range(count)])
        return model

    @staticmethod
    def pixmap():
        pixmap = QPixmap(32, 32)
        pixmap.fill(QColor("red"))
        return pixmap

    @staticmethod
    def request(model, row):
        return model.data(model.index(row, 0), Qt.ItemDataRole.DecorationRole)

    def test_large_scroll_has_bounded_active_and_pending_requests(self):
        model = self.model(count=10_000, max_active_loads=4, max_pending_loads=16)
        started = []
        model.loadRequested.connect(lambda *request: started.append(request))
        for row in range(10_000):
            self.request(model, row)
        self.assertEqual(len(started), 4)
        self.assertEqual(len(model._active), 4)
        self.assertEqual(len(model._pending), 16)
        self.assertLessEqual(len(model.loading), 20)
        model.completeImage(started[0][0], started[0][2], self.pixmap())
        self.assertEqual(started[-1][0], 9_999)
        self.assertEqual(len(model._active), 4)

    def test_reset_cancels_old_work_and_rejects_old_result_for_same_id(self):
        model = self.model(count=2, max_active_loads=1)
        started = []
        model.loadRequested.connect(lambda *request: started.append(request))
        self.request(model, 0)
        self.request(model, 1)
        old_generation = model.generation
        old_cancel = model.cancellation_for(old_generation)
        model.setAssets([Asset(id=0, thumbnail_path="new.png")])
        self.assertTrue(old_cancel.is_set())
        self.request(model, 0)
        self.assertEqual(len(started), 1)  # Old active decode still occupies its slot.
        model.completeImage(0, old_generation, self.pixmap())
        self.assertNotIn(0, model.thumbnails)
        self.assertEqual(len(started), 2)
        self.assertEqual(started[-1], (0, "new.png", model.generation))
        model.completeImage(0, model.generation, self.pixmap())
        self.assertIn(0, model.thumbnails)

    def test_cache_budget_and_eviction_do_not_start_repaint_reload_loop(self):
        pixmap = self.pixmap()
        item_size = pixmap.width() * pixmap.height() * (pixmap.depth() // 8)
        model = self.model(count=4, cache_budget_bytes=item_size * 2)
        started = []
        model.loadRequested.connect(lambda *request: started.append(request))
        for row in range(3):
            self.request(model, row)
            model.completeImage(row, model.generation, pixmap)
            self.assertLessEqual(model.cache_bytes, model.cache_budget_bytes)
        self.assertEqual(list(model.thumbnails), [1, 2])
        for _ in range(20):
            self.request(model, 0)
        self.assertEqual(len(started), 3)
        model.retry_evicted()  # A new viewport makes the evicted row eligible again.
        self.request(model, 0)
        self.assertEqual(len(started), 4)

    def test_failed_image_is_not_retried_by_every_paint(self):
        model = self.model(count=1)
        started = []
        model.loadRequested.connect(lambda *request: started.append(request))
        self.request(model, 0)
        model.completeImage(0, model.generation, None)
        for _ in range(20):
            self.request(model, 0)
        self.assertEqual(len(started), 1)
        self.assertFalse(model.loading)
        self.assertFalse(model._active)

    def test_visible_images_stay_cached_when_old_offscreen_loads_finish(self):
        model = self.model(count=90, cache_budget_bytes=8 * 1024 * 1024)
        model.set_visible_assets(range(80))
        image = QPixmap(640, 640)
        image.fill(QColor('blue'))
        for row in range(90):
            self.request(model, row)
            model.completeImage(row, model.generation, image)
            self.assertLessEqual(model.cache_bytes, model.cache_budget_bytes)
        self.assertTrue(set(range(80)).issubset(model.thumbnails))
        self.assertTrue(all(pixmap.width() <= 320 for pixmap in model.thumbnails.values()))

    def test_large_real_viewport_finishes_loading_and_does_not_lose_visible_images(self):
        from time import monotonic
        from PyQt6.QtTest import QTest
        from ui.widgets.gallery_view import GalleryView
        with tempfile.TemporaryDirectory(prefix='refer-viewport-tests-') as directory:
            path = Path(directory) / 'image.png'
            image = QImage(640, 640, QImage.Format.Format_RGB32)
            image.fill(QColor('blue'))
            self.assertTrue(image.save(str(path)))
            view = GalleryView()
            model = AssetListModel()
            model.setAssets([Asset(id=row, thumbnail_path=str(path)) for row in range(200)])
            view.setModel(model)
            view.set_thumbnail_size(140)
            view.resize(1600, 1000)
            view.show()
            try:
                for position in (0, 500, 0):
                    view.verticalScrollBar().setValue(position)
                    deadline = monotonic() + 5
                    while monotonic() < deadline:
                        QTest.qWait(10)
                        if model._visible and model._visible.issubset(model.thumbnails) and not model._active:
                            break
                    self.assertGreater(len(model._visible), 40)
                    self.assertTrue(model._visible.issubset(model.thumbnails),
                                    f'Missing visible images: {model._visible - model.thumbnails.keys()}')
                    self.assertLessEqual(model.cache_bytes, model.cache_budget_bytes)
                    for _ in range(3):
                        view.viewport().update()
                        QTest.qWait(10)
                        self.assertTrue(model._visible.issubset(model.thumbnails))
            finally:
                model.cancel_loads()
                view.thread_pool.waitForDone(2000)
                view.close()

    def test_close_cancellation_does_not_start_pending_requests(self):
        model = self.model(count=2, max_active_loads=1)
        started = []
        model.loadRequested.connect(lambda *request: started.append(request))
        self.request(model, 0)
        self.request(model, 1)
        generation = model.generation
        model.cancel_loads()
        model.completeImage(0, generation, self.pixmap())
        self.request(model, 1)
        self.assertEqual(len(started), 1)
        self.assertFalse(model._pending)
        self.assertFalse(model._active)

    def test_queue_overflow_requests_one_viewport_retry_when_capacity_returns(self):
        model = self.model(count=5, max_active_loads=1, max_pending_loads=2)
        capacity = []
        model.loadCapacityAvailable.connect(lambda: capacity.append(True))
        for row in range(5):
            self.request(model, row)
        self.assertNotIn(1, model.loading)
        self.assertFalse(capacity)
        model.completeImage(0, model.generation, self.pixmap())
        self.assertEqual(len(capacity), 1)
        model.completeImage(4, model.generation, self.pixmap())
        self.assertEqual(len(capacity), 1)

    def test_adding_page_keeps_cached_image_but_changed_path_drops_it(self):
        model = self.model(count=1)
        self.request(model, 0)
        model.completeImage(0, model.generation, self.pixmap())
        model.setAssets([Asset(id=0, thumbnail_path="fixture-0.png"),
                         Asset(id=1, thumbnail_path="fixture-1.png")])
        self.assertIn(0, model.thumbnails)
        model.setAssets([Asset(id=0, thumbnail_path="different.png")])
        self.assertFalse(model.thumbnails)
        self.assertEqual(model.cache_bytes, 0)

    def test_reader_scales_real_image_and_emits_single_completion(self):
        # Synthetic fixture stays within the workspace and is removed after the test.
        with tempfile.TemporaryDirectory(prefix="refer-thumbnail-test-", dir=Path(__file__).resolve().parents[1]) as folder:
            path = Path(folder) / "synthetic.png"
            image = QImage(2_048, 1_024, QImage.Format.Format_RGB32)
            image.fill(QColor("blue"))
            self.assertTrue(image.save(str(path)))
            worker = ImageLoaderWorker(7, path.as_uri(), generation=42, max_size=640)
            completed = []
            worker.signals.completed.connect(lambda *args: completed.append(args))
            worker.run()
            self.assertEqual(len(completed), 1)
            self.assertEqual(completed[0][:2], (7, 42))
            self.assertEqual(completed[0][2].size(), QSize(640, 320))
            self.assertEqual(completed[0][3], "")

    def test_cancelled_worker_frees_slot_without_decoding(self):
        cancel = Event()
        cancel.set()
        worker = ImageLoaderWorker(7, "never-opened.png", generation=42, cancellation=cancel)
        completed = []
        worker.signals.completed.connect(lambda *args: completed.append(args))
        with patch("ui.workers.image_loader.QImageReader") as reader:
            worker.run()
            reader.assert_not_called()
        self.assertEqual(len(completed), 1)
        self.assertTrue(completed[0][2].isNull())

    def test_missing_worker_image_finishes_once_with_error(self):
        worker = ImageLoaderWorker(7, str(Path(__file__).parent / "missing-test-fixture.png"))
        completed = []
        worker.signals.completed.connect(lambda *args: completed.append(args))
        worker.run()
        self.assertEqual(len(completed), 1)
        self.assertTrue(completed[0][2].isNull())
        self.assertIn("File cannot be loaded", completed[0][3])

    def test_plain_click_opens_viewer_but_ctrl_click_only_selects(self):
        from types import SimpleNamespace
        from PyQt6.QtTest import QTest
        from ui.widgets.gallery_view import GalleryView
        opened = []
        viewer = SimpleNamespace(set_assets=lambda assets, row: opened.append(row), show=lambda: None,
                                 raise_=lambda: None, activateWindow=lambda: None)
        view = GalleryView()
        model = AssetListModel()
        model.setAssets([Asset(id=row) for row in range(3)])
        view.setModel(model)
        view.resize(700, 300)
        view.show()
        self.app.processEvents()
        try:
            with patch('ui.widgets.gallery_view.ImageViewerWindow', return_value=viewer):
                QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                                 pos=view.visualRect(model.index(0, 0)).center())
                self.assertEqual(opened, [0])
                QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier,
                                 view.visualRect(model.index(1, 0)).center())
                self.assertEqual(opened, [0])
                self.assertEqual({index.row() for index in view.selectionModel().selectedIndexes()}, {0, 1})
        finally:
            view.close()


if __name__ == "__main__":
    unittest.main()
