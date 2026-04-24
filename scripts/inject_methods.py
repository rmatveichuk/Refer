import re

with open("ui/main_window.py", "r", encoding="utf-8") as f:
    content = f.read()

methods = r'''
    def _select_all_gallery(self):
        self.gallery.selectAll()

    def _delete_selected_gallery(self):
        selection_model = self.gallery.selectionModel()
        if not selection_model.hasSelection():
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Ничего не выбрано", "Пожалуйста, выделите картинки для удаления.")
            return

        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self,
            "Удаление",
            "Вы действительно хотите удалить выбранные картинки?\nОни больше не будут добавляться при сканировании.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        indexes = selection_model.selectedIndexes()
        
        # Sort in reverse order to safely delete by index if needed
        # But we will use the IDs from the model instead
        assets_to_delete = []
        for idx in indexes:
            asset = self.gallery_model.assets[idx.row()]
            assets_to_delete.append(asset)

        if not assets_to_delete:
            return

        import os
        import logging
        logger = logging.getLogger(__name__)

        deleted_count = 0
        for asset in assets_to_delete:
            # Add to deleted list to prevent re-adding (this happens in mark_as_deleted)
            if asset.original_url:
                self.db.mark_as_deleted(asset.original_url, reason="user_deleted", phash=asset.phash)
            
            # Delete physical file
            if asset.local_path and os.path.exists(asset.local_path):
                try:
                    os.remove(asset.local_path)
                except Exception as e:
                    logger.warning(f"Failed to delete local_path file: {e}")

            if asset.thumbnail_path and os.path.exists(asset.thumbnail_path):
                try:
                    os.remove(asset.thumbnail_path)
                except Exception as e:
                    logger.warning(f"Failed to delete thumbnail file: {e}")
            
            # Delete from DB
            with self.db.get_connection() as conn:
                conn.execute("DELETE FROM asset_tags WHERE asset_id = ?", (asset.id,))
                conn.execute("DELETE FROM assets WHERE id = ?", (asset.id,))
                conn.commit()
            deleted_count += 1

        self._load_assets_for_gallery()
        self._refresh_library()
        self.status_label.setText(f"Удалено {deleted_count} картинок.")

    def _setup_library_tab'''

content = content.replace("    def _setup_library_tab", methods)

with open("ui/main_window.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Methods injected")
