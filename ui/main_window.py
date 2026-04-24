from PyQt6.QtWidgets import (
    QMainWindow, QVBoxLayout, QHBoxLayout, QWidget, QMessageBox, QPushButton, 
    QLabel, QProgressBar, QTabWidget, QTableView, QHeaderView, 
    QAbstractItemView, QMenu, QApplication
)
from PyQt6.QtCore import Qt, QThreadPool, pyqtSlot, QTimer, QRunnable, QObject, pyqtSignal
from PyQt6.QtGui import QAction

from ui.widgets.gallery_view import GalleryView
from ui.widgets.lazy_model import AssetListModel
from ui.widgets.top_toolbar import TopToolbar
from ui.widgets.search_panel import SearchPanel
from database.db_manager import DatabaseManager
from database.models import Asset
from scrapers.manager import ScraperManager
from scrapers.behance_parser import BehanceParser
from scrapers.archdaily_parser import ArchDailyParser
from scrapers.local_folder import LocalFolderParser
from database.faiss_manager import FaissManager
from ui.settings_dialog import SettingsDialog
from PyQt6.QtWidgets import QFileDialog
import config

import sqlite3
import logging
import os
import numpy as np

logger = logging.getLogger(__name__)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Refer — AI Asset Manager")
        self.setMinimumSize(1200, 850)
        self.setStyleSheet("background-color: #121212;")
        
        self.db = DatabaseManager(config.DB_PATH)
        self.faiss_mgr = FaissManager(config.FAISS_PATH, dimension=config.VECTOR_DIMENSION)
        self.ai = None
        
        self.active_scraper = None
        self.active_indexer = None
        self.active_searcher = None
        self.ai_initializing = False  # Флаг для предотвращения двойной инициализации
        self.current_category = "All"
        self.search_threshold = 0.6
        self.search_sources = []
        
        self._init_ui()
        self.update_sources_panel()
        self._load_assets_for_gallery()
        self._refresh_library()
        
        # Начинаем фоновую загрузку AI сразу при старте
        QTimer.singleShot(500, self._background_ai_init)

    def _background_ai_init(self):
        if self.ai or self.ai_initializing:
            return
            
        self.ai_initializing = True
        self.status_label.setText("⏳ Инициализация AI (в фоне)...")
        
        class InitWorker(QRunnable):
            def __init__(self, parent):
                super().__init__()
                self.parent = parent
            def run(self):
                try:
                    from ai.engine import AiEngine
                    engine = AiEngine()
                    # Проверяем, не удален ли родительский объект перед обновлением UI
                    from PyQt6 import sip
                    if not sip.isdeleted(self.parent):
                        self.parent.ai = engine
                        self.parent.status_label.setText("✅ AI готов")
                except Exception as e:
                    logger.error(f"Background AI init failed: {e}")
                    from PyQt6 import sip
                    if not sip.isdeleted(self.parent):
                        self.parent.status_label.setText("❌ Ошибка AI")
                finally:
                    from PyQt6 import sip
                    if not sip.isdeleted(self.parent):
                        self.parent.ai_initializing = False

        worker = InitWorker(self)
        QThreadPool.globalInstance().start(worker)

    def _init_ui(self):
        # Структура:
        # CentralWidget (QVBoxLayout)
        # ├── TopToolbar
        # ├── Прямо под TopToolbar мы вставим QProgressBar для визуализации процессов
        # └── ContentArea (QHBoxLayout)
        #     ├── SearchPanel
        #     └── Tabs (Gallery / Library)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # === 1. Top Toolbar ===
        self.top_toolbar = TopToolbar()
        self.top_toolbar.scrape_started.connect(self.start_scrape)
        self.top_toolbar.scrape_stopped.connect(self.stop_scrape)
        self.top_toolbar.add_folder_requested.connect(self._add_folder)
        self.top_toolbar.index_requested.connect(self.start_indexing)
        self.top_toolbar.cleanup_requested.connect(self._cleanup_missing_files)
        self.top_toolbar.category_changed.connect(self._on_category_changed)
        main_layout.addWidget(self.top_toolbar)

        # Выведем статус-бар и прогресс-бар в общий доступ MainWindow, для совместимости
        self.status_label = self.top_toolbar.status_label
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar { background-color: transparent; border: none; }
            QProgressBar::chunk { background-color: #29b6f6; }
        """)
        main_layout.addWidget(self.progress_bar)

        # === 2. Content Area ===
        content_widget = QWidget()
        content_layout = QHBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        main_layout.addWidget(content_widget, 1)

        # --- Left: Search Panel ---
        self.search_panel = SearchPanel()
        self.search_panel.search_triggered.connect(self._perform_visual_search)
        self.search_panel.clear_triggered.connect(self._on_clear_search)
        self.search_panel.remove_source_requested.connect(self._remove_source_folder)
        content_layout.addWidget(self.search_panel)

        # --- Right: Gallery & Library Tabs ---
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane { border: none; border-left: 1px solid #333; }
            QTabBar::tab { background: #1a1a1a; color: #888; padding: 8px 16px; border: none; }
            QTabBar::tab:selected { background: #29b6f6; color: #000; font-weight: bold; }
        """)
        
        # --- We will add the buttons as "fake tabs" instead ---
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._previous_tab_index = 0
        
        content_layout.addWidget(self.tabs, 1)

        self._setup_gallery_tab()
        self._setup_library_tab()
    def _on_tab_changed(self, index):
        tab_text = self.tabs.tabText(index)
        if tab_text == "Выделить все":
            self.tabs.setCurrentIndex(self._previous_tab_index)
            self._select_all_gallery()
        elif tab_text == "Удалить":
            self.tabs.setCurrentIndex(self._previous_tab_index)
            self._delete_selected_gallery()
        else:
            self._previous_tab_index = index

    def _select_all_gallery(self):
        self.gallery.selectAll()

    def _delete_selected_gallery(self):
        selection_model = self.gallery.selectionModel()
        if not selection_model.hasSelection():
            QMessageBox.information(self, "Ничего не выбрано", "Пожалуйста, выделите картинки для удаления.")
            return

        indexes = selection_model.selectedIndexes()
        assets_to_delete = [self.gallery_model.assets[idx.row()] for idx in indexes]
        self._delete_assets_batch(assets_to_delete)

    def _setup_gallery_tab(self):
        gallery_tab = QWidget()
        gallery_layout = QVBoxLayout(gallery_tab)
        gallery_layout.setContentsMargins(0, 0, 0, 0)
        gallery_layout.setSpacing(0)

        self.gallery = GalleryView()
        self.gallery_model = AssetListModel()
        self.gallery.setModel(self.gallery_model)
        self.gallery.db = self.db
        self.gallery.parent_window = self
        
        gallery_layout.addWidget(self.gallery)
        self.tabs.addTab(gallery_tab, "Галерея")

    def _setup_library_tab(self):
        library_tab = QWidget()
        library_layout = QVBoxLayout(library_tab)
        library_layout.setContentsMargins(8, 8, 8, 8)

        self.library_table = QTableView()
        self.library_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.library_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.library_table.horizontalHeader().setStretchLastSection(True)
        self.library_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.library_table.setAlternatingRowColors(True)
        self.library_table.setStyleSheet("""
            QTableView { background-color: #121212; color: #e0e0e0; border: 1px solid #333; gridline-color: #2a2a2a; }
            QHeaderView::section { background-color: #1e1e1e; color: #29b6f6; border: none; padding: 6px; font-weight: bold; }
            QTableView::item { padding: 4px; }
            QTableView::item:selected { background-color: #29b6f6; color: #000; }
        """)
        self.library_table.doubleClicked.connect(self._on_library_doubleclick)
        self.library_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.library_table.customContextMenuRequested.connect(self._show_library_context_menu)

        library_layout.addWidget(self.library_table)
        self.tabs.addTab(library_tab, "Таблица")
        
        # Fake tabs for actions
        self.tabs.addTab(QWidget(), "Выделить все")
        self.tabs.addTab(QWidget(), "Удалить")

    def _delete_assets_batch(self, assets: list):
        """Централизованное удаление списка ассетов (БД + FAISS + файлы)."""
        if not assets:
            return

        count = len(assets)
        msg = f"Вы действительно хотите удалить {count} ассетов?\n\nОни будут скрыты из галереи и не добавятся при повторном сканировании."
        reply = QMessageBox.question(self, "Удаление", msg, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        
        if reply != QMessageBox.StandardButton.Yes:
            return

        deleted_count = 0
        asset_ids = []
        
        for asset in assets:
            asset_ids.append(asset.id)
            
            # 1. Помечаем как удаленный для игнорирования в будущем
            if asset.original_url:
                self.db.mark_as_deleted(asset.original_url, reason="user_deleted", phash=asset.phash)
            
            # 2. Удаляем физический файл ТОЛЬКО для веба
            if asset.image_type != "Local":
                if asset.thumbnail_path and os.path.exists(asset.thumbnail_path):
                    try: os.remove(asset.thumbnail_path)
                    except: pass
            
            # 3. Удаляем из БД
            with self.db.get_connection() as conn:
                conn.execute("DELETE FROM asset_tags WHERE asset_id = ?", (asset.id,))
                conn.execute("DELETE FROM assets WHERE id = ?", (asset.id,))
                conn.commit()
            
            deleted_count += 1

        # 4. Удаляем из FAISS
        if asset_ids:
            self.faiss_mgr.remove_ids(asset_ids)

        self._load_assets_for_gallery()
        self._refresh_library()
        self.status_label.setText(f"🧹 Удалено {deleted_count} ассетов")

    def _cleanup_missing_files(self):
        """Очистка базы от записей, файлы которых были удалены пользователем вручную."""
        deleted_count, deleted_ids = self.db.cleanup_missing_files()
        
        # Глубокая очистка FAISS (на случай, если что-то осталось от прошлых удалений)
        try:
            faiss_ids = self.faiss_mgr.get_all_ids()
            db_ids = self.db.get_all_asset_ids()
            # Находим ID, которые есть в FAISS, но нет в базе
            orphan_ids = [int(fid) for fid in faiss_ids if int(fid) not in db_ids]
            
            if orphan_ids:
                self.faiss_mgr.remove_ids(orphan_ids)
                logger.info(f"Deep Cleanup: removed {len(orphan_ids)} orphaned vectors from FAISS")
        except Exception as e:
            logger.error(f"Deep FAISS cleanup failed: {e}")

        if deleted_count > 0 or (locals().get('orphan_ids') and len(orphan_ids) > 0):
            # Синхронизируем с FAISS (хотя это уже сделано выше в глубокой очистке, оставим для надежности)
            if deleted_ids:
                self.faiss_mgr.remove_ids(deleted_ids)
            
            total_removed = deleted_count + (len(orphan_ids) if locals().get('orphan_ids') else 0)
            self.status_label.setText(f"🧹 Очищено {total_removed} неактуальных записей")
            self._load_assets_for_gallery()
            self._refresh_library()
        else:
            self.status_label.setText("✅ Отсутствующие файлы не найдены")

    def _on_category_changed(self, category: str):
        self.current_category = category
        self._load_assets_for_gallery()

    def update_sources_panel(self):
        # 1. Получаем "точки входа" из таблицы sources
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT domain FROM sources WHERE domain LIKE '_:%' OR domain LIKE '/%'")
            source_folders = [row['domain'] for row in (cur.fetchall() or [])]

            # 2. Получаем ВСЕ папки, в которых лежат проиндексированные файлы
            cur.execute("SELECT DISTINCT local_path FROM assets WHERE local_path IS NOT NULL")
            asset_paths = [row['local_path'] for row in cur.fetchall()]
            
            import os
            # Путь к папке thumbnails, которую нужно скрыть
            app_data = os.getenv('LOCALAPPDATA')
            thumbnails_path = os.path.join(app_data, 'ReferAssetManager', 'thumbnails').lower() if app_data else ""
            
            all_folders = set(source_folders)
            for p in asset_paths:
                folder = os.path.dirname(p)
                # Добавляем саму папку и ВСЕ её родительские папки вверх по дереву
                # пока не дойдем до одной из "точек входа" или корня диска
                curr = folder
                while curr and len(curr) > 3:
                    if curr.lower() == thumbnails_path:
                        break
                    all_folders.add(curr)
                    next_parent = os.path.dirname(curr)
                    if next_parent == curr: break # Дошли до корня
                    curr = next_parent
            
        self.search_panel.update_custom_folders(list(all_folders))

    def _add_folder(self):
        folder_path = QFileDialog.getExistingDirectory(self, "Выберите папку с изображениями")
        if folder_path:
            parse_mode = self.top_toolbar.type_combo.currentText()
            skip_deleted = self.top_toolbar.check_ignore_deleted.isChecked()
            recursive = self.top_toolbar.check_subfolders.isChecked()

            self.top_toolbar.set_scraping_state(True)
            self.status_label.setText(f"Сканирование ({parse_mode}): {folder_path}")
            self.progress_bar.setVisible(True)
            self.progress_bar.setRange(0, 0)
            
            if self.active_scraper:
                self.active_scraper.cancel()
                
            self.active_scraper = LocalFolderParser(
                folder_path, self.db, 
                mode=parse_mode, 
                recursive=recursive, 
                skip_deleted=skip_deleted
            )
            self.active_scraper.signals.progress.connect(self._on_local_folder_progress)
            self.active_scraper.signals.finished.connect(self.on_scrape_finished)
            self.active_scraper.signals.error.connect(self.on_scrape_error)
            QThreadPool.globalInstance().start(self.active_scraper)

    def _on_local_folder_progress(self, current: int, total: int, info: str):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.status_label.setText(f"Сканирование [{current}/{total}]: {info}")

    def _remove_source_folder(self, path_or_domain: str):
        """Полное удаление источника и всех его ассетов из БД и FAISS."""
        msg = f"Удалить источник '{path_or_domain}' и все связанные с ним изображения из галереи?\n\nФайлы на диске затронуты не будут."
        reply = QMessageBox.question(self, "Удаление источника", msg, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        
        if reply != QMessageBox.StandardButton.Yes:
            return

        # 1. Находим все ассеты, принадлежащие этому источнику или пути
        assets_to_delete = []
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            
            # Определяем, это веб-домен или локальный путь
            if path_or_domain in ('archdaily', 'behance'):
                domain = 'archdaily.com' if path_or_domain == 'archdaily' else 'behance.net'
                cur.execute("""
                    SELECT a.* FROM assets a 
                    JOIN sources s ON a.source_id = s.id 
                    WHERE s.domain = ?
                """, (domain,))
            else:
                # Для локальных папок удаляем всё, что начинается с этого пути
                search_path = path_or_domain.replace('\\', '/')
                if not search_path.endswith('/'): search_path += '/'
                cur.execute("SELECT * FROM assets WHERE REPLACE(local_path, '\\', '/') LIKE ?", (search_path + "%",))
            
            rows = cur.fetchall()
            for row in rows:
                assets_to_delete.append(Asset(
                    id=row['id'], original_url=row['original_url'],
                    thumbnail_path=row['thumbnail_path'], phash=row['phash'],
                    image_type=row['image_type'] or 'Photography'
                ))

        # 2. Удаляем ассеты пачкой (уже синхронизирует FAISS и БД)
        if assets_to_delete:
            # Мы вызываем _delete_assets_batch, но нам нужно избежать ПОВТОРНОГО подтверждения внутри него.
            # Поэтому мы временно подменим QMessageBox.question или просто реализуем логику тут.
            # На самом деле, лучше просто скопировать логику удаления без подтверждения.
            
            deleted_count = 0
            asset_ids = []
            for asset in assets_to_delete:
                asset_ids.append(asset.id)
                if asset.original_url:
                    self.db.mark_as_deleted(asset.original_url, reason="source_removed", phash=asset.phash)
                if asset.image_type != "Local":
                    if asset.thumbnail_path and os.path.exists(asset.thumbnail_path):
                        try: os.remove(asset.thumbnail_path)
                        except: pass
                with self.db.get_connection() as conn:
                    conn.execute("DELETE FROM asset_tags WHERE asset_id = ?", (asset.id,))
                    conn.execute("DELETE FROM assets WHERE id = ?", (asset.id,))
                    conn.commit()
                deleted_count += 1
            
            if asset_ids:
                self.faiss_mgr.remove_ids(asset_ids)
            
            logger.info(f"Removed source {path_or_domain}: {deleted_count} assets deleted")

        # 3. Удаляем сам источник из таблицы sources
        with self.db.get_connection() as conn:
            if path_or_domain in ('archdaily', 'behance'):
                domain = 'archdaily.com' if path_or_domain == 'archdaily' else 'behance.net'
                conn.execute("DELETE FROM sources WHERE domain = ?", (domain,))
            else:
                conn.execute("DELETE FROM sources WHERE domain = ?", (path_or_domain,))
            conn.commit()

        # 4. Обновляем UI
        self.update_sources_panel()
        self._load_assets_for_gallery()
        self._refresh_library()
        self.status_label.setText(f"🗑 Источник '{path_or_domain}' удален")

    def _load_assets_for_gallery(self):
        # Получаем выбранные источники
        selected_sources = self.search_panel.get_selected_sources()
        
        # Если ничего не выбрано - галерея пустая
        if not selected_sources:
            self.gallery_model.setAssets([])
            return

        with self.db.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            query = "SELECT a.* FROM assets a"
            conditions = []
            params = []
            
            # --- Логика фильтрации по источникам ---
            web_domains = []
            folder_paths = []

            for src in selected_sources:
                if src == 'archdaily':
                    web_domains.append('archdaily.com')
                elif src == 'behance':
                    web_domains.append('behance.net')
                else:
                    folder_paths.append(src)

            source_conditions = []
            
            # 1. Фильтр по веб-доменам (через source_id)
            if web_domains:
                domain_placeholders = ','.join('?' for _ in web_domains)
                cur.execute(f"SELECT id FROM sources WHERE domain IN ({domain_placeholders})", web_domains)
                allowed_source_ids = [row['id'] for row in cur.fetchall()]
                if allowed_source_ids:
                    src_placeholders = ','.join('?' for _ in allowed_source_ids)
                    source_conditions.append(f"a.source_id IN ({src_placeholders})")
                    params.extend(allowed_source_ids)

            # 2. Фильтр по локальным папкам (через префикс пути)
            if folder_paths:
                folder_sub_conditions = []
                for path in folder_paths:
                    # Нормализуем путь: превращаем всё в один тип слэша (/) для сравнения
                    search_path = path.replace('\\', '/')
                    if not search_path.endswith('/'):
                        search_path += '/'
                    
                    # В SQL мы тоже превращаем все слэши в / перед сравнением
                    # Это гарантирует совпадение даже если в БД каша из слэшей
                    folder_sub_conditions.append("REPLACE(a.local_path, '\\', '/') LIKE ?")
                    params.append(search_path + "%")
                
                if folder_sub_conditions:
                    source_conditions.append(f"({' OR '.join(folder_sub_conditions)})")

            if source_conditions:
                conditions.append(f"({' OR '.join(source_conditions)})")
            else:
                # Источники выбраны в UI, но в БД их еще нет
                self.gallery_model.setAssets([])
                return

            # Categories filtering based on current_category
            if self.current_category == "3D Models":
                conditions.append("a.category = '3d_render'")
            elif self.current_category == "Textures":
                conditions.append("a.category = 'textures'")
            # Add custom if needed
            
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY a.created_at DESC"
            
            cur.execute(query, params)
            
            assets = []
            for row_raw in cur.fetchall():
                row = dict(row_raw)
                assets.append(Asset(
                    id=row['id'], original_url=row['original_url'],
                    thumbnail_path=row['thumbnail_path'], phash=row['phash'],
                    width=row['width'], height=row['height'],
                    category=row.get('category', '3d_render'),
                    image_type=row.get('image_type', 'Photography'),
                    local_path=row.get('local_path', ''),
                    is_favorite=bool(row.get('is_favorite', 0))
                ))
            self.gallery_model.setAssets(assets)

    def _refresh_library(self):
        from PyQt6.QtGui import QStandardItemModel, QStandardItem
        model = QStandardItemModel()
        model.setHorizontalHeaderLabels(["#", "Studio", "URL", "Category", "W×H", "Date", "pHash"])

        with self.db.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT a.id, COALESCE(s.domain, 'unknown') as domain, a.original_url, 
                       a.category, a.width, a.height, a.created_at, a.phash
                FROM assets a
                LEFT JOIN sources s ON a.source_id = s.id
                ORDER BY a.created_at DESC
            """)

            for row_raw in cur.fetchall():
                row = dict(row_raw)
                model.appendRow([
                    QStandardItem(str(row['id'])),
                    QStandardItem(str(row['domain'])),
                    QStandardItem(str(row['original_url'])[:80] + "..."),
                    QStandardItem(str(row.get('category', '3d_render'))),
                    QStandardItem(f"{row['width']}×{row['height']}"),
                    QStandardItem(str(row['created_at'])[:16]),
                    QStandardItem(str(row['phash'])[:8])
                ])

        self.library_table.setModel(model)
        self.library_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

    def _on_library_doubleclick(self, index):
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        row = index.row()
        model = self.library_table.model()
        url_item = model.item(row, 2)
        if url_item:
            asset_id = model.item(row, 0).text()
            with self.db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT original_url FROM assets WHERE id = ?", (asset_id,))
                db_row = cur.fetchone()
                if db_row:
                    QDesktopServices.openUrl(QUrl(db_row[0]))

    def _show_library_context_menu(self, pos):
        menu = QMenu(self)
        open_action = QAction("🌐 Открыть URL в Браузере", self)
        open_action.triggered.connect(lambda: self._on_library_doubleclick(self.library_table.currentIndex()))

        delete_action = QAction("🗑 Удалить", self)
        delete_action.triggered.connect(self._delete_selected_asset)

        menu.addAction(open_action)
        menu.addAction(delete_action)
        menu.exec(self.library_table.viewport().mapToGlobal(pos))

    def _delete_selected_asset(self):
        idx = self.library_table.currentIndex()
        if not idx.isValid(): return
        
        # Получаем объект Asset из ID в таблице
        model = self.library_table.model()
        asset_id = int(model.item(idx.row(), 0).text())
        
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM assets WHERE id = ?", (asset_id,))
            row = cur.fetchone()
            if row:
                asset = Asset(
                    id=row['id'], original_url=row['original_url'],
                    thumbnail_path=row['thumbnail_path'], phash=row['phash'],
                    image_type=row['image_type'] or 'Photography'
                )
                self._delete_assets_batch([asset])

    def closeEvent(self, event):
        if self.active_scraper:
            self.active_scraper.cancel()
        if self.active_indexer:
            self.active_indexer.cancel()
        QThreadPool.globalInstance().waitForDone(2000)
        super().closeEvent(event)

    # === Parser Logic ===
    
    def start_scrape(self, parser_name: str, url: str):
        if not url:
            QMessageBox.warning(self, "Ошибка", "Укажите URL для парсинга!")
            self.top_toolbar.set_scraping_state(False)
            return

        if "behance" in url.lower():
            parser_class = BehanceParser
        elif "archdaily" in url.lower():
            parser_class = ArchDailyParser
        else:
            parser_class = None

        if not parser_class:
            QMessageBox.warning(self, "Ошибка", "Неподдерживаемый сайт. Укажите Behance или ArchDaily URL.")
            self.top_toolbar.set_scraping_state(False)
            return

        self.top_toolbar.set_scraping_state(True)
        self.status_label.setText(f"Скрапинг: {url.split('/')[-1]}")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0) # indeterminate

        max_images = 10 if parser_class == ArchDailyParser else 5
        self.active_scraper = ScraperManager(parser_class, url, self.db, category="3d_render", max_images_per_project=max_images)
        self.active_scraper.signals.asset_processed.connect(self.on_new_asset)
        self.active_scraper.signals.finished.connect(self.on_scrape_finished)
        self.active_scraper.signals.error.connect(self.on_scrape_error)
        QThreadPool.globalInstance().start(self.active_scraper)

    def stop_scrape(self):
        if self.active_scraper:
            self.active_scraper.cancel()
            self.active_scraper = None
        self.top_toolbar.set_scraping_state(False)
        self.status_label.setText("Остановлено")
        self.progress_bar.setVisible(False)

    @pyqtSlot(object)
    def on_new_asset(self, asset):
        current_assets = self.gallery_model.assets.copy()
        current_assets.insert(0, asset)
        self.gallery_model.setAssets(current_assets)
        self._refresh_library()

    @pyqtSlot(str)
    def on_scrape_finished(self, url):
        self.active_scraper = None
        self.top_toolbar.set_scraping_state(False)
        self.status_label.setText("Готово")
        self.progress_bar.setVisible(False)
        self._refresh_library()
        self.update_sources_panel()

    @pyqtSlot(str, str)
    def on_scrape_error(self, url, error):
        self.active_scraper = None
        self.top_toolbar.set_scraping_state(False)
        self.status_label.setText("Ошибка скрапинга")
        self.progress_bar.setVisible(False)
        QMessageBox.warning(self, "Ошибка", f"Скрапер завершился с ошибкой: {error}")

    # === AI SigLIP ===

    def _ensure_ai(self):
        if self.ai is not None:
            return self.ai
            
        if self.ai_initializing:
            # Ждем завершения фоновой инициализации, если она идет
            import time
            while self.ai_initializing:
                QApplication.processEvents()
                time.sleep(0.1)
            return self.ai

        # Если инициализация еще не начиналась или упала, запускаем синхронно
        self.status_label.setText("⏳ Загрузка SigLIP AI...")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        from ai.engine import AiEngine
        try:
            self.ai = AiEngine()
        finally:
            QApplication.restoreOverrideCursor()
        return self.ai

    def start_indexing(self):
        if self.active_indexer:
            return

        unindexed = self.db.get_unindexed_assets()
        if not unindexed:
            total_in_index = self.faiss_mgr.index.ntotal
            QMessageBox.information(self, "Всё проиндексировано", f"Векторов в базе: {total_in_index}")
            return

        self._ensure_ai()
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(unindexed))
        self.progress_bar.setValue(0)

        from ai.index_worker import SigLipIndexWorker
        self.active_indexer = SigLipIndexWorker(self.db, self.faiss_mgr, self.ai, unindexed)
        self.active_indexer.signals.progress.connect(self._on_index_progress)
        self.active_indexer.signals.finished.connect(self._on_index_finished)
        self.active_indexer.signals.error.connect(self._on_index_error)
        QThreadPool.globalInstance().start(self.active_indexer)

    @pyqtSlot(int, int, str)
    def _on_index_progress(self, current: int, total: int, info: str):
        self.progress_bar.setValue(current)
        self.status_label.setText(f"Индексация [{current}/{total}]")

    @pyqtSlot(int)
    def _on_index_finished(self, indexed: int):
        self.active_indexer = None
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"✅ Проиндексировано: {indexed} ассетов")

    @pyqtSlot(str)
    def _on_index_error(self, error: str):
        self.active_indexer = None
        self.progress_bar.setVisible(False)
        self.status_label.setText("❌ Ошибка индексации")
        QMessageBox.warning(self, "Ошибка", error)

    # === Search Logic ===

    def _on_clear_search(self):
        # Восстанавливаем оригинальный вызов
        # Поиск очищается, источники включены все, загружаем галерею с учетом источников
        self._load_assets_for_gallery()
        self.status_label.setText("Сброс фильтров. Показаны все выбранные источники.")

    def _perform_visual_search(self, text: str, img_path: str, threshold: float, sources: list):
        if not text and not img_path:
            return
            
        if self.faiss_mgr.index.ntotal == 0:
            QMessageBox.warning(self, "Пустая база", "База векторов пуста. Сначала выполните индексацию.")
            return

        # Prevent concurrent searches — SigLIP model is not thread-safe
        if self.active_searcher:
            return

        self.status_label.setText("🔎 Выполнение гибридного поиска...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.search_threshold = threshold
        self.search_sources = sources

        class SearchWorker(QRunnable):
            class Signals(QObject):
                result = pyqtSignal(np.ndarray, str)
                error = pyqtSignal(str)

            def __init__(self, ai, text, img_path):
                super().__init__()
                self.ai = ai
                self.text = text
                self.img_path = img_path
                self.signals = self.Signals()

            def run(self):
                try:
                    if not self.ai:
                        from ai.engine import AiEngine
                        self.ai = AiEngine()
                        
                    v_final = None
                    if self.text and self.img_path:
                        v_txt = self.ai.get_text_embedding(self.text)
                        v_img = self.ai.get_image_embedding(self.img_path)
                        # Гибридное смешивание, 60% картинка, 40% текст (как в ТЗ)
                        v_final = (v_img * 0.6 + v_txt * 0.4).astype('float32')
                    elif self.text:
                        v_final = self.ai.get_text_embedding(self.text).astype('float32')
                    elif self.img_path:
                        v_final = self.ai.get_image_embedding(self.img_path).astype('float32')
                    
                    if v_final is not None:
                        v_final = v_final.reshape(1, -1)
                        self.signals.result.emit(v_final, "гибридного запроса" if self.text and self.img_path else "запроса")
                except Exception as e:
                    self.signals.error.emit(str(e))

        worker = SearchWorker(self.ai, text, img_path)
        worker.signals.result.connect(self._on_search_result)
        worker.signals.error.connect(self._on_search_error)
        self.active_searcher = worker
        QThreadPool.globalInstance().start(worker)

    @pyqtSlot(np.ndarray, str)
    def _on_search_result(self, vector, query_info):
        self.active_searcher = None
        self.progress_bar.setVisible(False)
        self._search_vectors(vector, query_info)

    @pyqtSlot(str)
    def _on_search_error(self, error):
        self.active_searcher = None
        self.progress_bar.setVisible(False)
        self.status_label.setText("❌ Ошибка поиска")
        QMessageBox.warning(self, "Ошибка поиска", error)

    def _search_vectors(self, vector: np.ndarray, query_info: str):
        k = min(500, self.faiss_mgr.index.ntotal)
        distances, ids = self.faiss_mgr.search(vector, k=k)

        # self.search_threshold varies from 0.0 (Широкий) to 1.0 (Точный)
        # For L2 normalized vectors, distance goes from 0 (identical) to 2 (orthogonal).
        # We use an exponential curve so the slider feels intuitive:
        #   threshold 0.0 -> max_dist 2.0  (show everything)
        #   threshold 0.5 -> max_dist ~0.28 (moderate filtering)
        #   threshold 1.0 -> max_dist 0.01 (only near-identical, ~100% match)
        import math
        max_distance = 2.0 * math.exp(-5.3 * self.search_threshold)

        results = [(dist, int(aid)) for dist, aid in zip(distances, ids) if aid > 0 and dist <= max_distance]
        
        if not results:
            self.status_label.setText("Ничего не найдено (попробуйте сделать поиск шире).")
            self.gallery_model.setAssets([])
            return

        asset_ids = [aid for _, aid in results]

        # Фильтруем по источникам
        web_domains = []
        folder_paths = []

        for src in self.search_sources:
            if src == 'archdaily':
                web_domains.append('archdaily.com')
            elif src == 'behance':
                web_domains.append('behance.net')
            else:
                folder_paths.append(src)

        with self.db.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            
            source_conditions = []
            params = asset_ids.copy()
            
            # 1. Веб-источники
            if web_domains:
                domain_placeholders = ','.join('?' for _ in web_domains)
                cur.execute(f"SELECT id FROM sources WHERE domain IN ({domain_placeholders})", web_domains)
                allowed_source_ids = [row['id'] for row in cur.fetchall()]
                if allowed_source_ids:
                    src_placeholders = ','.join('?' for _ in allowed_source_ids)
                    source_conditions.append(f"source_id IN ({src_placeholders})")
                    params.extend(allowed_source_ids)

            # 2. Локальные папки (префикс пути)
            if folder_paths:
                folder_sub_conditions = []
                for path in folder_paths:
                    search_path = path.replace('\\', '/')
                    if not search_path.endswith('/'): search_path += '/'
                    folder_sub_conditions.append("REPLACE(local_path, '\\', '/') LIKE ?")
                    params.append(search_path + "%")
                if folder_sub_conditions:
                    source_conditions.append(f"({' OR '.join(folder_sub_conditions)})")

            placeholders = ','.join('?' for _ in asset_ids)
            query = f"SELECT * FROM assets WHERE id IN ({placeholders})"
            
            if source_conditions:
                query += " AND (" + " OR ".join(source_conditions) + ")"
            else:
                # Если ничего не выбрано (или выбраны папки, которых нет в базе)
                query += " AND 1=0"
            
            cur.execute(query, params)
            rows = {row['id']: dict(row) for row in cur.fetchall()}

        search_assets = []
        for dist, aid in results:
            if aid in rows:
                row = rows[aid]
                # If we want to strictly apply threshold:
                # if dist > (self.search_threshold * magic_number): continue
                search_assets.append(Asset(
                    id=row['id'], original_url=row['original_url'],
                    thumbnail_path=row['thumbnail_path'], phash=row['phash'],
                    width=row['width'], height=row['height'],
                    category=row.get('category', '3d_render'),
                    image_type=row.get('image_type', 'Photography'),
                    local_path=row.get('local_path', ''),
                    is_favorite=bool(row.get('is_favorite', 0))
                ))

        self.gallery_model.setAssets(search_assets)
        self.status_label.setText(f"Найдено {len(search_assets)} совпадений")
        self.tabs.setCurrentIndex(0) # Убедимся, что открыта галерея
