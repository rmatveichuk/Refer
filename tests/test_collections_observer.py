"""Tests for CollectionsPanel external database observer and live synchronization."""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication
from database.db_manager import DatabaseManager
from database.collection_repository import CollectionRepository
from ui.widgets.collections_panel import CollectionsPanel

app = QApplication.instance() or QApplication(sys.argv)


class CollectionsObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="refer_test_observer_"))
        self.db_path = self.temp_dir / "test_collection.db"
        self.db_mgr = DatabaseManager(self.db_path)
        self.repo = CollectionRepository(self.db_mgr)
        self.panel = CollectionsPanel(repository=self.repo)

    def tearDown(self):
        self.panel._close_observer_conn()
        self.panel._sync_timer.stop()
        self.panel.deleteLater()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_external_insert_detected_and_reloaded(self):
        """External process inserts collection; observer detects it via PRAGMA data_version and updates list."""
        initial_count = self.panel.list_widget.count()

        # External connection writes to DB
        ext_conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        ext_conn.execute("INSERT INTO collections (name, name_key, description) VALUES ('Ext Board', 'ext board', 'Created by MCP')")
        ext_conn.commit()
        ext_conn.close()

        # Trigger observer check
        events = []
        self.panel.contentsChanged.connect(lambda: events.append(True))
        self.panel._check_external_changes()

        self.assertEqual(len(events), 1, "contentsChanged should be emitted on external change")
        self.assertEqual(self.panel.list_widget.count(), initial_count + 1)

    def test_external_delete_of_selected_clears_selection(self):
        """If currently selected collection is deleted externally, selection is safely reset."""
        cid = self.repo.create_collection(name="To be deleted", ids=[])
        self.panel.reload()
        self.panel._on_row_clicked(cid)
        self.assertEqual(self.panel._current_selected_id, cid)

        # Delete externally
        ext_conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        ext_conn.execute("DELETE FROM collections WHERE id = ?", (cid,))
        ext_conn.commit()
        ext_conn.close()

        cleared_events = []
        self.panel.collectionCleared.connect(lambda: cleared_events.append(True))
        self.panel._check_external_changes()

        self.assertIsNone(self.panel._current_selected_id)
        self.assertEqual(len(cleared_events), 1)

    def test_external_process_insert_detected_and_reloaded(self):
        """A separate OS Python process inserts a collection; PRAGMA data_version detects it."""
        import subprocess
        initial_count = self.panel.list_widget.count()

        cmd = [
            sys.executable,
            "-c",
            f"import sqlite3; conn = sqlite3.connect(r'{self.db_path}', timeout=5.0); "
            "conn.execute(\"INSERT INTO collections (name, name_key, description) VALUES ('Subproc Board', 'subproc board', 'From Subprocess')\"); "
            "conn.commit(); conn.close()"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"External process failed: {res.stderr}")

        events = []
        self.panel.contentsChanged.connect(lambda: events.append(True))
        self.panel._check_external_changes()

        self.assertEqual(len(events), 1, "contentsChanged should be emitted on external OS process change")
        self.assertEqual(self.panel.list_widget.count(), initial_count + 1)


if __name__ == "__main__":
    unittest.main()
