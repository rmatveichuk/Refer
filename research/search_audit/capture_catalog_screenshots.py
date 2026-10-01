"""Capture high-resolution verification screenshots for the Catalog UI Redesign:
- Main window with calm indicators, compact top bar (hamburger), operational source groups, and refinements block;
- Catalog management dialog with splitter, properties card, calm checkboxes, and web import drawer.
Strictly read-only DB connection mode.
"""
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontDatabase
from ui.main_window import MainWindow
from ui.catalog_dialog import CatalogDialog
from ui.workers.results_worker import ReadOnlyDatabase
import config


def run_visual_capture():
    sys.stdout.reconfigure(encoding='utf-8')
    app = QApplication.instance() or QApplication([])
    if os.path.exists(r'C:\Windows\Fonts\segoeui.ttf'):
        QFontDatabase.addApplicationFont(r'C:\Windows\Fonts\segoeui.ttf')
        app.setFont(QFont('Segoe UI', 10))

    docs_research = ROOT / "docs" / "research"
    docs_research.mkdir(parents=True, exist_ok=True)

    with patch('ui.main_window.DatabaseManager', return_value=ReadOnlyDatabase(config.DB_PATH)), \
         patch.object(MainWindow, '_background_ai_init'):
        
        # 1. Main Window at 1200x850 (minimal supported resolution)
        window = MainWindow()
        window.resize(1200, 850)
        window.show()

        # Wait for results worker to populate gallery
        started = time.monotonic()
        deadline = started + 15
        while time.monotonic() < deadline:
            app.processEvents()
            if time.monotonic() - started > 3 and window._active_results_worker is None and not window.gallery_model._active:
                break
            time.sleep(.03)
        app.processEvents()

        # Make sure tags & filters widget is visible to showcase refinements
        window.search_panel.filters_widget.show()
        app.processEvents()

        # Capture 1200x850 interface
        output_1200 = docs_research / 'refer-unified-interface-1200.png'
        success = window.grab().save(str(output_1200))
        print(f"Captured {output_1200.name}: {success} ({window.width()}x{window.height()})")

        # 2. Main Window at 1440x900 (standard comfortable resolution)
        window.resize(1440, 900)
        app.processEvents()
        time.sleep(0.1)
        app.processEvents()

        output_main = docs_research / 'refer-unified-interface.png'
        success = window.grab().save(str(output_main))
        print(f"Captured {output_main.name}: {success} ({window.width()}x{window.height()})")

        # 3. Catalog Management Dialog at 1060x680
        dialog = CatalogDialog(window.db, window.group_store, parent=window)
        dialog.resize(1060, 680)
        dialog.show()
        app.processEvents()

        # Select a folder item in the tree to display properties card
        if dialog.tree.topLevelItemCount() > 0:
            top_item = dialog.tree.topLevelItem(0)
            if top_item.childCount() > 0:
                child_item = top_item.child(0)
                dialog.tree.setCurrentItem(child_item)
            else:
                dialog.tree.setCurrentItem(top_item)
        app.processEvents()

        output_dialog = docs_research / 'refer-catalog-dialog.png'
        success = dialog.grab().save(str(output_dialog))
        print(f"Captured {output_dialog.name}: {success} ({dialog.width()}x{dialog.height()})")

        dialog.close()
        window.close()
        print("Visual verification screenshots successfully generated.")


if __name__ == '__main__':
    run_visual_capture()
