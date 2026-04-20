import logging
import os
import requests
from urllib.parse import urlparse
from pathlib import Path
from PyQt6.QtCore import QRunnable, pyqtSignal, QObject
import time

from database.db_manager import DatabaseManager
from database.models import Asset, Source
from scrapers.processors import compute_phash, optimize_thumbnail
import config

logger = logging.getLogger(__name__)

class ScraperSignals(QObject):
    asset_processed = pyqtSignal(Asset)
    finished = pyqtSignal(str)
    error = pyqtSignal(str, str)

class ScraperManager(QRunnable):
    """
    Оркестратор загрузки данных. Работает в QThreadPool.
    1. Инициирует загрузку определенного сайта/страницы.
    2. Скачивает оригиналы картинок.
    3. Создает WebP превью (1024px) и вычисляет pHash.
    4. Если pHash уникальный -> сохраняет в SQLite и делает pyqtSignal(Asset).
    """

    def __init__(self, parser_class, url: str, db_manager: DatabaseManager, category: str = "3d_render", max_images_per_project: int = 5):
        super().__init__()
        self.parser_class = parser_class
        self.url = url
        self.db = db_manager
        self.category = category
        self.signals = ScraperSignals()
        self._is_cancelled = False
        self._project_image_count: dict[str, int] = {}  # project_id -> count
        self._max_images_per_project = max_images_per_project

    def delete_asset(self, asset: Asset):
        """Удаляет ассет из БД и физически, помещает URL и pHash в список удаленных."""
        import os
        
        if asset.original_url:
            self.db.mark_as_deleted(asset.original_url, reason="user_deleted", phash=asset.phash)
            logger.info(f"Marked URL as deleted: {asset.original_url[:60]}")
        
        # Удаляем файл с диска
        if asset.thumbnail_path and Path(asset.thumbnail_path).exists():
            try:
                Path(asset.thumbnail_path).unlink()
                logger.info(f"Deleted file: {asset.thumbnail_path}")
            except Exception as e:
                logger.warning(f"Failed to delete file {asset.thumbnail_path}: {e}")
        
        # Удаляем запись из БД
        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM asset_tags WHERE asset_id = ?", (asset.id,))
            conn.execute("DELETE FROM assets WHERE id = ?", (asset.id,))
            conn.commit()
        
        logger.info(f"Deleted asset #{asset.id}: {asset.original_url[:60]}")

    def cancel(self):
        """Signals the scraper to stop ASAP."""
        self._is_cancelled = True
        # Также сообщаем парсеру (если у него есть метод cancel)
        if hasattr(self, '_scraper_instance') and hasattr(self._scraper_instance, 'cancel'):
            self._scraper_instance.cancel()

    def run(self):
        logger.info(f"Starting ingestion for: {self.url}")

        # Передаем db_path в парсер для проверки дубликатов
        import config
        scraper_instance = self.parser_class(self.url, on_image_found=self.process_image_url, db_path=str(config.DB_PATH))
        self._scraper_instance = scraper_instance  # Сохраняем ссылку для cancel()

        try:
            scraper_instance.run()
            if not self._is_cancelled:
                self.signals.finished.emit(self.url)
        except Exception as e:
            logger.error(f"Scraper Manager failed: {e}")
            if not self._is_cancelled:
                try:
                    self.signals.error.emit(self.url, str(e))
                except RuntimeError:
                    pass # Object already deleted by UI

    def process_image_url(self, asset_data: dict) -> bool:
        """
        Returns True if should continue scraping, False if should STOP.
        asset_data keys: url, domain, project_id, project_title, author
        """
        if self._is_cancelled:
            logger.info("Cancellation requested, aborting process_image_url.")
            return False

        img_url = asset_data.get('url')
        source_domain = asset_data.get('domain')
        project_id_str = asset_data.get('project_id')
        project_title = asset_data.get('project_title', '')
        author = asset_data.get('author', '')
        location = asset_data.get('location', '')
        image_type = asset_data.get('image_type', 'Photography')
        category = asset_data.get('category') or self.category
        tags = asset_data.get('tags', [])

        # Limit images per project
        if project_id_str:
            current_count = self._project_image_count.get(project_id_str, 0)
            if current_count >= self._max_images_per_project:
                # Log only once when limit is reached
                if current_count == self._max_images_per_project:
                    logger.info(f"Project '{project_title}' reached max {self._max_images_per_project} images, skipping remaining.")
                    self._project_image_count[project_id_str] = current_count + 1 # Increment once more to stop logging
                return False  # STOP processing images for THIS project
            self._project_image_count[project_id_str] = current_count + 1

        # 1. URL Duplicate Check — пропускаем, но не останавливаем весь парсинг
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM assets WHERE original_url = ?", (img_url,))
            if cur.fetchone():
                logger.info(f"URL duplicate, skipping: {img_url[:60]}")
                return True  # Пропустить, но продолжить парсинг
        
        try:
            # Этичный Scraping Rate Limit (1 картинка раз в 2 секунды)
            time.sleep(2)
            
            headers = {"User-Agent": "RenderVaultBot/1.0 (contact: test@example.com)"}
            response = requests.get(img_url, timeout=15, headers=headers)
            response.raise_for_status()
            
            filename = os.path.basename(urlparse(img_url).path)
            if not filename:
                filename = f"temp_{int(time.time())}.jpg"
                
            temp_path = config.APP_DATA_DIR / "temp" / filename
            temp_path.parent.mkdir(exist_ok=True)
            
            with open(temp_path, 'wb') as f:
                f.write(response.content)
            
            # Check Cancellation again before heavy compute
            if self._is_cancelled:
                temp_path.unlink()
                return False

            phash_val = compute_phash(str(temp_path))
            
            if self.db.is_deleted(phash=phash_val):
                logger.info(f"Image was previously deleted (by pHash), skipping: {img_url[:60]}")
                temp_path.unlink()
                return True
            
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT id FROM assets WHERE phash = ?", (phash_val,))
                if cur.fetchone():
                    # pHash дубликат — пропускаем эту картинку, но НЕ останавливаем парсинг.
                    # Это НЕ Delta Logic: картинка может быть той же, но с другого CDN-размера.
                    logger.info(f"pHash duplicate, skipping (but continuing scrape): {img_url[:60]}")
                    temp_path.unlink()
                    return True  # <-- Continue scraping!
            
            new_filename = f"{phash_val}.webp"
            thumb_path = config.THUMBNAILS_DIR / new_filename
            width, height = optimize_thumbnail(str(temp_path), str(thumb_path), max_size=1024)
            temp_path.unlink() 
            
            # Trash-Filter: Проверяем минимальное разрешение
            if min(width, height) < 512:
                logger.warning(f"Ignored: Low Resolution {width}x{height} (<512px)")
                thumb_path.unlink(missing_ok=True)
                return True # Просто пропускаем картинку, но не останавливаем весь парсинг проекта
            
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                
                # Ищем или создаем Source
                cur.execute("SELECT id FROM sources WHERE domain = ?", (source_domain,))
                s_row = cur.fetchone()
                if s_row:
                    source_id = s_row['id']
                else:
                    cur.execute("INSERT INTO sources (url, domain) VALUES (?, ?)", (self.url, source_domain))
                    source_id = cur.lastrowid
                
                # Ищем или создаем Project
                db_project_id = None
                if project_id_str:
                    cur.execute("SELECT id FROM projects WHERE url = ?", (project_id_str,))
                    p_row = cur.fetchone()
                    if p_row:
                        db_project_id = p_row['id']
                    else:
                        cur.execute("INSERT INTO projects (title, url, author, location) VALUES (?, ?, ?, ?)", 
                                    (project_title, project_id_str, author, location))
                        db_project_id = cur.lastrowid
                    
                cur.execute('''
                    INSERT INTO assets (original_url, local_path, thumbnail_path, phash, width, height, source_id, project_id, category, image_type, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ''', (img_url, str(thumb_path), str(thumb_path), phash_val, width, height, source_id, db_project_id, category, image_type))
                
                asset_id = cur.lastrowid
                conn.commit()
            
            if tags:
                self.db.add_tags_to_asset(asset_id, tags)
            
            asset = Asset(id=asset_id, original_url=img_url, thumbnail_path=str(thumb_path), phash=phash_val, width=width, height=height, project_id=db_project_id, category=category, image_type=image_type)
            if not self._is_cancelled:
                try:
                    self.signals.asset_processed.emit(asset)
                except RuntimeError:
                    pass # Object deleted
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to process image {img_url}: {e}")
            return True
