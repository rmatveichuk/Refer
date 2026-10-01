import logging
import hashlib
import ntpath
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit
from PyQt6.QtCore import QRunnable, pyqtSignal, QObject
from PIL import Image

from database.db_manager import DatabaseManager
from database.models import Asset

logger = logging.getLogger(__name__)


def normalized_local_path(value: str) -> str:
    """Compare Windows paths independently of case, slash style and dot segments."""
    if not value:
        return ""
    if value.lower().startswith("file:"):
        parsed = urlsplit(value)
        value = unquote(parsed.path)
        if parsed.netloc and parsed.netloc.lower() != "localhost":
            value = "//" + parsed.netloc + value
        elif len(value) >= 3 and value[0] == "/" and value[2] == ":":
            value = value[1:]
    return ntpath.normpath(value.replace("/", "\\")).casefold()


class LocalFolderSignals(QObject):
    asset_processed = pyqtSignal(Asset)
    finished = pyqtSignal(str)
    error = pyqtSignal(str, str)
    progress = pyqtSignal(int, int, str)

class LocalFolderParser(QRunnable):
    def __init__(self, folder_path: str, db_manager: DatabaseManager, mode: str = "All", recursive: bool = True, skip_deleted: bool = True):
        super().__init__()
        self.folder_path = folder_path
        self.db = db_manager
        self.mode = mode
        self.recursive = recursive
        self.skip_deleted = skip_deleted
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
                if part.casefold() in self.models_ignored_folders:
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
            if self._is_cancelled:
                return
            if not Path(self.folder_path).is_dir():
                raise NotADirectoryError(f"Папка недоступна: {self.folder_path}")

            # Explicitly added parent and child roots remain independent sources.
            source_domain = str(Path(self.folder_path).resolve())
            source_id = None
            source_key = normalized_local_path(source_domain)
            
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                
                if not conn.in_transaction:
                    conn.execute("BEGIN IMMEDIATE")
                cur.execute("SELECT id, domain FROM sources ORDER BY id")
                s_row = next((row for row in cur.fetchall()
                              if normalized_local_path(row['domain']) == source_key), None)
                if s_row:
                    source_id = s_row['id']
                else:
                    cur.execute("INSERT INTO sources (url, domain) VALUES (?, ?)", (self.folder_path, source_domain))
                    source_id = cur.lastrowid
                    
                conn.commit()
            
            # Сохраняем ID источника для дальнейшего использования
            self.current_source_id = source_id

            # Сначала соберем все файлы
            files_to_process = []
            
            if self.recursive:
                for root, _, files in os.walk(self.folder_path):
                    if self._is_cancelled:
                        break
                    for file in files:
                        if self._is_cancelled:
                            break
                        ext = Path(file).suffix.lower()
                        if ext in self.valid_extensions:
                            if not self._should_ignore_file(root, file):
                                files_to_process.append(os.path.join(root, file))
            else:
                # Только корневая папка
                root = self.folder_path
                with os.scandir(root) as entries:
                    for entry in entries:
                        if self._is_cancelled: break
                        if entry.is_file():
                            ext = Path(entry.name).suffix.lower()
                            if ext in self.valid_extensions:
                                if not self._should_ignore_file(root, entry.name):
                                    files_to_process.append(entry.path)

            if self._is_cancelled:
                return

            total_files = len(files_to_process)
            logger.info(f"Найдено изображений для обработки: {total_files}")

            # Запрашиваем все уже существующие local_path из БД для быстрого поиска дубликатов
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT local_path FROM assets WHERE local_path IS NOT NULL AND local_path != ''")
                existing_paths = {normalized_local_path(row['local_path']) for row in cur.fetchall()}
                
                # Также запрашиваем список удаленных (игнорируемых) путей
                # (Для локальных файлов original_url в базе часто равен file:///... или совпадает с путем)
                cur.execute("SELECT original_url FROM deleted_assets")
                deleted_paths = {normalized_local_path(row['original_url']) for row in cur.fetchall()}

            # Батчинг
            batch_size = 500
            current_batch = []
            
            for i, file_path in enumerate(files_to_process):
                if self._is_cancelled:
                    logger.info("Сканирование папки прервано пользователем.")
                    break

                if i % 50 == 0 or i == total_files - 1:
                    self.signals.progress.emit(i + 1, total_files, Path(file_path).name)

                path_key = normalized_local_path(file_path)
                if path_key in existing_paths:
                    continue # Уже есть в БД
                
                if self.skip_deleted and path_key in deleted_paths:
                    continue # Игнорируем удаленный ранее файл

                # Быстро читаем размеры, не загружая всю картинку в память если возможно
                try:
                    with Image.open(file_path) as img:
                        width, height = img.size
                except Exception as e:
                    logger.warning(f"Не удалось прочитать {file_path}: {e}")
                    continue

                # Быстрый MD5 хэш от пути файла вместо медленного pHash
                # (нам не нужна умная дедупликация визуально похожих для локальных папок)
                fast_hash = hashlib.md5(path_key.encode('utf-8')).hexdigest()
                file_url = f"file:///{file_path.replace(os.sep, '/')}"
                
                cat = "custom_folder"
                if self.mode == "3D Models":
                    cat = "3d_render"
                elif self.mode == "Textures":
                    cat = "textures"

                current_batch.append((
                    file_url, file_path, file_path, fast_hash, width, height, self.current_source_id, cat, "Local"
                ))
                existing_paths.add(path_key)

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
        """Recheck paths under the write lock so overlapping imports cannot race."""
        if self._is_cancelled:
            return
        with self.db.get_connection() as conn:
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            cur = conn.cursor()
            cur.execute("SELECT local_path FROM assets WHERE local_path IS NOT NULL AND local_path != ''")
            existing_paths = {normalized_local_path(row['local_path']) for row in cur.fetchall()}
            cur.execute("SELECT original_url FROM deleted_assets")
            deleted_paths = {normalized_local_path(row['original_url']) for row in cur.fetchall()}
            unique_batch = []
            for row in batch:
                key = normalized_local_path(row[1])
                if key in existing_paths or (self.skip_deleted and key in deleted_paths):
                    continue
                unique_batch.append(row)
                existing_paths.add(key)
            if not self._is_cancelled:
                cur.executemany('''
                    INSERT INTO assets (original_url, local_path, thumbnail_path, phash, width, height, source_id, category, image_type, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ''', unique_batch)
                conn.commit()
