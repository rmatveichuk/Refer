"""Replay GUI searches in the project Python with a read-only live library."""
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
from ui.main_window import MainWindow
from ui.workers.results_worker import ReadOnlyDatabase
import config


def run():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    print('Runtime smoke starting', flush=True)
    import traceback
    def report_error(kind, error, tb):
        traceback.print_exception(kind, error, tb)
    sys.excepthook = report_error
    app = QApplication([])
    queries = ['бетонный дом среди сосен', 'БЕТОННЫЙ ДОМ СРЕДИ СОСЕН']
    results, gaps = [], []
    last = time.perf_counter()
    started = None
    failure = []
    with patch('ui.main_window.DatabaseManager', return_value=ReadOnlyDatabase(config.DB_PATH)):
        window = MainWindow()
        window.resize(1600, 1000)
        window.show()

        def query():
            nonlocal started
            started = time.perf_counter()
            window.search_panel.hybrid_input.text_input.setText(queries[len(results)])
            window.search_panel._emit_search()

        def beat():
            nonlocal last, started
            now = time.perf_counter()
            gaps.append(now - last)
            last = now
            if started is None or window._embedding_cache is None:
                return
            if window._embedding_cache[0][0] != queries[len(results)]:
                return
            if window._active_results_worker is not None or window.active_searcher is not None:
                return
            assets = window.gallery_model.assets
            if not assets:
                return
            results.append({'query': queries[len(results)], 'seconds': now - started,
                            'count': len(assets), 'first_ids': [asset.id for asset in assets[:20]]})
            started = None
            if len(results) == len(queries):
                app.quit()
            else:
                QTimer.singleShot(100, query)

        def timeout():
            failure.append(window.status_label.text())
            app.quit()

        timer = QTimer()
        timer.timeout.connect(beat)
        timer.start(20)
        QTimer.singleShot(250, query)
        QTimer.singleShot(30000, timeout)
        try:
            app.exec()
        finally:
            window.close()
        assert not failure, failure
        assert len(results) == 2, results
        assert max(gaps) < 2, max(gaps)
        stats = {'python': sys.executable, 'queries': results, 'max_gui_gap_seconds': max(gaps),
                 'index_size': window.faiss_mgr.index.ntotal,
                 'windows_search': 'NumPy squared L2 over existing FAISS flat vectors',
                 'model': 'isolated local process'}
        (Path(__file__).parent / 'runtime_smoke.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(stats, ensure_ascii=True))


if __name__ == '__main__':
    run()
