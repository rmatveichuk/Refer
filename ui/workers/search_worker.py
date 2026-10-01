import numpy as np
from PyQt6.QtCore import QObject, QRunnable, pyqtSignal


def embedding_key(text, image_path):
    import os
    try:
        stat = os.stat(image_path) if image_path else None
        signature = (stat.st_mtime_ns, stat.st_size) if stat else None
    except OSError:
        signature = None
    return text, image_path, signature


class SearchWorker(QRunnable):
    class Signals(QObject):
        result = pyqtSignal(object, object, object)
        error = pyqtSignal(object, str)

    def __init__(self, ai, text, image_path):
        super().__init__()
        self.ai, self.text, self.image_path = ai, text, image_path
        self.key = embedding_key(text, image_path)
        self.signals = self.Signals()

    def run(self):
        try:
            info = self.ai.get_text_query_info(self.text) if self.text else {}
            txt = self.ai.get_text_embedding(self.text) if self.text else None
            img = self.ai.get_image_embedding(self.image_path) if self.image_path else None
            def checked(value):
                if value is None:
                    return None
                value = np.asarray(value, dtype=np.float32).reshape(-1)
                if value.size != self.ai.model.config.text_config.hidden_size or not np.isfinite(value).all() or np.linalg.norm(value) <= 1e-12:
                    raise ValueError("Модель вернула некорректный поисковый вектор.")
                return value
            txt, img = checked(txt), checked(img)
            vector = img * 0.6 + txt * 0.4 if txt is not None and img is not None else txt if txt is not None else img
            vector = checked(vector)
            if vector is None:
                raise ValueError("Нет текста или изображения для поиска.")
            vector = vector / np.linalg.norm(vector)
            self.signals.result.emit(self.key, vector, info)
        except Exception as exc:
            self.signals.error.emit(self.key, str(exc))
