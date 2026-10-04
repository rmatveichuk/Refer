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
                Qt.AspectRatioMode.KeepAspectRatio,
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
            
            # Draw Delete Button
            # We only show delete button on hover now
            top_right_x = option.rect.right() - 8 - self.btn_size
            top_right_y = option.rect.top() + 8
            del_rect = QRectF(top_right_x, top_right_y, self.btn_size, self.btn_size)
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

    def get_delete_button_rect(self, rect):
        top_right_x = rect.right() - 8 - self.btn_size
        top_right_y = rect.top() + 8
        return QRectF(top_right_x, top_right_y, self.btn_size, self.btn_size)

    def editorEvent(self, event, model, option, index):
        """Перехват кликов по иконкам, чтобы не срабатывало стандартное поведение ячейки."""
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            del_rect = self.get_delete_button_rect(option.rect)
            if del_rect.adjusted(-4, -4, 4, 4).contains(event.position()):
                return True
        return super().editorEvent(event, model, option, index)

    def sizeHint(self, option, index):
        return QSize(220, 220)


class GalleryView(QListView):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.thread_pool = QThreadPool(self)
        self.thread_pool.setMaxThreadCount(4)
        self.verticalScrollBar().valueChanged.connect(lambda _value: self._retry_thumbnails())

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
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
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

        # Plain click opens; Ctrl/Shift retain normal multiple selection.
        self.clicked.connect(self._on_item_clicked)
        self.setToolTip("Клик — рассмотреть изображение; Ctrl/Shift + клик — выделить несколько; пробел — открыть")
        
        # Right-click context menu
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        self._viewer_window: ImageViewerWindow | None = None
        self.db = None
        self.parent_window = None
        self._delete_pressed_index = None
        self._delete_in_progress = False

    def _get_delete_button_rect(self, index: QModelIndex):
        if not index.isValid():
            return None
        rect = self.visualRect(index)
        delegate = self.itemDelegate()
        if hasattr(delegate, 'get_delete_button_rect'):
            return delegate.get_delete_button_rect(rect)
        return QRectF(rect.right() - 8 - 28, rect.top() + 8, 28, 28)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            index = self.indexAt(pos.toPoint())
            del_rect = self._get_delete_button_rect(index)
            if del_rect and del_rect.adjusted(-4, -4, 4, 4).contains(pos):
                self._delete_pressed_index = index
                event.accept()
                return
        self._delete_pressed_index = None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and getattr(self, '_delete_pressed_index', None) is not None:
            pressed_index = self._delete_pressed_index
            self._delete_pressed_index = None
            pos = event.position()
            index = self.indexAt(pos.toPoint())
            del_rect = self._get_delete_button_rect(pressed_index)
            # Если отпустили на кнопке корзины или в пределах того же элемента
            if del_rect and (del_rect.adjusted(-6, -6, 6, 6).contains(pos) or (index.isValid() and index.row() == pressed_index.row())):
                asset = pressed_index.data(Qt.ItemDataRole.UserRole)
                if asset:
                    self._delete_in_progress = True
                    try:
                        self._delete_asset(asset)
                    finally:
                        self._delete_in_progress = False
                event.accept()
                return
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def setModel(self, model: AssetListModel):
        old_model = self.model()
        if old_model and isinstance(old_model, AssetListModel):
            old_model.cancel_loads()
            old_model.loadRequested.disconnect(self._on_load_requested)
            old_model.loadCapacityAvailable.disconnect(self.viewport().update)
            
        super().setModel(model)
        model.loadRequested.connect(self._on_load_requested)
        model.loadCapacityAvailable.connect(self.viewport().update)

    @pyqtSlot(int, str, int)
    def _on_load_requested(self, asset_id: int, file_path: str, generation: int):
        worker = ImageLoaderWorker(asset_id, file_path, generation=generation,
                                   cancellation=self.model().cancellation_for(generation))
        worker.signals.completed.connect(self._on_image_completed)
        self.thread_pool.start(worker)

    @pyqtSlot(int, int, QImage, str)
    def _on_image_completed(self, asset_id: int, generation: int, image: QImage, error: str):
        model = self.model()
        if isinstance(model, AssetListModel):
            model.completeImage(asset_id, generation, QPixmap.fromImage(image) if not image.isNull() else None)
        if error:
            logger.warning("Asset %s thumbnail: %s", asset_id, error)

    def _retry_thumbnails(self):
        model = self.model()
        if isinstance(model, AssetListModel):
            model.retry_evicted()

    def resizeEvent(self, event):
        self._retry_thumbnails()
        super().resizeEvent(event)

    def paintEvent(self, event):
        model = self.model()
        if isinstance(model, AssetListModel):
            viewport = self.viewport().rect()
            visible = [asset.id for row, asset in enumerate(model.assets)
                       if self.visualRect(model.index(row, 0)).intersects(viewport)]
            model.set_visible_assets(visible)
        super().paintEvent(event)

    @pyqtSlot(QModelIndex)
    def _on_item_clicked(self, index: QModelIndex):
        """Open the viewer from a plain click, Space or the context menu."""
        if not index.isValid() or getattr(self, '_delete_in_progress', False) or getattr(self, '_delete_pressed_index', None) is not None:
            return

        # Если зажаты Shift или Ctrl, мы просто выделяем объекты, не открывая просмотрщик
        from PyQt6.QtWidgets import QApplication
        modifiers = QApplication.keyboardModifiers()
        if modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            return

        model = self.model()
        if not isinstance(model, AssetListModel):
            return

        # Собираем видимые ассеты
        assets = model.assets.copy()
        start_idx = index.row()

        if self._viewer_window is None:
            self._viewer_window = ImageViewerWindow(self.db, self.parent_window)
        self._viewer_window.set_assets(assets, start_idx)
        self._viewer_window.show()
        self._viewer_window.raise_()
        self._viewer_window.activateWindow()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space and self.currentIndex().isValid():
            self._on_item_clicked(self.currentIndex())
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            model = self.model()
            if isinstance(model, AssetListModel):
                selected_indexes = self.selectionModel().selectedIndexes()
                if selected_indexes:
                    selected_assets = [model.assets[idx.row()] for idx in selected_indexes if idx.row() < len(model.assets)]
                    if selected_assets:
                        self._delete_assets(selected_assets)
                        event.accept()
                        return
                elif self.currentIndex().isValid():
                    idx = self.currentIndex().row()
                    if 0 <= idx < len(model.assets):
                        self._delete_asset(model.assets[idx])
                        event.accept()
                        return
        super().keyPressEvent(event)

    def set_thumbnail_size(self, size):
        self._retry_thumbnails()
        self.setGridSize(QSize(size, size))
        self.setIconSize(QSize(size - 16, size - 16))
        self.viewport().update()

    def _on_context_menu(self, pos):
        index = self.indexAt(pos)
        if not index.isValid():
            return
        
        model = self.model()
        if not isinstance(model, AssetListModel):
            return
            
        menu = QMenu(self)
        
        # Check if multiple items are selected
        selected_indexes = self.selectionModel().selectedIndexes()
        
        if len(selected_indexes) > 1:
            selected_assets = [model.assets[idx.row()] for idx in selected_indexes]
            
            ai_menu = menu.addMenu("🤖 ИИ-анализ")
            
            # 1. Все выбранные
            all_action = ai_menu.addAction(f"Анализировать все выбранные ({len(selected_assets)})")
            all_action.triggered.connect(lambda: self._batch_ai_analyze(selected_assets))
            
            # 2. Только Веб (где есть URL)
            web_assets = [a for a in selected_assets if a.original_url and a.original_url.startswith('http')]
            if web_assets and len(web_assets) < len(selected_assets):
                web_action = ai_menu.addAction(f"Только веб-изображения ({len(web_assets)})")
                web_action.triggered.connect(lambda: self._batch_ai_analyze(web_assets))
            
            # 3. Только без описания
            # Note: asset objects in model might not have description updated, so we check DB status
            # But for simplicity let's just add the action and filter inside _batch_ai_analyze or here
            needed_assets = [a for a in selected_assets if not getattr(a, 'description', None)]
            if needed_assets and len(needed_assets) > 0:
                needed_action = ai_menu.addAction(f"Только без описания ({len(needed_assets)})")
                needed_action.triggered.connect(lambda: self._batch_ai_analyze(needed_assets))

            menu.addSeparator()
            
            delete_action = menu.addAction(f"Скрыть из библиотеки ({len(selected_assets)})")
            delete_action.triggered.connect(lambda: self._delete_assets(selected_assets))
        else:
            asset = model.assets[index.row()]

            view_action = menu.addAction("Рассмотреть изображение")
            view_action.triggered.connect(lambda: self._on_item_clicked(index))
            favorite_action = menu.addAction("Убрать из избранного" if asset.is_favorite else "В избранное")
            favorite_action.triggered.connect(lambda: self._toggle_favorite(asset))
            if asset.local_path or asset.thumbnail_path:
                folder_action = menu.addAction("Открыть папку изображения")
                folder_action.triggered.connect(lambda: self._open_image_folder(asset))
            menu.addSeparator()
            
            ai_action = menu.addAction("🤖 ИИ-анализ (LM Studio)")
            ai_action.triggered.connect(lambda: self._batch_ai_analyze([asset]))
            
            menu.addSeparator()
            
            delete_action = menu.addAction("Скрыть из библиотеки")
            delete_action.triggered.connect(lambda: self._delete_asset(asset))
        
        menu.exec(self.viewport().mapToGlobal(pos))

    def _open_image_folder(self, asset):
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        path = asset.local_path or asset.thumbnail_path
        folder = os.path.dirname(os.path.abspath(path)) if path else ""
        if not folder or not os.path.isdir(folder):
            QMessageBox.information(self, "Папка недоступна", "Проверьте подключение диска и расположение изображения.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _toggle_favorite(self, asset):
        if not self.db: return
        new_status = self.db.toggle_favorite(asset.id)
        asset.is_favorite = new_status
        
        # Trigger redraw
        self.viewport().update()

        status = "добавлен в" if new_status else "убран из"
        if self.parent_window:
            self.parent_window.status_label.setText(f"⭐ Ассет #{asset.id} {status} избранного")

    def _delete_assets(self, assets):
        if not self.db: return
        if self.parent_window and hasattr(self.parent_window, "_delete_assets_batch"):
            self.parent_window._delete_assets_batch(assets)
            
    def _delete_asset(self, asset):
        self._delete_assets([asset])

    def _batch_ai_analyze(self, assets):
        """Passes the request to the main window to handle background processing."""
        if self.parent_window and hasattr(self.parent_window, "start_batch_ai_analysis"):
            self.parent_window.start_batch_ai_analysis(assets)
        else:
            QMessageBox.information(self, "Анализ недоступен", "Откройте изображения в основном окне приложения.")
