import logging
import os
import time
from pathlib import Path
from PyQt6.QtCore import QRunnable, pyqtSignal, QObject
from PIL import Image

from database.db_manager import DatabaseManager
from database.models import Asset
from scrapers.processors import compute_phash

logger = logging.getLogger(__name__)

class LocalFolderSignals(QObject):
    asset_processed = pyqtSignal(Asset)
    finished = pyqtSignal(str)
    error = pyqtSignal(str, str)
    progress = pyqtSignal(int, int, str)

class LocalFolderParser(QRunnable):
    def __init__(self, folder_path: str, db_manager: DatabaseManager):
        super().__init__()
        self.folder_path = folder_path
        self.db = db_manager
        self.signals = LocalFolderSignals()
        self._is_cancelled = False
        self.valid_extensions = {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        logger.info(f"Начинаем сканирование папки: {self.folder_path}")
        try:
            # Сначала соберем все файлы
            files_to_process = []
            for root, _, files in os.walk(self.folder_path):
                if self._is_cancelled:
                    break
                for file in files:
                    ext = Path(file).suffix.lower()
                    if ext in self.valid_extensions:
                        files_to_process.append(os.path.join(root, file))

            total_files = len(files_to_process)
            logger.info(f"Найдено изображений для обработки: {total_files}")

            for i, file_path in enumerate(files_to_process):
                if self._is_cancelled:
                    logger.info("Сканирование папки прервано пользователем.")
                    break

                self.signals.progress.emit(i, total_files, Path(file_path).name)
                self.process_local_image(file_path)

            if not self._is_cancelled:
                self.signals.finished.emit(self.folder_path)

        except Exception as e:
            logger.error(f"Ошибка при сканировании папки: {e}")
            if not self._is_cancelled:
                self.signals.error.emit(self.folder_path, str(e))

    def process_local_image(self, file_path: str):
        # 1. Проверяем, не добавляли ли мы этот файл ранее по пути
        file_url = f"file:///{file_path.replace(os.sep, '/')}"
        
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM assets WHERE original_url = ?", (file_url,))
            if cur.fetchone():
                return  # Уже в базе, пропускаем

        try:
            # Читаем размеры картинки
            with Image.open(file_path) as img:
                width, height = img.size

            # Вычисляем pHash. Мы не отсеиваем дубликаты из папок,
            # но нам нужен pHash для группировки / отображения в таблице
            phash_val = compute_phash(file_path)
            
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                
                # Ищем или создаем Source. Домен - это корневая папка, которую мы выбрали.
                source_domain = str(Path(self.folder_path).resolve())
                
                cur.execute("SELECT id FROM sources WHERE domain = ?", (source_domain,))
                s_row = cur.fetchone()
                if s_row:
                    source_id = s_row['id']
                else:
                    cur.execute("INSERT INTO sources (url, domain) VALUES (?, ?)", (self.folder_path, source_domain))
                    source_id = cur.lastrowid
                
                cur.execute('''
                    INSERT INTO assets (original_url, local_path, thumbnail_path, phash, width, height, source_id, category, image_type, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ''', (file_url, file_path, file_path, phash_val, width, height, source_id, "custom_folder", "Local"))
                
                asset_id = cur.lastrowid
                conn.commit()
            
            asset = Asset(id=asset_id, original_url=file_url, thumbnail_path=file_path, phash=phash_val, width=width, height=height, category="custom_folder", image_type="Local")
            self.signals.asset_processed.emit(asset)

        except Exception as e:
            logger.warning(f"Не удалось обработать локальный файл {file_path}: {e}")