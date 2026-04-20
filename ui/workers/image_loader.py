import logging
from PyQt6.QtCore import QObject, QRunnable, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QImage

logger = logging.getLogger(__name__)

class ImageLoaderSignals(QObject):
    loaded = pyqtSignal(int, QImage)
    error = pyqtSignal(int, str)

class ImageLoaderWorker(QRunnable):
    """
    Background worker for loading images from disk.
    Ensures that disk I/O and image decoding never blocks the main GUI thread.
    """
    def __init__(self, asset_id: int, file_path: str):
        super().__init__()
        self.asset_id = asset_id
        self.file_path = file_path
        self.signals = ImageLoaderSignals()

    @pyqtSlot()
    def run(self):
        try:
            # QImage is safe to use in non-GUI threads!
            # It loads the byte data and decodes it (e.g. WebP). 
            image = QImage(self.file_path)
            if image.isNull():
                self.signals.error.emit(self.asset_id, f"File cannot be loaded: {self.file_path}")
                return
            
            self.signals.loaded.emit(self.asset_id, image)
        except Exception as e:
            self.signals.error.emit(self.asset_id, str(e))
