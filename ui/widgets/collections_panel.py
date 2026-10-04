from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

from PyQt6.QtCore import Qt, QSize, pyqtSignal, QSignalBlocker
from PyQt6.QtGui import QIcon, QPixmap, QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QMenu, QInputDialog, QMessageBox,
    QAbstractItemView
)

from database.collection_repository import CollectionRepository
from ui.export_collection_dialog import ExportCollectionDialog

logger = logging.getLogger(__name__)

MIME_ASSET_IDS = "application/x-refer-asset-ids"


class CollectionsListWidget(QListWidget):
    """QListWidget с удобным приёмом Drag & Drop (большая зона сброса и подсветка)."""

    assetsDropped = pyqtSignal(int, list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setIconSize(QSize(32, 32))
        self._hover_item: Optional[QListWidgetItem] = None

        self._base_stylesheet = """
            QListWidget {
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 6px;
                outline: none;
                padding: 2px;
            }
            QListWidget::item {
                min-height: 38px;
                padding: 6px 10px;
                border-radius: 6px;
                color: #e2e4e8;
                font-size: 13px;
                background-color: #1a1c22;
                border: 1px solid #2a2d36;
                margin-bottom: 4px;
            }
            QListWidget::item:hover {
                background-color: #252831;
                border-color: #3d4250;
                color: #fff;
            }
            QListWidget::item:selected {
                background-color: #1976D2;
                border-color: #2196F3;
                color: #ffffff;
                font-weight: bold;
            }
        """
        self.setStyleSheet(self._base_stylesheet)

    def _reset_drag_style(self):
        self.setStyleSheet(self._base_stylesheet)

    def _apply_drag_style(self):
        self.setStyleSheet(self._base_stylesheet + """
            QListWidget {
                border: 2px dashed #2196F3;
                background-color: rgba(33, 150, 243, 0.07);
                border-radius: 6px;
            }
        """)

    def _clear_hover(self):
        if self._hover_item:
            self._hover_item.setBackground(QColor(0, 0, 0, 0))
            self._hover_item = None

    def _get_target_item(self, pos=None) -> Optional[QListWidgetItem]:
        if pos is not None:
            item = self.itemAt(pos)
            if item:
                return item

        # If hovering outside a specific item (e.g. empty space of list):
        panel = self.parent() if isinstance(self.parent(), CollectionsPanel) else None
        if panel:
            # 1. Currently opened collection
            if panel._current_selected_id is not None:
                for i in range(self.count()):
                    it = self.item(i)
                    if it.data(Qt.ItemDataRole.UserRole) == panel._current_selected_id:
                        return it

            # 2. Quick Target collection
            target_id = panel.repository.get_quick_target()
            if target_id is not None:
                for i in range(self.count()):
                    it = self.item(i)
                    if it.data(Qt.ItemDataRole.UserRole) == target_id:
                        return it

        # 3. First item in list
        return self.item(0) if self.count() > 0 else None

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(MIME_ASSET_IDS):
            self._apply_drag_style()
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(MIME_ASSET_IDS):
            item = self._get_target_item(event.position().toPoint())
            if self._hover_item and self._hover_item != item:
                self._clear_hover()
            if item:
                item.setBackground(QColor(33, 150, 243, 130))
                self._hover_item = item
                event.acceptProposedAction()
            else:
                event.ignore()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._clear_hover()
        self._reset_drag_style()
        event.accept()

    def dropEvent(self, event):
        self._clear_hover()
        self._reset_drag_style()

        item = self._get_target_item(event.position().toPoint())
        if not item or not event.mimeData().hasFormat(MIME_ASSET_IDS):
            event.ignore()
            return

        collection_id = item.data(Qt.ItemDataRole.UserRole)
        raw_data = bytes(event.mimeData().data(MIME_ASSET_IDS))
        try:
            asset_ids = json.loads(raw_data.decode("utf-8"))
            if isinstance(asset_ids, list) and asset_ids:
                self.assetsDropped.emit(collection_id, asset_ids)
                event.acceptProposedAction()
                return
        except Exception as e:
            logger.error(f"Error parsing dropped asset IDs: {e}")
        event.ignore()


class CollectionsPanel(QWidget):
    """Панель управления мудбордами / наборами в левом сайдбаре."""

    collectionSelected = pyqtSignal(int)
    collectionCleared = pyqtSignal()
    contentsChanged = pyqtSignal()
    statusNotice = pyqtSignal(str)

    def __init__(self, repository: CollectionRepository, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.last_added: Optional[tuple[int, list[int]]] = None
        self._current_selected_id: Optional[int] = None

        self._init_ui()
        self.reload()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 6)
        layout.setSpacing(6)

        # Header Row
        header = QHBoxLayout()
        header.setContentsMargins(4, 4, 4, 2)

        title = QLabel("МУДБОРДЫ")
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(10)
        title.setFont(title_font)
        title.setStyleSheet("color: #8e949e; letter-spacing: 0.5px;")

        self.btn_create = QPushButton("+")
        self.btn_create.setFixedSize(24, 24)
        self.btn_create.setToolTip("Создать новый мудборд под проект")
        self.btn_create.setStyleSheet("""
            QPushButton {
                background-color: #24272e;
                color: #e0e0e0;
                font-weight: bold;
                font-size: 15px;
                border: 1px solid #383c45;
                border-radius: 4px;
                padding-bottom: 2px;
            }
            QPushButton:hover {
                background-color: #1976D2;
                border-color: #2196F3;
                color: #fff;
            }
        """)
        self.btn_create.clicked.connect(self._on_create_clicked)

        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.btn_create)
        layout.addLayout(header)

        # "Back to all" button (hidden when viewing whole library)
        self.btn_show_all = QPushButton("← Все референсы")
        self.btn_show_all.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                padding: 4px 6px;
                text-align: left;
                color: #29b6f6;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                color: #81d4fa;
                text-decoration: underline;
            }
        """)
        self.btn_show_all.clicked.connect(self.clear_selection)
        self.btn_show_all.hide()
        layout.addWidget(self.btn_show_all)

        # Lists of collections
        self.list_widget = CollectionsListWidget(self)
        self.list_widget.assetsDropped.connect(self.add_assets_to_collection)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self.list_widget)

        # Quick Target Hint
        self.hint_label = QLabel()
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("color: #727885; font-size: 11px; padding: 0 4px;")
        layout.addWidget(self.hint_label)

        # Undo button
        self.btn_undo = QPushButton("Отменить добавление")
        self.btn_undo.setEnabled(False)
        self.btn_undo.setStyleSheet("""
            QPushButton {
                background-color: #1e2025;
                color: #9aa0a6;
                border: 1px solid #30333b;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
            }
            QPushButton:hover:enabled {
                background-color: #2d3038;
                color: #fff;
            }
            QPushButton:disabled {
                color: #4b5059;
                border-color: #22252a;
            }
        """)
        self.btn_undo.clicked.connect(self.undo_last_add)
        layout.addWidget(self.btn_undo)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(MIME_ASSET_IDS):
            self.list_widget.dragEnterEvent(event)
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(MIME_ASSET_IDS):
            self.list_widget.dragMoveEvent(event)
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.list_widget.dragLeaveEvent(event)

    def dropEvent(self, event):
        self.list_widget.dropEvent(event)

    def reload(self):
        """Обновляет список наборов, иконки обложек и статус Quick Target."""
        with QSignalBlocker(self.list_widget):
            self.list_widget.clear()
            collections = self.repository.get_collections_with_counts()
            target_id = self.repository.get_quick_target()

            quick_target_name = None

            for col in collections:
                cid = col["id"]
                is_target = (cid == target_id)
                prefix = "★ " if is_target else ""
                count = col["asset_count"]
                item_text = f"{prefix}{col['name']}  ·  {count}"

                item = QListWidgetItem(item_text)
                item.setSizeHint(QSize(0, 44))
                item.setData(Qt.ItemDataRole.UserRole, cid)
                item.setToolTip(f"{col['name']}\nИзображений: {count}" + ("\n(Quick Target: Ctrl+B)" if is_target else ""))

                # Set cover thumbnail if available
                thumb_path = col.get("cover_thumbnail_path")
                if thumb_path and Path(thumb_path).exists():
                    item.setIcon(QIcon(str(thumb_path)))

                self.list_widget.addItem(item)

                if cid == self._current_selected_id:
                    self.list_widget.setCurrentItem(item)

                if is_target:
                    quick_target_name = col["name"]

            # Update hint
            if quick_target_name:
                self.hint_label.setText(f"Ctrl+B → {quick_target_name}\nИли перетащите кадры в этот блок.")
            else:
                self.hint_label.setText("Ctrl+B: назначьте Quick Target\nИли перетащите кадры в этот блок.")

    def _on_item_clicked(self, item: QListWidgetItem):
        cid = item.data(Qt.ItemDataRole.UserRole)
        self._current_selected_id = cid
        self.btn_show_all.show()
        self.collectionSelected.emit(cid)

    def clear_selection(self):
        self._current_selected_id = None
        self.list_widget.clearSelection()
        self.btn_show_all.hide()
        self.collectionCleared.emit()

    def _on_create_clicked(self):
        name, ok = QInputDialog.getText(
            self,
            "Новый мудборд",
            "Название проекта / подборки:",
            text=""
        )
        if ok and name.strip():
            try:
                cid = self.repository.create_collection(name.strip())
                self.reload()
                self.statusNotice.emit(f"Создан мудборд '{name.strip()}'")
            except Exception as e:
                QMessageBox.warning(self, "Ошибка", str(e))

    def add_assets_to_collection(self, collection_id: int, asset_ids: List[int]):
        if not asset_ids:
            return
        try:
            added = self.repository.add_assets(collection_id, asset_ids)
            col = self.repository.get_collection(collection_id)
            col_name = col["name"] if col else "Набор"

            if added:
                self.last_added = (collection_id, added)
                self.btn_undo.setEnabled(True)
                msg = f"В набор '{col_name}' добавлено: {len(added)}"
                if len(asset_ids) > len(added):
                    msg += f" (уже были: {len(asset_ids) - len(added)})"
            else:
                msg = f"Все выбранные изображения ({len(asset_ids)}) уже есть в наборе '{col_name}'"

            self.reload()
            self.contentsChanged.emit()
            self.statusNotice.emit(msg)
        except Exception as e:
            QMessageBox.warning(self, "Ошибка добавления", str(e))

    def quick_add(self, asset_ids: List[int]):
        """Быстрое добавление выделенных кадров в текущий Quick Target (Ctrl+B)."""
        target_id = self.repository.get_quick_target()
        if not target_id:
            QMessageBox.information(
                self,
                "Quick Target",
                "Сначала выберите активный мудборд (правый клик по строке набора → «Сделать Quick Target»)."
            )
            return
        self.add_assets_to_collection(target_id, asset_ids)

    def undo_last_add(self):
        if not self.last_added:
            return
        collection_id, added_ids = self.last_added
        try:
            self.repository.remove_assets(collection_id, added_ids)
            col = self.repository.get_collection(collection_id)
            name = col["name"] if col else "Набор"
            self.statusNotice.emit(f"Отменено добавление {len(added_ids)} кадров в '{name}'")
            self.last_added = None
            self.btn_undo.setEnabled(False)
            self.reload()
            self.contentsChanged.emit()
        except Exception as e:
            QMessageBox.warning(self, "Ошибка отмены", str(e))

    def _on_context_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        if not item:
            return
        cid = item.data(Qt.ItemDataRole.UserRole)
        col = self.repository.get_collection(cid)
        if not col:
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #202227; color: #eee; border: 1px solid #383c45; padding: 4px; }
            QMenu::item { padding: 6px 20px; border-radius: 3px; }
            QMenu::item:selected { background-color: #1976D2; color: #fff; }
        """)

        target_id = self.repository.get_quick_target()
        is_target = (cid == target_id)

        act_target = menu.addAction("★ Сделать Quick Target (Ctrl+B)")
        act_target.setEnabled(not is_target)

        menu.addSeparator()
        act_export = menu.addAction("📁 Экспортировать подборку…")
        act_rename = menu.addAction("✏️ Переименовать…")
        act_clear_cover = menu.addAction("Сбросить обложку")
        act_clear_cover.setEnabled(bool(col.get("cover_asset_id")))

        menu.addSeparator()
        act_delete = menu.addAction("🗑 Удалить мудборд…")

        action = menu.exec(self.list_widget.viewport().mapToGlobal(pos))

        if action == act_target:
            self.repository.set_quick_target(cid)
            self.reload()
            self.statusNotice.emit(f"Quick Target назначен: '{col['name']}'")

        elif action == act_export:
            dlg = ExportCollectionDialog(cid, self.repository, self)
            dlg.exec()

        elif action == act_rename:
            new_name, ok = QInputDialog.getText(
                self,
                "Переименовать мудборд",
                "Новое название:",
                text=col["name"]
            )
            if ok and new_name.strip():
                try:
                    self.repository.rename_collection(cid, new_name.strip())
                    self.reload()
                except Exception as e:
                    QMessageBox.warning(self, "Ошибка", str(e))

        elif action == act_clear_cover:
            self.repository.set_cover(cid, None)
            self.reload()

        elif action == act_delete:
            ans = QMessageBox.question(
                self,
                "Удалить мудборд?",
                f"Удалить мудборд '{col['name']}'?\n\nВсе изображения останутся в общей библиотеке.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if ans == QMessageBox.StandardButton.Yes:
                if self._current_selected_id == cid:
                    self.clear_selection()
                self.repository.delete_collection(cid)
                self.reload()
                self.contentsChanged.emit()
                self.statusNotice.emit(f"Мудборд '{col['name']}' удален")
