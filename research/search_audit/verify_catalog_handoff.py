"""Verify catalog handoff fixes exclusively on disposable fixture data."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'


def run():
    with tempfile.TemporaryDirectory(prefix='refer-catalog-review-') as directory:
        os.environ['APPDATA'] = directory
        os.environ['LOCALAPPDATA'] = directory
        from PyQt6.QtWidgets import QApplication
        from database.source_group_store import SourceGroupStore
        from ui.catalog_dialog import CatalogDialog
        from test_source_groups import TestDBSourceGroups
        app = QApplication.instance() or QApplication([])
        db = TestDBSourceGroups()
        store = SourceGroupStore(store_path=Path(directory) / 'groups.json', db=db)

        # 1. Verification of draft isolation: Cancel does not keep group changes
        before_text = store.path.read_text(encoding='utf-8')
        before_groups = store.get_groups()
        dialog = CatalogDialog(db, store)
        with patch('ui.catalog_dialog.QInputDialog.getText', return_value=('Cancelled change', True)):
            dialog._create_group()
        dialog.reject()
        cancellation_persists = (
            store.path.read_text(encoding='utf-8') != before_text
            or store.get_groups() != before_groups
        )

        # Confirm reopening loads pristine state
        dialog2 = CatalogDialog(db, store)
        dialog2_names = [g['name'] for g in dialog2.group_store.get_groups()]
        assert 'Cancelled change' not in dialog2_names, "Cancelled change leaked into newly opened dialog"
        dialog2.reject()

        # Confirm "Применить" saves changes
        dialog3 = CatalogDialog(db, store)
        with patch('ui.catalog_dialog.QInputDialog.getText', return_value=('Applied change', True)):
            dialog3._create_group()
        applied = dialog3._apply_and_close()
        assert applied is True, "Apply failed unexpectedly"
        assert 'Applied change' in [g['name'] for g in store.get_groups()], "Applied change not saved to store"

        # 2. Verification of cycle prevention on delete with transfer into descendant
        parent = store.create_group('Parent')
        child = store.create_group('Child', parent_id=parent)
        grandchild = store.create_group('GrandChild', parent_id=child)
        deletion_cycle = False
        try:
            store.delete_group(parent, target_group_id=child)
            deletion_cycle = store.get_group(child)['parent_id'] == child
        except ValueError:
            deletion_cycle = False

        try:
            store.delete_group(parent, target_group_id=grandchild)
            deletion_cycle = True
        except ValueError:
            pass

        # Safe transfer into sibling or root works
        sibling = store.create_group('Sibling')
        store.delete_group(child, target_group_id=sibling)
        assert store.get_group(grandchild)['parent_id'] == sibling

        # Cycle detection in load()
        cycle_json = Path(directory) / 'cycle.json'
        cycle_json.write_text(json.dumps({
            "version": 1,
            "groups": [
                {"id": "g1", "name": "G1", "parent_id": "g2"},
                {"id": "g2", "name": "G2", "parent_id": "g1"}
            ]
        }), encoding='utf-8')
        try:
            SourceGroupStore(store_path=cycle_json, db=db)
            cycle_detected_on_load = False
        except RuntimeError:
            cycle_detected_on_load = True
        assert cycle_detected_on_load, "Store failed to detect cycle on load"

        # 3. Verification of transactional rollback on write failure
        before_file = store.path.read_text(encoding='utf-8')
        before_groups = store.get_groups()
        with patch('database.source_group_store.os.replace', side_effect=OSError('simulated write failure')):
            try:
                store.create_group('Failed change')
            except RuntimeError:
                pass
        memory_diverges = (
            store.get_groups() != before_groups
            and store.path.read_text(encoding='utf-8') == before_file
        )

        result = {
            'cancel_keeps_group_change': cancellation_persists,
            'delete_into_descendant_creates_cycle': deletion_cycle,
            'failed_save_changes_in_memory_state': memory_diverges,
            'database': 'temporary fixture only'
        }
        # All three defect indicators must now be False (fixed)
        assert not any(result[key] for key in list(result)[:3]), f"Defects still present: {result}"

        output = Path(__file__).with_name('catalog_handoff_review.json')
        output.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result))
        dialog.deleteLater()
        dialog2.deleteLater()
        dialog3.deleteLater()
        app.processEvents()


if __name__ == '__main__':
    run()
