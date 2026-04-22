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
    def __init__(self, folder_path: str, db_manager: DatabaseManager, mode: str = "All"):
        super().__init__()
        self.folder_path = folder_path
        self.db = db_manager
        self.mode = mode
        self.signals = LocalFolderSignals()
        self._is_cancelled = False
        self.valid_extensions = {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}
        
        # Blacklists for 3D Models mode
        self.models_ignored_folders = {'textures', 'maps', 'mat', 'materials', 'tex'}
        self.models_ignored_suffixes = {'_diffuse', '_diff', '_bump', '_normal', '_nrm', '_spec', '_gloss', '_rough', '_disp', '_mask', '_ao', '_opacity'}

        # Blacklists for Textures mode
        self.textures_ignored_suffixes = {'_bump', '_normal', '_nrm', '_spec', '_gloss', '_rough', '_disp', '_mask', '_ao', '_opacity'}

    def _should_ignore_file(self, root: str, file_name: str) -> bool:
        if self.mode == "All":
            return False
            
        name_lower = Path(file_name).stem.lower()
        
        if self.mode == "3D Models":
            # 1. Проверка пути (папок)
            path_parts = Path(root).parts
            for part in path_parts:
                if any(keyword in part.lower() for keyword in self.models_ignored_folders):
                    return True
                    
            # 2. Проверка суффиксов в имени файла
            if any(name_lower.endswith(suffix) for suffix in self.models_ignored_suffixes):
                return True
                
        elif self.mode == "Textures":
            # Игнорируем технические карты, оставляем только diffuse/color
            if any(suffix in name_lower for suffix in self.textures_ignored_suffixes):
                return True
                
        return False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        logger.info(f"Начинаем сканирование папки: {self.folder_path} в режиме {self.mode}")
        try:
            # 1. Умная обработка источников (Sources)
            source_domain = str(Path(self.folder_path).resolve())
            source_id = None
            
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                
                # Ищем текущий Source
                cur.execute("SELECT id FROM sources WHERE domain = ?", (source_domain,))
                s_row = cur.fetchone()
                if s_row:
                    source_id = s_row['id']
                else:
                    cur.execute("INSERT INTO sources (url, domain) VALUES (?, ?)", (self.folder_path, source_domain))
                    source_id = cur.lastrowid
                    
                # Ищем все "дочерние" Sources, которые уже есть в базе
                cur.execute("SELECT id, domain FROM sources WHERE domain LIKE '_:%' OR domain LIKE '/%'")
                for child_row in cur.fetchall():
                    child_id = child_row['id']
                    child_domain = child_row['domain']
                    
                    if child_id != source_id:
                        try:
                            # Если текущая папка является родителем для child_domain
                            if os.path.commonpath([source_domain, child_domain]) == source_domain:
                                logger.info(f"Объединение источника: перенос ассетов из {child_domain} в {source_domain}")
                                # Переносим все ассеты из дочерней папки в родительскую
                                cur.execute("UPDATE assets SET source_id = ? WHERE source_id = ?", (source_id, child_id))
                                # Удаляем старый дочерний source
                                cur.execute("DELETE FROM sources WHERE id = ?", (child_id,))
                        except ValueError:
                            pass # Разные диски
                
                conn.commit()
            
            # Сохраняем ID источника для дальнейшего использования
            self.current_source_id = source_id

            # Сначала соберем все файлы
            files_to_process = []
            for root, _, files in os.walk(self.folder_path):
                if self._is_cancelled:
                    break
                for file in files:
                    ext = Path(file).suffix.lower()
                    if ext in self.valid_extensions:
                        if not self._should_ignore_file(root, file):
                            files_to_process.append(os.path.join(root, file))

            total_files = len(files_to_process)
            logger.info(f"Найдено изображений для обработки: {total_files}")

            # Запрашиваем все уже существующие local_path из БД для быстрого поиска дубликатов
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT local_path FROM assets WHERE source_id = ?", (self.current_source_id,))
                existing_paths = {row['local_path'] for row in cur.fetchall()}

            # Батчинг
            batch_size = 500
            current_batch = []
            
            import hashlib

            for i, file_path in enumerate(files_to_process):
                if self._is_cancelled:
                    logger.info("Сканирование папки прервано пользователем.")
                    break

                if i % 50 == 0 or i == total_files - 1:
                    self.signals.progress.emit(i + 1, total_files, Path(file_path).name)

                if file_path in existing_paths:
                    continue # Уже есть в БД

                # Быстро читаем размеры, не загружая всю картинку в память если возможно
                try:
                    with Image.open(file_path) as img:
                        width, height = img.size
                except Exception as e:
                    logger.warning(f"Не удалось прочитать {file_path}: {e}")
                    continue

                # Быстрый MD5 хэш от пути файла вместо медленного pHash
                # (нам не нужна умная дедупликация визуально похожих для локальных папок)
                fast_hash = hashlib.md5(file_path.encode('utf-8')).hexdigest()
                file_url = f"file:///{file_path.replace(os.sep, '/')}"
                
                cat = "custom_folder"
                if self.mode == "3D Models":
                    cat = "3d_render"
                elif self.mode == "Textures":
                    cat = "textures"

                current_batch.append((
                    file_url, file_path, file_path, fast_hash, width, height, self.current_source_id, cat, "Local"
                ))

                if len(current_batch) >= batch_size:
                    self._flush_batch(current_batch)
                    current_batch = []

            # Дописываем остатки
            if current_batch and not self._is_cancelled:
                self._flush_batch(current_batch)

            if not self._is_cancelled:
                self.signals.finished.emit(self.folder_path)

        except Exception as e:
            logger.error(f"Ошибка при сканировании папки: {e}")
            if not self._is_cancelled:
                self.signals.error.emit(self.folder_path, str(e))

    def _flush_batch(self, batch: list):
        """Пакетная вставка в базу данных для максимальной скорости."""
        try:
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                cur.executemany('''
                    INSERT INTO assets (original_url, local_path, thumbnail_path, phash, width, height, source_id, category, image_type, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ''', batch)
                conn.commit()
        except Exception as e:
            logger.error(f"Ошибка при пакетной вставке: {e}")