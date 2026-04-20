import os
import logging
from PyQt6.QtWidgets import QListView, QAbstractItemView, QStyledItemDelegate, QStyle, QMessageBox, QMenu
from PyQt6.QtCore import QThreadPool, pyqtSlot, QSize, Qt, QRectF, QModelIndex, QEvent, QPoint
from PyQt6.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QPainterPath

from ui.widgets.lazy_model import AssetListModel
from ui.workers.image_loader import ImageLoaderWorker
from ui.widgets.image_viewer import ImageViewerWindow

logger = logging.getLogger(__name__)

class GalleryDelegate(QStyledItemDelegate):
    """Кастомный делегат — центрирует изображение, добавляет эффекты наведения и кнопки."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.btn_size = 28
        self.spacing = 8
        self.hovered_index = None

    def paint(self, painter: QPainter, option, index):
        painter.save()

        # Получаем данные ассета
        model = index.model()
        asset = index.data(Qt.ItemDataRole.UserRole)
        is_hovered = option.state & QStyle.StateFlag.State_MouseOver

        # Фон ячейки
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor("#3a3a3a"))
        else:
            painter.fillRect(option.rect, QColor("#121212"))

        # Прямоугольник для картинки
        cell = option.rect.adjusted(8, 8, -8, -8)

        # Рисуем изображение по центру с сохранением пропорций
        pixmap = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            scaled = pixmap.scaled(
                cell.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation
            )
            # Clip path for rounded corners
            path = QPainterPath()
            path.addRoundedRect(QRectF(cell), 8, 8)
            painter.setClipPath(path)
            
            # Center it
            x = cell.x() + (cell.width() - scaled.width()) // 2
            y = cell.y() + (cell.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
            
            painter.setClipping(False) # Remove clip for overlay icons
        else:
            # Placeholder
            painter.fillRect(cell, QColor("#2d2d2d"))

        # Отрисовка кнопок при наведении
        if is_hovered and asset:
            self.hovered_index = index
            painter.fillRect(cell, QColor(0, 0, 0, 100)) # Dark overlay
            
            fav_rect, del_rect = self._get_button_rects(option.rect)

            # Draw Fav Button
            fav_color = QColor("#ffeb3b") if asset.is_favorite else QColor(255, 255, 255, 180)
            self._draw_circle_btn(painter, fav_rect, fav_color, "⭐" if asset.is_favorite else "☆")

            # Draw Delete Button
            self._draw_circle_btn(painter, del_rect, QColor(244, 67, 54, 180), "🗑")

        painter.restore()

    def _draw_circle_btn(self, painter, rect, bg_color, text):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(40, 40, 40, 200))
        painter.drawEllipse(rect)
        
        painter.setPen(bg_color)
        font = painter.font()
        font.setPixelSize(14)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _get_button_rects(self, option_rect):
        # Top right corner with some padding
        top_right_x = option_rect.right() - 8 - self.btn_size
        top_right_y = option_rect.top() + 8
        
        del_rect = QRectF(top_right_x, top_right_y, self.btn_size, self.btn_size)
        fav_rect = QRectF(top_right_x - self.btn_size - self.spacing, top_right_y, self.btn_size, self.btn_size)
        
        return fav_rect, del_rect

    def editorEvent(self, event, model, option, index):
        """Перехват кликов по иконкам, чтобы не срабатывало выделение/открытие."""
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            asset = index.data(Qt.ItemDataRole.UserRole)
            if not asset:
                return False

            fav_rect, del_rect = self._get_button_rects(option.rect)
            pos = event.position()
            
            if fav_rect.contains(pos):
                if event.type() == QEvent.Type.MouseButtonRelease:
                    # Parent view will handle the logic via custom signals ideally, 
                    # but we can call a method on the view if we cast parent()
                    view = self.parent()
                    if hasattr(view, '_toggle_favorite'):
                        view._toggle_favorite(asset)
                return True # Event handled

            elif del_rect.contains(pos):
                if event.type() == QEvent.Type.MouseButtonRelease:
                    view = self.parent()
                    if hasattr(view, '_delete_asset'):
                        view._delete_asset(asset)
                return True

        return super().editorEvent(event, model, option, index)

    def sizeHint(self, option, index):
        return QSize(220, 220)


class GalleryView(QListView):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.thread_pool = QThreadPool.globalInstance()

        # Flow layout mode
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setSpacing(4)
        self.setUniformItemSizes(True)
        self.setGridSize(QSize(220, 220))
        self.setIconSize(QSize(200, 200))

        # Настраиваем мышь для hover эффектов
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover)

        # Кастомный делегат
        self.setItemDelegate(GalleryDelegate(self))

        # Interaction
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("""
            QListView { background-color: #121212; border: none; outline: none; }
            QScrollBar:vertical {
                border: none;
                background: #121212;
                width: 10px;
                margin: 0px 0px 0px 0px;
            }
            QScrollBar::handle:vertical {
                background: #333;
                min-height: 20px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical:hover { background: #555; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
        """)

        # Single-click -> open full-size viewer
        self.clicked.connect(self._on_item_clicked)
        
        # Right-click context menu
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        self._viewer_window: ImageViewerWindow | None = None
        self.db = None
        self.parent_window = None

    def setModel(self, model: AssetListModel):
        old_model = self.model()
        if old_model and isinstance(old_model, AssetListModel):
            old_model.loadRequested.disconnect(self._on_load_requested)
            
        super().setModel(model)
        model.loadRequested.connect(self._on_load_requested)

    @pyqtSlot(int, str)
    def _on_load_requested(self, asset_id: int, file_path: str):
        worker = ImageLoaderWorker(asset_id, file_path)
        worker.signals.loaded.connect(self._on_image_loaded)
        worker.signals.error.connect(self._on_image_error)
        self.thread_pool.start(worker)

    @pyqtSlot(int, QImage)
    def _on_image_loaded(self, asset_id: int, image: QImage):
        pixmap = QPixmap.fromImage(image)
        model = self.model()
        if isinstance(model, AssetListModel):
            model.setImage(asset_id, pixmap)

    @pyqtSlot(int, str)
    def _on_image_error(self, asset_id: int, error_msg: str):
        logger.error(f"Asset {asset_id} image load failure: {error_msg}")

    @pyqtSlot(QModelIndex)
    def _on_item_clicked(self, index: QModelIndex):
        """Открыть полноэкранный просмотрщик при ПЕРВОМ клике."""
        if not index.isValid():
            return

        model = self.model()
        if not isinstance(model, AssetListModel):
            return

        # Собираем видимые ассеты
        assets = model.assets.copy()
        start_idx = index.row()

        if self._viewer_window is None:
            self._viewer_window = ImageViewerWindow()
        self._viewer_window.set_assets(assets, start_idx)
        self._viewer_window.show()
        self._viewer_window.raise_()
        self._viewer_window.activateWindow()

    def _on_context_menu(self, pos):
        index = self.indexAt(pos)
        if not index.isValid():
            return
        
        model = self.model()
        if not isinstance(model, AssetListModel):
            return
        
        asset = model.assets[index.row()]
        menu = QMenu(self)
        
        is_fav = False
        if self.db:
            is_fav = self.db.is_favorite(asset.id)
        
        fav_action = menu.addAction("⭐ В избранное" if not is_fav else "💔 Убрать из избранного")
        fav_action.triggered.connect(lambda: self._toggle_favorite(asset))
        
        menu.addSeparator()
        
        delete_action = menu.addAction("🗑️ Удалить")
        delete_action.triggered.connect(lambda: self._delete_asset(asset))
        
        menu.exec(self.viewport().mapToGlobal(pos))

    def _toggle_favorite(self, asset):
        if not self.db: return
        new_status = self.db.toggle_favorite(asset.id)
        asset.is_favorite = new_status
        
        # Trigger redraw
        self.viewport().update()

        status = "добавлен в" if new_status else "убран из"
        if self.parent_window:
            self.parent_window.status_label.setText(f"⭐ Ассет #{asset.id} {status} избранного")

    def _delete_asset(self, asset):
        if not self.db: return
        
        reply = QMessageBox.question(
            self, "Удалить ассет",
            f"Удалить ассет #{asset.id}?\n\nЭто действие необратимо.\nФайл будет удалён с диска.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            if asset.original_url:
                self.db.mark_as_deleted(asset.original_url, reason="user_deleted", phash=asset.phash)
            
            if asset.thumbnail_path and os.path.exists(asset.thumbnail_path):
                try:
                    os.remove(asset.thumbnail_path)
                except Exception as e:
                    logger.warning(f"Failed to delete file: {e}")
            
            with self.db.get_connection() as conn:
                conn.execute("DELETE FROM asset_tags WHERE asset_id = ?", (asset.id,))
                conn.execute("DELETE FROM assets WHERE id = ?", (asset.id,))
                conn.commit()
            
            logger.info(f"Deleted asset #{asset.id}")
            
            if self.parent_window:
                self.parent_window._load_assets_for_gallery()
                self.parent_window._refresh_library()
                self.parent_window.status_label.setText(f"🗑️ Ассет #{asset.id} удалён")
