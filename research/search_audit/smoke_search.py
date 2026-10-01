"""Offline smoke check of the implementation; opens the real library read-only."""
import os
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import json
import sqlite3
import time
import sys
from contextlib import contextmanager
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from unittest.mock import patch
from types import SimpleNamespace
import numpy as np
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPixmap, QFontDatabase, QFont
from ai.engine import AiEngine
from ui.main_window import MainWindow
from ui.catalog_dialog import CatalogDialog
from database.search_repository import SearchFilters, SearchRepository
import config


class ReadOnlyDB:
    def is_favorite(self, asset_id):
        with self.get_connection() as conn:
            return bool(conn.execute("SELECT is_favorite FROM assets WHERE id=?", (asset_id,)).fetchone()[0])

    def get_description(self, asset_id):
        with self.get_connection() as conn:
            return conn.execute("SELECT description FROM assets WHERE id=?", (asset_id,)).fetchone()[0]

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(config.DB_PATH.as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


def run():
    started = time.perf_counter()
    engine = AiEngine()
    load_ms = (time.perf_counter() - started) * 1000
    app = QApplication.instance() or QApplication([])
    # The offscreen Qt plugin cannot discover Windows fonts automatically.
    font_file = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeui.ttf"
    if font_file.exists():
        QFontDatabase.addApplicationFont(str(font_file))
        app.setFont(QFont("Segoe UI", 10))
    with patch("ui.main_window.DatabaseManager", return_value=ReadOnlyDB()), patch.object(MainWindow, "_background_ai_init"):
        window = MainWindow()
        window.ai = engine
        repo = SearchRepository(window.db, window.faiss_mgr)
        filters = SearchFilters(sources=("archdaily", "behance"), section="references", top_only=True)
        ids = [row["id"] for row in repo.candidates(filters)]
        results = []
        for query in ["бетонный дом среди сосен", "БЕТОННЫЙ ДОМ СРЕДИ СОСЕН", "бетонный дом среди сосен " * 35]:
            info = engine.get_text_query_info(query)
            started = time.perf_counter()
            vector = engine.get_text_embedding(query)
            embed_ms = (time.perf_counter() - started) * 1000
            assert vector.shape == (1152,) and np.isfinite(vector).all()
            assert abs(float(np.linalg.norm(vector)) - 1) < 0.001
            started = time.perf_counter()
            distances, found = window.faiss_mgr.search(vector, k=20, valid_ids=ids)
            search_ms = (time.perf_counter() - started) * 1000
            assert len(found) == 20 and all(int(aid) in ids for aid in found)
            results.append({"query": query, "info": info, "embedding_ms": embed_ms,
                            "cpu_faiss_ms": search_ms, "ids": found.tolist(), "distances": distances.tolist()})
        jobs = []
        window.search_panel.collection_combo.setCurrentIndex(1)
        window.search_panel.hybrid_input.text_input.setText("бетонный дом среди сосен")
        with patch("ui.main_window.QThreadPool.globalInstance", return_value=SimpleNamespace(start=jobs.append)):
            window.search_panel._emit_search()
            assert len(jobs) == 1
            jobs[0].run()
        assert window.gallery_model.rowCount() > 0
        assert window.library_table.model().rowCount() == window.gallery_model.rowCount()
        sample = window.gallery_model.assets[0]
        image_vector = engine.get_image_embedding(sample.thumbnail_path or sample.local_path)
        similar, _ = repo.search(filters, vector=image_vector, limit=20)
        assert len(similar) == 20 and sample.id in {asset.id for asset in similar}
        window.resize(1440, 900)
        window.show()
        def drain_images():
            deadline = time.perf_counter() + 8
            while time.perf_counter() < deadline:
                app.processEvents()
                assert len(window.gallery_model._active) <= 4
                assert len(window.gallery_model._pending) <= 64
                assert window.gallery_model.cache_bytes <= window.gallery_model.cache_budget_bytes
                if not window.gallery_model._active and not window.gallery_model._pending:
                    break
                time.sleep(0.01)
            app.processEvents()
        drain_images()
        output = config.BASE_DIR / "docs" / "research" / "refer-interface.png"
        pixmap = QPixmap(window.size())
        window.render(pixmap)
        assert pixmap.save(str(output))
        assert window.gallery_model.rowCount() == 400
        window._show_more_results()
        assert window.gallery_model.rowCount() == 800
        assert window.library_table.model().rowCount() == 800
        for step in range(20):
            bar = window.gallery.verticalScrollBar()
            bar.setValue(int(bar.maximum() * step / 19))
            drain_images()
        catalog = CatalogDialog(window.db, window.search_panel.source_assignments, window.search_panel.disabled_sources, window)
        catalog.show()
        app.processEvents()
        catalog_image = QPixmap(catalog.size())
        catalog.render(catalog_image)
        assert catalog_image.save(str(output.parent / "refer-catalogs.png"))
        catalog.close()
        window.gallery._on_item_clicked(window.gallery_model.index(0))
        app.processEvents()
        assert window.gallery._viewer_window.isVisible()
        window.gallery._viewer_window.close()
        stats = {"model_load_ms": load_ms, "index_size": window.faiss_mgr.index.ntotal,
                 "top_scope_size": len(ids), "queries": results,
                 "gallery_results": window.gallery_model.rowCount(), "image_search_checked": True,
                 "viewer_checked": True, "thumbnail_cache_bytes": window.gallery_model.cache_bytes,
                 "thumbnail_cache_budget": window.gallery_model.cache_budget_bytes,
                 "scroll_pages_checked": 20, "screenshot": str(output)}
        (Path(__file__).parent / "implementation_smoke.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in stats.items() if k != "queries"}))
        print("Offline real-model and CPU FAISS checks passed.")
        window.close()


if __name__ == "__main__":
    run()
