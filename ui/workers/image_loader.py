import logging
from PyQt6.QtCore import QObject, QRunnable, QSize, QUrl, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QImage, QImageReader

logger = logging.getLogger(__name__)

class ImageLoaderSignals(QObject):
    loaded = pyqtSignal(int, QImage)
    error = pyqtSignal(int, str)
    completed = pyqtSignal(int, int, QImage, str)

class ImageLoaderWorker(QRunnable):
    """
    Background worker for loading images from disk.
    Ensures that disk I/O and image decoding never blocks the main GUI thread.
    """
    def __init__(self, asset_id: int, file_path: str, generation=0, max_size=640,
                 cancellation=None):
        super().__init__()
        self.asset_id = asset_id
        self.file_path = file_path
        self.generation = generation
        self.max_size = max(1, int(max_size))
        self.cancellation = cancellation
        self.signals = ImageLoaderSignals()

    @pyqtSlot()
    def run(self):
        image = QImage()
        error = ""
        try:
            if self.cancellation is not None and self.cancellation.is_set():
                return
            import os
            # Нормализуем путь для Windows (убираем file:/// если есть)
            path = self.file_path
            if path.startswith('file:'):
                path = QUrl(path).toLocalFile()
            path = os.path.normpath(path)
            
            # QImage is safe to use in non-GUI threads!
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            dimensions = reader.size()
            if dimensions.isValid():
                # Reject pathological/decompression-bomb dimensions before decoding.
                if dimensions.width() * dimensions.height() > 25_000_000:
                    raise ValueError("Image dimensions exceed the thumbnail decode limit")
                if max(dimensions.width(), dimensions.height()) > self.max_size:
                    reader.setScaledSize(dimensions.scaled(
                        QSize(self.max_size, self.max_size), Qt.AspectRatioMode.KeepAspectRatio))
            image = reader.read()
            if self.cancellation is not None and self.cancellation.is_set():
                image = QImage()
                return
            if image.isNull():
                error = f"File cannot be loaded: {path}: {reader.errorString()}"
                self.signals.error.emit(self.asset_id, error)
                return
            if max(image.width(), image.height()) > self.max_size:
                image = image.scaled(self.max_size, self.max_size,
                    Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            
            self.signals.loaded.emit(self.asset_id, image)
        except Exception as e:
            error = str(e)
            self.signals.error.emit(self.asset_id, error)
        finally:
            # Cancellation must also free the model's outstanding-request slot.
            self.signals.completed.emit(self.asset_id, self.generation, image, error)
