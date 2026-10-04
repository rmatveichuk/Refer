from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

from PyQt6.QtCore import Qt, QSize, QPointF, pyqtSignal, QSignalBlocker, QUrl
from PyQt6.QtGui import QIcon, QPixmap, QColor, QFont, QDesktopServices, QPainter, QPolygonF, QPen
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QMenu, QInputDialog, QMessageBox,
    QAbstractItemView, QFileDialog
)

import math

from database.collection_repository import CollectionRepository
from ui.export_collection_dialog import ExportCollectionDialog
from export.moodboard_exporter import sync_collection_web_moodboard

logger = logging.getLogger(__name__)

MIME_ASSET_IDS = "application/x-refer-asset-ids"


def _create_star_icon(color: str, size: int = 32) -> QIcon:
    """Создаёт геометрически выверенную, правильную пятиконечную звезду."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    poly = QPolygonF()
    cx, cy = size / 2.0, size / 2.0
    r_out = size * 0.40
    r_in = r_out * 0.40
    for i in range(10):
        r = r_out if i % 2 == 0 else r_in
        angle = -math.pi / 2 + i * math.pi / 5
        poly.append(QPointF(cx + r * math.cos(angle), cy + r * math.sin(angle)))
    p.setBrush(QColor(color))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPolygon(poly)
    p.end()
    return QIcon(pix)


def _create_cross_icon(color: str, size: int = 32) -> QIcon:
    """Создаёт аккуратный геометрический крестик без искажения шрифта."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(2.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    cx, cy = size / 2.0, size / 2.0
    d = size * 0.22
    p.drawLine(QPointF(cx - d, cy - d), QPointF(cx + d, cy + d))
    p.drawLine(QPointF(cx - d, cy + d), QPointF(cx + d, cy - d))
    p.end()
    return QIcon(pix)


def _create_plus_icon(color: str, size: int = 32) -> QIcon:
    """Создаёт аккуратный геометрический плюс без зависимости от шрифта."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(2.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    cx, cy = size / 2.0, size / 2.0
    d = size * 0.24
    p.drawLine(QPointF(cx - d, cy), QPointF(cx + d, cy))
    p.drawLine(QPointF(cx, cy - d), QPointF(cx, cy + d))
    p.end()
    return QIcon(pix)


class ActionButton(QPushButton):
    """Инлайн-кнопка действия с чистой векторной иконкой и подсветкой при наведении."""

    def __init__(self, normal_icon: QIcon, hover_icon: QIcon, tooltip: str, parent=None):
        super().__init__(parent)
        self._normal_icon = normal_icon
        self._hover_icon = hover_icon
        self.setFixedSize(26, 26)
        self.setIconSize(QSize(18, 18))
        self.setIcon(self._normal_icon)
        self.setToolTip(tooltip)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptDrops(False)
        self.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 4px;
                padding: 0;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.08);
            }
        """)

    def set_icons(self, normal_icon: QIcon, hover_icon: QIcon, tooltip: Optional[str] = None):
        self._normal_icon = normal_icon
        self._hover_icon = hover_icon
        self.setIcon(normal_icon)
        if tooltip:
            self.setToolTip(tooltip)

    def enterEvent(self, event):
        self.setIcon(self._hover_icon)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setIcon(self._normal_icon)
        super().leaveEvent(event)


