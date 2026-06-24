from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QToolBar, QStatusBar, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QMessageBox
)
from PyQt6.QtCore import Qt, QRectF, QSize, pyqtSignal
from PyQt6.QtGui import QPixmap, QPainter, QImage, QWheelEvent, QMouseEvent, QKeyEvent, QKeySequence, QFont
from PyQt6.QtWidgets import QDockWidget, QTextEdit

import logging
import config

logger = logging.getLogger(__name__)


class ZoomableImageView(QGraphicsView):
    """Вьювер с зумом (колёсико), панорамированием (перетаскивание) и сбросом."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._is_first_fit = True

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._zoom_level = 1.0

    def set_pixmap(self, pixmap: QPixmap):
        self._scene.clear()
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._is_first_fit = True
        self.fit_in_view()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # If we haven't manually zoomed or if it's the initial display, keep fitting to window
        if self._zoom_level == 1.0 or self._is_first_fit:
            self.fit_in_view()

    def fit_in_view(self):
        if self._pixmap_item:
            self.setSceneRect(self._pixmap_item.boundingRect())
            self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
            self._is_first_fit = False # Reset fit flag after first successful fit

    def reset_zoom(self):
        self.fit_in_view()

    def zoom_in(self):
        self.scale(1.25, 1.25)
        self._zoom_level *= 1.25

    def zoom_out(self):
        self.scale(0.8, 0.8)
        self._zoom_level *= 0.8

    def wheelEvent(self, event: QWheelEvent):
        if event.angleDelta().y() > 0:
            self.zoom_in()
        else:
            self.zoom_out()




class ImageViewerWindow(QMainWindow):
    """Окно просмотра изображения в полном размере."""

    def __init__(self, db=None, parent_window=None):
        super().__init__()
        self.db = db
        self.parent_window = parent_window
        self.setWindowTitle("Refer — Image Viewer")
        self.resize(1200, 800)
        self.setStyleSheet("background-color: #0a0a0a;")

        # Данные
        self.assets: list = []
        self.current_index: int = -1

        # Центральный виджет
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        self.viewer = ZoomableImageView(self)
        layout.addWidget(self.viewer)
        self.setCentralWidget(central)


        # Описание от ИИ в Dock-панели (снизу)
        self.dock = QDockWidget("ИИ Описание" if config.CURRENT_LANGUAGE == "ru" else "AI Description", self)
        self.dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.TopDockWidgetArea)
        self.desc_view = QTextEdit()
        self.desc_view.setReadOnly(True)
        self.desc_view.setStyleSheet("""
            QTextEdit {
                background-color: #121212; color: #eee; border: none;
                padding: 15px; font-size: 14px; line-height: 1.5;
                font-family: 'Segoe UI', 'Roboto', sans-serif;
            }
        """)
        self.dock.setWidget(self.desc_view)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock)
        self.dock.setVisible(False)

        # Тулбар
        toolbar = QToolBar()
        toolbar.setStyleSheet("""
            QToolBar { background-color: #1a1a1a; border-bottom: 1px solid #222; padding: 4px; spacing: 8px; }
            QPushButton { 
                background-color: #252525; color: #888; border: 1px solid #333; border-radius: 4px; padding: 6px 14px; font-size: 13px; 
            }
            QPushButton:hover { background-color: #333; color: #ccc; border-color: #444; }
            QPushButton:pressed { background-color: #111; }
            QPushButton:checked { background-color: #dcdcdc; color: #000; border: none; }
        """)
        self.addToolBar(toolbar)

        self.btn_prev = QPushButton("◀  Prev")
        self.btn_prev.setShortcut(QKeySequence(Qt.Key.Key_Left))
        self.btn_prev.clicked.connect(self.show_prev)
        toolbar.addWidget(self.btn_prev)

        self.btn_next = QPushButton("Next  ▶")
        self.btn_next.setShortcut(QKeySequence(Qt.Key.Key_Right))
        self.btn_next.clicked.connect(self.show_next)
        toolbar.addWidget(self.btn_next)

        toolbar.addSeparator()

        self.btn_fit = QPushButton("⊞  Fit")
        self.btn_fit.setShortcut(QKeySequence(Qt.Key.Key_F))
        self.btn_fit.clicked.connect(self.viewer.reset_zoom)
        toolbar.addWidget(self.btn_fit)

        self.btn_zoom_in = QPushButton("🔍+")
        self.btn_zoom_in.setShortcut(QKeySequence(Qt.Key.Key_Equal))
        self.btn_zoom_in.clicked.connect(self.viewer.zoom_in)
        toolbar.addWidget(self.btn_zoom_in)

        self.btn_zoom_out = QPushButton("🔍−")
        self.btn_zoom_out.setShortcut(QKeySequence(Qt.Key.Key_Minus))
        self.btn_zoom_out.clicked.connect(self.viewer.zoom_out)
        toolbar.addWidget(self.btn_zoom_out)

        toolbar.addSeparator()

        self.btn_open_url = QPushButton("🌐  Open URL")
        self.btn_open_url.setShortcut(QKeySequence(Qt.Key.Key_U))
        self.btn_open_url.clicked.connect(self._open_in_browser)
        toolbar.addWidget(self.btn_open_url)

        self.btn_open_folder = QPushButton("📁 В папке")
        self.btn_open_folder.clicked.connect(self._open_in_folder)
        toolbar.addWidget(self.btn_open_folder)

        toolbar.addSeparator()

        btn_project_label = "📁 Проект" if config.CURRENT_LANGUAGE == "ru" else "📁 Project"
        btn_author_label = "👤 Автор" if config.CURRENT_LANGUAGE == "ru" else "👤 Author"

        self.btn_filter_project = QPushButton(btn_project_label)
        self.btn_filter_project.clicked.connect(self._filter_by_project)
        toolbar.addWidget(self.btn_filter_project)

        self.btn_filter_author = QPushButton(btn_author_label)
        self.btn_filter_author.clicked.connect(self._filter_by_author)
        toolbar.addWidget(self.btn_filter_author)

        toolbar.addSeparator()

        self.btn_fav = QPushButton("☆ В избранное")
        self.btn_fav.clicked.connect(self._toggle_favorite)
        toolbar.addWidget(self.btn_fav)

        self.btn_ai = QPushButton("🤖 ИИ-Анализ")
        self.btn_ai.clicked.connect(self._trigger_ai_analysis)
        toolbar.addWidget(self.btn_ai)

        self.btn_delete = QPushButton("🗑 Удалить")
        self.btn_delete.setStyleSheet("""
            QPushButton:hover { background-color: #a83232; color: white; border-color: #cc4444; }
        """)
        self.btn_delete.setShortcut(QKeySequence(Qt.Key.Key_Delete))
        self.btn_delete.clicked.connect(self._delete_current_asset)
        toolbar.addWidget(self.btn_delete)

        self.btn_toggle_desc = QPushButton("📝 Текст")
        self.btn_toggle_desc.setCheckable(True)
        self.btn_toggle_desc.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_desc.toggled.connect(self.dock.setVisible)
        self.dock.visibilityChanged.connect(self.btn_toggle_desc.setChecked)
        self.btn_toggle_desc_action = toolbar.addWidget(self.btn_toggle_desc)
        self.btn_toggle_desc_action.setVisible(False) # Скрыта по умолчанию

        # Статус-бар
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.setStyleSheet("QStatusBar { background-color: #1a1a1a; color: #888; }")

        # Загружаем полное изображение в фоне
        from PyQt6.QtCore import QThreadPool
        from ui.workers.image_loader import ImageLoaderWorker
        self._thread_pool = QThreadPool.globalInstance()

        self._key_pressed = False

    def resizeEvent(self, event):
        super().resizeEvent(event)

    def set_assets(self, assets: list, start_index: int):
        """Открыть viewer, assets — список Asset, start_index — какой открыть."""
        self.assets = assets
        self.current_index = start_index
        self._load_full_image()

    def _load_full_image(self):
        if self.current_index < 0 or self.current_index >= len(self.assets):
            return

        asset = self.assets[self.current_index]
        thumb_path = asset.thumbnail_path

        # Обновляем статус
        cat_label = "📸 Photo" if asset.category == "photography" else "🏛 3D Render"
        self.status.showMessage(
            f"{cat_label}  |  {asset.width}×{asset.height}  |  {asset.original_url[:100]}...",
            0
        )
        self.setWindowTitle(f"Refer — {asset.original_url.split('/')[-1]}  ({self.current_index + 1}/{len(self.assets)})")

        self._update_ui_state()

        import os
        # Нормализуем путь
        path = thumb_path
        if path.startswith('file:///'):
            path = path[8:]
        path = os.path.normpath(path)

        # Загружаем полное изображение
        from PyQt6.QtGui import QImage
        image = QImage(path)
        if not image.isNull():
            pixmap = QPixmap.fromImage(image)
            self.viewer.set_pixmap(pixmap)
        else:
            logger.warning(f"Cannot load full image: {path}")

    def show_prev(self):
        if self.current_index > 0:
            self.current_index -= 1
            self._load_full_image()

    def show_next(self):
        if self.current_index < len(self.assets) - 1:
            self.current_index += 1
            self._load_full_image()

    def _open_in_browser(self):
        if self.current_index < 0 or self.current_index >= len(self.assets):
            return
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl(self.assets[self.current_index].original_url))

    def _open_in_folder(self):
        if self.current_index < 0 or self.current_index >= len(self.assets):
            return
        import subprocess
        import platform
        import os
        
        asset = self.assets[self.current_index]
        
        # Для веб-картинок (Behance/ArchDaily) приоритет отдаем скачанной миниатюре
        # Для локальных файлов local_path и thumbnail_path обычно одинаковы
        path = asset.thumbnail_path if asset.thumbnail_path else asset.local_path
        
        # Если вообще ничего нет, пробуем original_url как фолбэк для локальных файлов
        if not path:
            path = asset.original_url
            
        if path and path.startswith('file:///'):
            path = path[8:]
            
        if not path:
            logger.warning("Нет пути для открытия папки")
            return
            
        # На всякий случай нормализуем путь для Windows
        path = os.path.normpath(path)
            
        if os.path.exists(path):
            if platform.system() == "Windows":
                # Возвращаем самый надежный метод (SHOpenFolderAndSelectItems).
                # Он гарантированно открывает папку и выделяет файл, 
                # даже если скролл иногда позиционирует его внизу экрана.
                try:
                    import ctypes
                    import ctypes.wintypes
                    
                    # Нормализуем путь
                    abs_path = os.path.abspath(path)
                    
                    # Пытаемся использовать shell32
                    shell32 = ctypes.windll.shell32
                    co_initialize = ctypes.windll.ole32.CoInitialize
                    co_uninitialize = ctypes.windll.ole32.CoUninitialize
                    
                    # Инициализируем COM
                    co_initialize(None)
                    
                    # ILCreateFromPathW создает Item ID List (PIDL)
                    pidl = shell32.ILCreateFromPathW(abs_path)
                    if pidl:
                        # Открываем папку и выделяем файл 
                        # Используем флаг 1 (OFASI_EDIT), чтобы заставить Windows 
                        # прокрутить список так, чтобы файл был полностью виден
                        shell32.SHOpenFolderAndSelectItems(pidl, 1, None, 0)
                        ctypes.windll.shell32.ILFree(pidl)
                    else:
                        subprocess.Popen(['explorer', '/select,', abs_path])
                        
                    co_uninitialize()
                    
                except Exception as e:
                    logger.warning(f"Failed to use SHOpenFolderAndSelectItems: {e}")
                    subprocess.Popen(['explorer', '/select,', path])
                    
            elif platform.system() == "Darwin": # macOS
                subprocess.run(['open', '-R', path])
            else: # Linux
                subprocess.run(['xdg-open', os.path.dirname(path)])
        else:
            logger.warning(f"Файл не найден на диске: {path}")

    def _update_ui_state(self):
        if self.current_index < 0 or self.current_index >= len(self.assets):
            return
            
        asset = self.assets[self.current_index]
        if self.db:
            asset.is_favorite = self.db.is_favorite(asset.id)
            desc = self.db.get_description(asset.id)
            
            if asset.is_favorite:
                self.btn_fav.setText("★ В избранном")
                self.btn_fav.setStyleSheet("background-color: #dcdcdc; color: #000; border: none;")
            else:
                self.btn_fav.setText("☆ В избранное")
                self.btn_fav.setStyleSheet("")
                
            if desc and desc.strip():
                self.desc_view.setMarkdown(desc)
                self.btn_toggle_desc_action.setVisible(True)
            else:
                self.desc_view.clear()
                self.btn_toggle_desc_action.setVisible(False)
                self.btn_toggle_desc.setChecked(False)
                self.dock.setVisible(False)

            # Проверяем наличие проекта и автора для показа кнопок фильтрации
            project_id = asset.project_id
            project_found = False
            author_found = False
            if project_id:
                try:
                    with self.db.get_connection() as conn:
                        cur = conn.cursor()
                        cur.execute("SELECT title, author FROM projects WHERE id = ?", (project_id,))
                        p_row = cur.fetchone()
                        if p_row:
                            project_found = True
                            title = p_row['title'] or f"Project #{project_id}"
                            self.btn_filter_project.setToolTip(f"Показать все работы проекта: {title}" if config.CURRENT_LANGUAGE == "ru" else f"Show all project works: {title}")
                            author_name = p_row['author']
                            if author_name and author_name.strip():
                                author_found = True
                                self.btn_filter_author.setToolTip(f"Показать все работы автора: {author_name}" if config.CURRENT_LANGUAGE == "ru" else f"Show all works by: {author_name}")
                except Exception as e:
                    logger.error(f"Error querying project in viewer: {e}")
            
            self.btn_filter_project.setVisible(project_found)
            self.btn_filter_author.setVisible(author_found)

    def _toggle_favorite(self):
        if not self.db or self.current_index < 0: return
        asset = self.assets[self.current_index]
        new_status = self.db.toggle_favorite(asset.id)
        asset.is_favorite = new_status
        self._update_ui_state()
        
        # Обновляем UI в главном окне
        if self.parent_window and hasattr(self.parent_window, "library_table"):
            # It's better to tell the gallery to refresh
            if hasattr(self.parent_window, "search_panel"):
                # Ideally emit a signal, for now we can just rely on the user refreshing 
                pass

    def _trigger_ai_analysis(self):
        if self.current_index < 0 or not self.parent_window: return
        asset = self.assets[self.current_index]
        if hasattr(self.parent_window, "start_batch_ai_analysis"):
            self.parent_window.start_batch_ai_analysis([asset])
            self.desc_view.setPlainText("⏳ ИИ анализирует изображение... Пожалуйста, подождите.")
            self.btn_toggle_desc_action.setVisible(True)
            self.btn_toggle_desc.setChecked(True)
            self.dock.setVisible(True)
            self.status.showMessage("⏳ Отправлено на ИИ-анализ...", 3000)

    def _delete_current_asset(self):
        if self.current_index < 0 or not self.parent_window: return
        asset = self.assets[self.current_index]
        
        if hasattr(self.parent_window, "_delete_assets_batch"):
            if self.parent_window._delete_assets_batch([asset]):
                # Remove from local list and move to next or previous
                self.assets.pop(self.current_index)
                if not self.assets:
                    self.close()
                else:
                    if self.current_index >= len(self.assets):
                        self.current_index = len(self.assets) - 1
                    self._load_full_image()

    def update_description(self, asset_id: int, description: str):
        """Called externally when AI analysis finishes."""
        if self.current_index >= 0 and self.assets[self.current_index].id == asset_id:
            self._update_ui_state()

    def showEvent(self, event):
        super().showEvent(event)
        # Ensure image fits when window is shown for the first time
        # We use a tiny delay to ensure layout is fully calculated
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(50, self.viewer.fit_in_view)

    def keyPressEvent(self, event: QKeyEvent):
        self._key_pressed = True
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        elif event.key() == Qt.Key.Key_Left:
            self.show_prev()
        elif event.key() == Qt.Key.Key_Right:
            self.show_next()
        elif event.key() == Qt.Key.Key_F:
            self.viewer.reset_zoom()
        elif event.key() == Qt.Key.Key_Equal:
            self.viewer.zoom_in()
        elif event.key() == Qt.Key.Key_Minus:
            self.viewer.zoom_out()
        elif event.key() == Qt.Key.Key_U:
            self._open_in_browser()
        else:
            super().keyPressEvent(event)

    def _filter_by_project(self):
        try:
            if self.current_index < 0 or not self.parent_window:
                QMessageBox.warning(self, "Error", f"Invalid state: index={self.current_index}, parent={self.parent_window is not None}")
                return
            asset = self.assets[self.current_index]
            project_id = asset.project_id
            if not project_id:
                QMessageBox.warning(self, "Error", "No project associated with this asset.")
                return
            self.parent_window.filter_project_id = project_id
            # Очищаем текстовый и визуальный поиск перед фильтрацией, чтобы не конфликтовать
            if hasattr(self.parent_window, 'search_panel'):
                self.parent_window.search_panel.hybrid_input.text_input.clear()
                self.parent_window.search_panel.hybrid_input.clear_image()
            self.parent_window._update_breadcrumbs()
            self.close()
        except Exception as e:
            import traceback
            QMessageBox.critical(self, "Exception in _filter_by_project", f"Traceback:\n{traceback.format_exc()}")

    def _filter_by_author(self):
        try:
            if self.current_index < 0 or not self.parent_window:
                QMessageBox.warning(self, "Error", f"Invalid state: index={self.current_index}, parent={self.parent_window is not None}")
                return
            asset = self.assets[self.current_index]
            project_id = asset.project_id
            if not project_id:
                QMessageBox.warning(self, "Error", "No project associated with this asset.")
                return
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT author FROM projects WHERE id = ?", (project_id,))
                p_row = cur.fetchone()
                if p_row and p_row['author']:
                    self.parent_window.filter_author = p_row['author']
                    if hasattr(self.parent_window, 'search_panel'):
                        self.parent_window.search_panel.hybrid_input.text_input.clear()
                        self.parent_window.search_panel.hybrid_input.clear_image()
                    self.parent_window._update_breadcrumbs()
                    self.close()
                else:
                    QMessageBox.warning(self, "Error", "No author found for this project.")
        except Exception as e:
            import traceback
            QMessageBox.critical(self, "Exception in _filter_by_author", f"Traceback:\n{traceback.format_exc()}")
