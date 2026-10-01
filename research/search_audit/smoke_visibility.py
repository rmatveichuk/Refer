"""Real-library read-only QA; visibility writes go to an isolated temporary file."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HF_HUB_OFFLINE"] = "1"
import sys
import json
import hashlib
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import faiss
import numpy as np
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtGui import QPixmap, QFontDatabase, QFont
from ui.main_window import MainWindow
from ui.hidden_assets_dialog import HiddenAssetsDialog
from ui.availability_dialog import AvailabilityDialog
from database.visibility_store import VisibilityStore
from database.search_repository import SearchRepository
from database.availability import inspect_files
from research.search_audit.smoke_search import ReadOnlyDB
import config


def snapshot(widget, name):
    widget.show()
    QApplication.processEvents()
    pixmap = QPixmap(widget.size())
    widget.render(pixmap)
    assert pixmap.save(str(config.BASE_DIR / "docs" / "research" / name))


def run():
    app = QApplication([])
    QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / "segoeui.ttf"))
    app.setFont(QFont("Segoe UI", 10))
    with tempfile.TemporaryDirectory(prefix="refer-visibility-smoke-") as temporary:
        visibility = VisibilityStore(Path(temporary) / "hidden_assets.json")
        with patch("ui.main_window.DatabaseManager", return_value=ReadOnlyDB()), \
             patch("ui.main_window.VisibilityStore", return_value=visibility), \
             patch.object(MainWindow, "_background_ai_init"):
            window = MainWindow()
            window.resize(1440, 900)
            old_ids = window.faiss_mgr.get_all_ids().copy()
            with window.db.get_connection() as conn:
                before = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
            asset = next(asset for asset in window.gallery_model.assets if asset.thumbnail_path and Path(asset.thumbnail_path).is_file())
            original_path = Path(asset.thumbnail_path)
            original_hash = hashlib.sha256(original_path.read_bytes()).hexdigest()
            position = int(np.flatnonzero(old_ids == asset.id)[0])
            vector = faiss.downcast_index(window.faiss_mgr.index.index).reconstruct(position)
            with patch("ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                assert window._delete_assets_batch([asset])
            assert asset.id not in {row.id for row in window.gallery_model.assets}
            repo = SearchRepository(window.db, window.faiss_mgr, window.search_panel.source_assignments, visibility)
            ranked, _ = repo.search(window._current_filters(), vector=vector, limit=20)
            assert asset.id not in {row.id for row in ranked}
            dialog = HiddenAssetsDialog(window.db, visibility, window)
            assert dialog.available_ids == [asset.id]
            snapshot(dialog, "refer-hidden-images.png")
            dialog._open_image(dialog.table.item(0, 0))
            app.processEvents()
            assert dialog._viewer.isVisible()
            dialog._viewer.close()
            dialog.close()
            window._restore_hidden_assets([asset.id])
            assert asset.id in {row.id for row in window.gallery_model.assets}
            assert np.array_equal(window.faiss_mgr.get_all_ids(), old_ids)
            assert hashlib.sha256(original_path.read_bytes()).hexdigest() == original_hash
            started = time.perf_counter()
            report = inspect_files(window.db)
            elapsed = time.perf_counter() - started
            check = AvailabilityDialog(report, window)
            snapshot(check, "refer-file-check.png")
            check.close()
            with window.db.get_connection() as conn:
                assert conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == before
            stats = {"checked": report.checked, "issue_count": len(report.issues), "issues_by_status": report.counts,
                     "diagnostics_seconds": elapsed, "hidden_and_restored_id": asset.id,
                     "index_ids_unchanged": True, "cached_file_unchanged": True,
                     "visibility_state_isolated": True, "model_loaded": False}
            (Path(__file__).parent / "visibility_smoke.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(stats, ensure_ascii=False))
            window.close()


if __name__ == "__main__":
    run()
