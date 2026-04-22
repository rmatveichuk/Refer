from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QToolBar, QStatusBar, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem
)
from PyQt6.QtCore import Qt, QRectF, QSize
from PyQt6.QtGui import QPixmap, QPainter, QImage, QWheelEvent, QMouseEvent, QKeyEvent, QKeySequence

import logging
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

    def __init__(self):
        super().__init__()
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

        # Тулбар
        toolbar = QToolBar()
        toolbar.setStyleSheet("""
            QToolBar { background-color: #1a1a1a; border-bottom: 1px solid #333; padding: 4px; }
            QPushButton { background-color: #2d2d2d; color: #e0e0e0; border: 1px solid #444; border-radius: 4px; padding: 6px 12px; font-size: 13px; }
            QPushButton:hover { background-color: #3d3d3d; }
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

        # Статус-бар
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.setStyleSheet("QStatusBar { background-color: #1a1a1a; color: #888; }")

        # Загружаем полное изображение в фоне
        from PyQt6.QtCore import QThreadPool
        from ui.workers.image_loader import ImageLoaderWorker
        self._thread_pool = QThreadPool.globalInstance()

        self._key_pressed = False

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
