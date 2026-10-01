"""Render the real UI against the live library in read-only mode, without AI."""
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont, QFontDatabase
from ui.main_window import MainWindow
from ui.workers.results_worker import ReadOnlyDatabase
import config


def run():
    sys.stdout.reconfigure(encoding='utf-8')
    app = QApplication([])
    QFontDatabase.addApplicationFont(r'C:\Windows\Fonts\segoeui.ttf')
    app.setFont(QFont('Segoe UI', 10))
    with patch('ui.main_window.DatabaseManager', return_value=ReadOnlyDatabase(config.DB_PATH)), \
         patch.object(MainWindow, '_background_ai_init'):
        window = MainWindow()
        window.resize(1440, 900)
        window.show()
        started = time.monotonic()
        deadline = started + 15
        while time.monotonic() < deadline:
            app.processEvents()
            if time.monotonic() - started > 3 and window._active_results_worker is None and not window.gallery_model._active:
                break
            time.sleep(.02)
        app.processEvents()
        output = ROOT / 'docs/research/refer-unified-interface.png'
        assert window.grab().save(str(output)), output
        before = window.gallery.viewport().height()
        window.filters_button.click()
        app.processEvents()
        assert window.gallery.viewport().height() == before
        output_filters = ROOT / 'docs/research/refer-unified-filters.png'
        assert window.grab().save(str(output_filters)), output_filters
        print(f'Rendered {len(window.gallery_model.assets)} images; gallery viewport '
              f'{window.gallery.viewport().width()}×{before}.')
        window.close()


if __name__ == '__main__':
    run()