class CollectionRowWidget(QWidget):
    """
    Виджет карточки мудборда в списке с превью обложки, названием, количеством кадров
    и двумя аккуратными малозаметными темносерыми иконками действий (★ Quick Target и ✕ Удалить).
    """

    quickTargetToggled = pyqtSignal(int)
    deleteRequested = pyqtSignal(int)
    rowClicked = pyqtSignal(int)

    def __init__(
        self,
        collection_data: Dict[str, Any],
        is_quick_target: bool,
        is_selected: bool,
        parent=None
    ):
        super().__init__(parent)
        self.cid = collection_data["id"]
        self.name = collection_data["name"]
        self.count = collection_data.get("asset_count", 0)
        self.is_quick_target = is_quick_target
        self.is_selected = is_selected

        self.setAcceptDrops(False)
        self._init_ui(collection_data.get("cover_thumbnail_path"))

    def _init_ui(self, thumb_path: Optional[str]):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 3, 5, 3)
        layout.setSpacing(8)

        # 1. Cover Thumbnail (32x32)
        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(32, 32)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setStyleSheet("border-radius: 4px; background-color: #242730;")
        self.thumb_label.setAcceptDrops(False)

        if thumb_path and Path(thumb_path).exists():
            pix = QPixmap(str(thumb_path))
            if not pix.isNull():
                self.thumb_label.setPixmap(
                    pix.scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                )
        else:
            self.thumb_label.setText("📁")
            self.thumb_label.setStyleSheet("border-radius: 4px; background-color: #242730; color: #666; font-size: 13px;")

        # 2. Text layout (Title + Count)
        text_layout = QHBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(5)

        self.name_label = QLabel(self.name)
        self.name_label.setToolTip(self.name)
        self.name_label.setAcceptDrops(False)

        self.count_label = QLabel(f"· {self.count}")
        self.count_label.setAcceptDrops(False)

        text_layout.addWidget(self.name_label)
        text_layout.addWidget(self.count_label)
        text_layout.addStretch()

        # 3. Action Buttons (Discreet dark-gray #5c606a, larger 26x26 vector geometry)
        if self.is_quick_target:
            star_normal = _create_star_icon("#ffca28")
            star_hover = _create_star_icon("#ffe082")
            star_tip = "Активный Quick Target (Ctrl+B)\nНажмите, чтобы снять"
        else:
            star_normal = _create_star_icon("#5c606a")
            star_hover = _create_star_icon("#ffd54f")
            star_tip = "Сделать Quick Target (Ctrl+B)"

        self.btn_star = ActionButton(star_normal, star_hover, star_tip, self)
        self.btn_star.clicked.connect(lambda: self.quickTargetToggled.emit(self.cid))

        cross_normal = _create_cross_icon("#5c606a")
        cross_hover = _create_cross_icon("#ef5350")
        self.btn_delete = ActionButton(cross_normal, cross_hover, "Удалить мудборд", self)
        self.btn_delete.clicked.connect(lambda: self.deleteRequested.emit(self.cid))

        layout.addWidget(self.thumb_label)
        layout.addLayout(text_layout, 1)
        layout.addWidget(self.btn_star)
        layout.addWidget(self.btn_delete)

        self.update_styles()

    def set_selected(self, selected: bool):
        self.is_selected = selected
        self.update_styles()

    def update_styles(self):
        if self.is_selected:
            self.name_label.setStyleSheet("color: #ffffff; font-size: 13px; font-weight: bold; background: transparent;")
            self.count_label.setStyleSheet("color: #d0e4ff; font-size: 11px; background: transparent;")
        else:
            self.name_label.setStyleSheet("color: #e2e4e8; font-size: 13px; font-weight: 500; background: transparent;")
            self.count_label.setStyleSheet("color: #727885; font-size: 11px; background: transparent;")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.rowClicked.emit(self.cid)
        super().mousePressEvent(event)


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
                min-height: 40px;
                padding: 0px 4px;
                border-radius: 6px;
                color: #e2e4e8;
                font-size: 13px;
                background-color: #1a1c22;
                border: 1px solid #2a2d36;
                margin-bottom: 4px;
            }
            QListWidget::item:hover {
                background-color: #242730;
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

        self.btn_create = ActionButton(
            normal_icon=_create_plus_icon("#d0d4dc", 32),
            hover_icon=_create_plus_icon("#ffffff", 32),
            tooltip="Создать новый мудборд под проект",
            parent=self
        )
        self.btn_create.setFixedSize(28, 28)
        self.btn_create.setIconSize(QSize(18, 18))
        self.btn_create.setStyleSheet("""
            QPushButton {
                background-color: #24272e;
                border: 1px solid #383c45;
                border-radius: 5px;
                padding: 0;
            }
            QPushButton:hover {
                background-color: #1976D2;
                border-color: #2196F3;
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
                count = col["asset_count"]

                item = QListWidgetItem()
                item.setSizeHint(QSize(0, 44))
                item.setData(Qt.ItemDataRole.UserRole, cid)
                item.setToolTip(f"{col['name']}\nИзображений: {count}" + ("\n(Quick Target: Ctrl+B)" if is_target else ""))

                self.list_widget.addItem(item)

                row_widget = CollectionRowWidget(
                    collection_data=col,
                    is_quick_target=is_target,
                    is_selected=(cid == self._current_selected_id),
                    parent=self.list_widget
                )
                row_widget.rowClicked.connect(self._on_row_clicked)
                row_widget.quickTargetToggled.connect(self._on_row_quick_target_toggled)
                row_widget.deleteRequested.connect(self._on_row_delete_requested)
                self.list_widget.setItemWidget(item, row_widget)

                if cid == self._current_selected_id:
                    self.list_widget.setCurrentItem(item)

                if is_target:
                    quick_target_name = col["name"]

            # Update hint
            if quick_target_name:
                self.hint_label.setText(f"Ctrl+B → {quick_target_name}\nИли перетащите кадры в этот блок.")
            else:
                self.hint_label.setText("Ctrl+B: назначьте Quick Target (★)\nИли перетащите кадры в этот блок.")

    def _on_row_clicked(self, cid: int):
        self._current_selected_id = cid
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == cid:
                self.list_widget.setCurrentItem(it)
                break
        self._update_rows_selected_state()
        self.btn_show_all.show()
        self.collectionSelected.emit(cid)

    def _on_item_clicked(self, item: QListWidgetItem):
        cid = item.data(Qt.ItemDataRole.UserRole)
        self._on_row_clicked(cid)

    def _update_rows_selected_state(self):
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            w = self.list_widget.itemWidget(it)
            if isinstance(w, CollectionRowWidget):
                w.set_selected(it.data(Qt.ItemDataRole.UserRole) == self._current_selected_id)

    def clear_selection(self):
        self._current_selected_id = None
        self.list_widget.clearSelection()
        self._update_rows_selected_state()
        self.btn_show_all.hide()
        self.collectionCleared.emit()

    def _on_create_clicked(self):
        dlg = QInputDialog(self)
        dlg.setWindowTitle("Новый мудборд")
        dlg.setLabelText("Название проекта / подборки:")
        dlg.setOkButtonText("Создать")
        dlg.setCancelButtonText("Отмена")
        dlg.setStyleSheet("""
            QInputDialog { background-color: #1a1b1e; color: #fff; }
            QLabel { color: #ddd; font-size: 13px; }
            QLineEdit { background-color: #121316; border: 1px solid #333; border-radius: 4px; padding: 6px; color: #fff; }
            QPushButton { background-color: #2a2d34; border: 1px solid #444; border-radius: 4px; padding: 6px 14px; color: #fff; }
            QPushButton:hover { background-color: #353942; }
        """)
        if dlg.exec() == QInputDialog.DialogCode.Accepted:
            name = dlg.textValue().strip()
            if name:
                try:
                    cid = self.repository.create_collection(name)
                    self.reload()
                    self.statusNotice.emit(f"Создан мудборд «{name}»")
                except Exception as e:
                    QMessageBox.warning(self, "Ошибка", str(e))

    def _on_row_quick_target_toggled(self, cid: int):
        current_target = self.repository.get_quick_target()
        col = self.repository.get_collection(cid)
        name = col["name"] if col else "Набор"
        if current_target == cid:
            self.repository.set_quick_target(None)
            self.statusNotice.emit(f"Quick Target снят с «{name}»")
        else:
            self.repository.set_quick_target(cid)
            self.statusNotice.emit(f"Quick Target назначен: «{name}» (Ctrl+B)")
        self.reload()

    def _on_row_delete_requested(self, cid: int):
        col = self.repository.get_collection(cid)
        if not col:
            return

        box = QMessageBox(self)
        box.setWindowTitle("Удалить мудборд?")
        box.setText(f"Удалить мудборд «{col['name']}»?\n\nВсе изображения останутся в общей библиотеке.")
        box.setIcon(QMessageBox.Icon.Question)
        btn_delete = box.addButton("Удалить", QMessageBox.ButtonRole.YesRole)
        btn_delete.setStyleSheet("""
            QPushButton {
                background-color: #c62828;
                color: #ffffff;
                font-weight: bold;
                border: 1px solid #e53935;
                border-radius: 4px;
                padding: 6px 14px;
            }
            QPushButton:hover {
                background-color: #d32f2f;
            }
        """)
        btn_cancel = box.addButton("Отмена", QMessageBox.ButtonRole.NoRole)
        btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: #2a2d34;
                color: #e0e0e0;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 6px 14px;
            }
            QPushButton:hover {
                background-color: #353942;
            }
        """)
        box.setDefaultButton(btn_cancel)
        box.exec()

        if box.clickedButton() == btn_delete:
            if self._current_selected_id == cid:
                self.clear_selection()
            self.repository.delete_collection(cid)
            self.reload()
            self.contentsChanged.emit()
            self.statusNotice.emit(f"Мудборд «{col['name']}» удален")

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
                msg = f"В набор «{col_name}» добавлено: {len(added)}"
                if len(asset_ids) > len(added):
                    msg += f" (уже были: {len(asset_ids) - len(added)})"
            else:
                msg = f"Все выбранные изображения ({len(asset_ids)}) уже есть в наборе «{col_name}»"

            # Auto-sync moodboard.html if project directory is bound
            if col and col.get("export_dir"):
                synced = sync_collection_web_moodboard(self.repository, collection_id)
                if synced:
                    msg += " (moodboard.html синхронизирован)"

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
                "Сначала выберите активный мудборд (нажмите иконку ★ в строке набора)."
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
            msg = f"Отменено добавление {len(added_ids)} кадров в «{name}»"

            # Auto-sync moodboard.html if project directory is bound
            if col and col.get("export_dir"):
                sync_collection_web_moodboard(self.repository, collection_id)
                msg += " (moodboard.html синхронизирован)"

            self.statusNotice.emit(msg)
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

        export_dir = col.get("export_dir")
        moodboard_file = Path(export_dir) / "moodboard.html" if export_dir else None
        has_moodboard = moodboard_file and moodboard_file.exists()

        if has_moodboard:
            act_open_html = menu.addAction("🌐 Открыть moodboard.html")
            act_open_dir = menu.addAction(f"📂 Открыть папку проекта ({Path(export_dir).name})")
            act_sync_html = menu.addAction("🔄 Синхронизировать moodboard.html")
            act_bind_dir = menu.addAction("📁 Сменить папку проекта…")
        elif export_dir and Path(export_dir).exists():
            act_open_html = None
            act_open_dir = menu.addAction(f"📂 Открыть папку проекта ({Path(export_dir).name})")
            act_sync_html = menu.addAction("🔄 Создать / Синхронизировать moodboard.html")
            act_bind_dir = menu.addAction("📁 Сменить папку проекта…")
        else:
            act_open_html = None
            act_open_dir = None
            act_sync_html = None
            act_bind_dir = menu.addAction("🌐 Создать moodboard.html в папке проекта…")

        act_export = menu.addAction("⚙️ Экспорт / Синхронизация…")

        menu.addSeparator()
        act_rename = menu.addAction("✏️ Переименовать…")
        act_clear_cover = menu.addAction("Сбросить обложку")
        act_clear_cover.setEnabled(bool(col.get("cover_asset_id")))

        menu.addSeparator()
        act_delete = menu.addAction("🗑 Удалить мудборд…")

        action = menu.exec(self.list_widget.viewport().mapToGlobal(pos))

        if action == act_target:
            self.repository.set_quick_target(cid)
            self.reload()
            self.statusNotice.emit(f"Quick Target назначен: «{col['name']}»")

        elif action == act_open_html and has_moodboard:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(moodboard_file)))

        elif action == act_open_dir and export_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(export_dir)))

        elif action == act_sync_html and export_dir:
            synced = sync_collection_web_moodboard(self.repository, cid)
            if synced:
                self.statusNotice.emit(f"moodboard.html синхронизирован в «{export_dir}»")
            else:
                QMessageBox.warning(self, "Ошибка синхронизации", "Не удалось обновить moodboard.html")

        elif action == act_bind_dir:
            initial_dir = export_dir or str(Path.home())
            chosen = QFileDialog.getExistingDirectory(
                self,
                "Выберите папку проекта (где создать moodboard.html)",
                initial_dir,
                QFileDialog.Option.ShowDirsOnly
            )
            if chosen:
                self.repository.remember_export_dir(cid, chosen)
                synced = sync_collection_web_moodboard(self.repository, cid, chosen)
                self.reload()
                self.statusNotice.emit(f"Создан moodboard.html в: {chosen}")
                if synced and synced.exists():
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(synced)))

        elif action == act_export:
            dlg = ExportCollectionDialog(cid, self.repository, self)
            dlg.exec()

        elif action == act_rename:
            dlg = QInputDialog(self)
            dlg.setWindowTitle("Переименовать мудборд")
            dlg.setLabelText("Новое название:")
            dlg.setTextValue(col["name"])
            dlg.setOkButtonText("Сохранить")
            dlg.setCancelButtonText("Отмена")
            dlg.setStyleSheet("""
                QInputDialog { background-color: #1a1b1e; color: #fff; }
                QLabel { color: #ddd; font-size: 13px; }
                QLineEdit { background-color: #121316; border: 1px solid #333; border-radius: 4px; padding: 6px; color: #fff; }
                QPushButton { background-color: #2a2d34; border: 1px solid #444; border-radius: 4px; padding: 6px 14px; color: #fff; }
                QPushButton:hover { background-color: #353942; }
            """)
            if dlg.exec() == QInputDialog.DialogCode.Accepted:
                new_name = dlg.textValue().strip()
                if new_name:
                    try:
                        self.repository.rename_collection(cid, new_name)
                        self.reload()
                        self.statusNotice.emit(f"Мудборд переименован в «{new_name}»")
                    except Exception as e:
                        QMessageBox.warning(self, "Ошибка", str(e))

        elif action == act_clear_cover:
            self.repository.set_cover(cid, None)
            self.reload()

        elif action == act_delete:
            self._on_row_delete_requested(cid)
