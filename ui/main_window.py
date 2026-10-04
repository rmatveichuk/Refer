from PyQt6.QtWidgets import (
    QMainWindow, QVBoxLayout, QDialog, QHBoxLayout, QWidget, QMessageBox, QPushButton, 
    QLabel, QProgressBar, QTabWidget, QTableView, QHeaderView, 
    QAbstractItemView, QMenu, QApplication, QSlider, QToolButton
)
from PyQt6.QtCore import Qt, QThreadPool, pyqtSlot, QTimer, QRunnable, QObject, pyqtSignal
from PyQt6.QtGui import QAction

from ui.widgets.gallery_view import GalleryView
from ui.widgets.lazy_model import AssetListModel
from ui.widgets.top_toolbar import TopToolbar
from ui.widgets.search_panel import SearchPanel
from database.search_repository import SearchRepository, SearchFilters
from ui.workers.search_worker import SearchWorker, embedding_key
from ui.workers.results_worker import ResultsWorker
from ui.widgets.tag_manager import TagManagerDialog
from ui.widgets.tag_chip import TagChip
from ui.widgets.flow_layout import FlowLayout
from database.db_manager import DatabaseManager
from database.models import Asset
from database.visibility_store import VisibilityStore
from database.source_group_store import SourceGroupStore
from ui.hidden_assets_dialog import HiddenAssetsDialog
from scrapers.manager import ScraperManager
from scrapers.behance_parser import BehanceParser
from scrapers.archdaily_parser import ArchDailyParser
from scrapers.local_folder import LocalFolderParser
from database.faiss_manager import FaissManager
from ui.settings_dialog import SettingsDialog
from ui.catalog_dialog import CatalogDialog, FolderImportDialog
from PyQt6.QtWidgets import QFileDialog
import config
from ui.translations import tr

import sqlite3
import logging
import os
import numpy as np

logger = logging.getLogger(__name__)

class MainWindow(QMainWindow):
    scraping_state_changed = pyqtSignal(bool)

    def _set_scraping_state(self, is_scraping: bool):
        self.top_toolbar.set_scraping_state(is_scraping)
        self.scraping_state_changed.emit(is_scraping)
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Refer — AI Asset Manager")
        self.setMinimumSize(1200, 850)
        self.setStyleSheet("""
            QWidget { background-color: #121212; color: #e0e0e0; }
            QPushButton { background-color: #282828; color: #e0e0e0;
                          border: 1px solid #3b3b3b; border-radius: 4px; padding: 5px 9px; }
            QPushButton:hover { background-color: #353535; }
            QPushButton:checked { background-color: #414141; }
            QToolButton { background-color: #282828; color: #e0e0e0;
                          border: 1px solid #3b3b3b; border-radius: 4px; padding: 5px 24px 5px 9px; }
            QToolButton:hover { background-color: #353535; }
            QToolButton::menu-button { border-left: 1px solid #444; width: 16px; }
            QMenu { background-color: #242424; color: #e0e0e0; border: 1px solid #444; }
            QMenu::item { padding: 7px 24px; }
            QMenu::item:selected { background-color: #414141; }
            QMenu::item:disabled { color: #777; }
            QComboBox, QLineEdit { background-color: #1d1d1d; color: #e0e0e0;
                                  border: 1px solid #393939; padding: 4px; }
        """)
        
        self.db = DatabaseManager(config.DB_PATH)
        self.visibility = VisibilityStore(config.APP_ROAMING_DIR / "hidden_assets.json")
        self.group_store = SourceGroupStore(db=self.db)
        self.faiss_mgr = FaissManager(config.FAISS_PATH, dimension=config.VECTOR_DIMENSION)
        self.ai = None
        
        self.active_scraper = None
        self.active_indexer = None
        self.active_searcher = None
        self.ai_initializing = False  # Флаг для предотвращения двойной инициализации
        self._requested_search = None
        self._embedding_cache = None
        self.current_search_text = ""
        self.search_tags = []
        self.result_limit = config.SEARCH_PAGE_SIZE
        self._query_info = {}
        self._results_revision = 0
        self._closing = False
        self._active_results_worker = None
        self._pending_results_worker = None
        self._results_pool = QThreadPool(self)
        self._results_pool.setMaxThreadCount(1)
        self._ai_pool = QThreadPool(self)
        self._ai_pool.setMaxThreadCount(1)
        self._ai_pool.setExpiryTimeout(-1)
        self.search_threshold = 0.0
        self.search_sources = []
        self.filter_project_id = None
        self.filter_author = None
        
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
        
        class InitSignals(QObject):
            finished = pyqtSignal(object)  # AiEngine or None
            error = pyqtSignal(str)

        class InitWorker(QRunnable):
            def __init__(self):
                super().__init__()
                self.signals = InitSignals()
            def run(self):
                try:
                    from ai.runtime import create_engine
                    engine = create_engine()
                    self.signals.finished.emit(engine)
                except Exception as e:
                    logger.error(f"Background AI init failed: {e}")
                    self.signals.error.emit(str(e))

        self._init_worker = InitWorker()
        self._init_worker.signals.finished.connect(self._on_ai_ready)
        self._init_worker.signals.error.connect(self._on_ai_error)
        self._start_ai_worker(self._init_worker)

    @pyqtSlot(object)
    def _on_ai_ready(self, engine):
        self._init_worker = None
        if self._closing:
            if hasattr(engine, 'close'):
                engine.close()
            return
        self.ai = engine
        self.ai_initializing = False
        self.status_label.setText("✅ AI готов")
        self._run_requested_search()

    def _start_ai_worker(self, worker):
        self._ai_pool.start(worker)

    @pyqtSlot(str)
    def _on_ai_error(self, error_msg):
        self._init_worker = None
        self.ai_initializing = False
        self.progress_bar.hide()
        self.status_label.setText("Ошибка загрузки AI: " + error_msg)

    def _init_ui(self):
        # Search and library controls share the sidebar; results occupy the right.
        
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
        self.top_toolbar.catalogs_requested.connect(self._open_catalogs)
        self.top_toolbar.hidden_assets_requested.connect(self._open_hidden_assets)
        self.top_toolbar.index_requested.connect(self.start_indexing)
        self.top_toolbar.cleanup_requested.connect(self._cleanup_missing_files)
        self.top_toolbar.language_changed.connect(self.retranslate_ui)
        self.top_toolbar.settings_requested.connect(self._open_settings)
        main_layout.addWidget(self.top_toolbar)
        self.top_toolbar.show()

        # Выведем статус-бар и прогресс-бар в общий доступ MainWindow, для совместимости
        self.status_label = self.top_toolbar.status_label
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.status_label.setStyleSheet("color: #959b91; font-size: 11px; padding: 2px 6px;")
        self.status_label.show()
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().addWidget(self.status_label, 1)
        self._settings_dialog = None
        
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
        self.search_panel = SearchPanel(db=self.db, group_store=self.group_store)
        self.search_panel.search_triggered.connect(self._update_breadcrumbs)
        self.search_panel.manage_tags_requested.connect(self._open_tag_manager)
        self.search_panel.extract_tags_requested.connect(self._extract_tags_from_image)
        self.search_panel.clear_triggered.connect(self._on_clear_search)
        self.search_panel.filters_reset.connect(self._on_reset_filters)
        self.search_panel.remove_source_requested.connect(self._remove_source_folder)
        self.search_panel.catalogs_requested.connect(self._open_catalogs)
        self.filters_button = self.search_panel.filters_button
        self.reset_filters_button = self.search_panel.reset_filters_button
        self._setup_catalog_menu()
        self.search_panel.btn_catalogs.setMenu(self.catalog_menu)
        self.search_panel.btn_catalogs.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        content_layout.addWidget(self.search_panel)

        # --- Right: Gallery & Library Tabs ---
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane { border: none; border-left: 1px solid #222; }
            QTabBar::tab { background: #1a1a1a; color: #777; padding: 10px 20px; border: none; border-bottom: 2px solid transparent; }
            QTabBar::tab:hover { color: #aaa; background: #222; }
            QTabBar::tab:selected { background: #222; color: #fff; border-bottom: 2px solid #fff; font-weight: bold; }
        """)
        
        # Gallery and table share one query and one set of results.
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._previous_tab_index = 0
        results_widget = QWidget()
        results_layout = QVBoxLayout(results_widget)
        results_layout.setContentsMargins(10, 6, 10, 6)
        results_layout.setSpacing(4)
        heading = QHBoxLayout()
        self.result_title = QLabel("Референсы")
        self.result_title.setStyleSheet("font-size: 18px; color: #e4e8df;")
        heading.addWidget(self.result_title)
        self.result_count = QLabel()
        self.result_count.setStyleSheet("color: #959b91;")
        heading.addWidget(self.result_count)
        heading.addStretch()
        results_layout.addLayout(heading)
        content_layout.addWidget(results_widget, 1)

        # --- Breadcrumbs as Corner Widget ---
        self.breadcrumbs_widget = QWidget()
        self.breadcrumbs_layout = FlowLayout(self.breadcrumbs_widget)
        self.breadcrumbs_layout.setContentsMargins(10, 0, 10, 0)
        self.breadcrumbs_widget.hide()
        results_layout.addWidget(self.breadcrumbs_widget)
        controls_widget = QWidget()
        controls = QHBoxLayout(controls_widget)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(QLabel("Размер"))
        self.thumbnail_size = QSlider(Qt.Orientation.Horizontal)
        self.thumbnail_size.setRange(140, 320)
        self.thumbnail_size.setValue(220)
        self.thumbnail_size.setFixedWidth(100)
        self.thumbnail_size.valueChanged.connect(lambda size: self.gallery.set_thumbnail_size(size))
        controls.addWidget(self.thumbnail_size)
        self.select_all_button = QPushButton(tr("select_all"))
        self.select_all_button.clicked.connect(self._select_all_gallery)
        controls.addWidget(self.select_all_button)
        self.delete_button = QPushButton(tr("hide"))
        self.delete_button.clicked.connect(self._delete_selected_gallery)
        controls.addWidget(self.delete_button)
        self.tabs.setCornerWidget(controls_widget, Qt.Corner.TopRightCorner)
        results_layout.addWidget(self.tabs, 1)
        self.more_button = QPushButton(f"Показать ещё {config.SEARCH_PAGE_SIZE}")
        self.more_button.clicked.connect(self._show_more_results)
        self.more_button.hide()
        results_layout.addWidget(self.more_button)

        self._setup_gallery_tab()
        self._setup_library_tab()
        
        self.retranslate_ui()

    def _setup_catalog_menu(self):
        self.catalog_menu = QMenu(self.search_panel.btn_catalogs)
        self.catalog_menu.addAction("Управление каталогами…", self._open_catalogs)
        self.catalog_menu.addAction("Добавить папку…", self.top_toolbar.btn_add_folder.click)
        self.catalog_menu.addAction("Добавить с сайта…", lambda: self.top_toolbar.show_import_dialog(self))
        self.catalog_menu.addSeparator()
        self.catalog_menu.addAction("Скрытые изображения…", self.top_toolbar.btn_hidden.click)
        maintenance = self.catalog_menu.addMenu("Обслуживание")
        index_action = maintenance.addAction("Индексация", self.top_toolbar.btn_index.click)
        check_action = maintenance.addAction("Проверить файлы", self.top_toolbar.btn_cleanup.click)

        def refresh_actions():
            index_action.setEnabled(self.top_toolbar.btn_index.isEnabled())
            check_action.setEnabled(self.top_toolbar.btn_cleanup.isEnabled())
            check_action.setText(self.top_toolbar.btn_cleanup.text())

        self.catalog_menu.aboutToShow.connect(refresh_actions)
        maintenance.aboutToShow.connect(refresh_actions)
        self.search_panel.btn_catalogs.setMenu(self.catalog_menu)

    def _on_tab_changed(self, index):
        # Changing presentation does not alter the query or filters.
        self._previous_tab_index = index

    def _select_all_gallery(self):
        (self.gallery if self.tabs.currentIndex() == 0 else self.library_table).selectAll()

    def _delete_selected_gallery(self):
        if self.tabs.currentIndex() == 0:
            indexes = self.gallery.selectionModel().selectedIndexes()
            assets = [self.gallery_model.assets[index.row()] for index in indexes]
        else:
            rows = self.library_table.selectionModel().selectedRows()
            selected = {int(self.library_table.model().item(index.row(), 0).text()) for index in rows}
            assets = [asset for asset in self.gallery_model.assets if asset.id in selected]
        if not assets:
            QMessageBox.information(self, "Ничего не выбрано", "Выделите изображения для удаления.")
            return
        self._delete_assets_batch(assets)

    def _filter_favorites(self):
        self.search_panel.favorite_check.setChecked(not self.search_panel.favorite_check.isChecked())

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
        self.tabs.addTab(gallery_tab, tr("gallery"))

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
        self.tabs.addTab(library_tab, tr("table"))

    def _delete_assets_batch(self, assets: list):
        """Compatibility entry point: reversible hiding, never physical deletion."""
        ids = list(dict.fromkeys(asset.id for asset in assets if asset.id is not None))
        if not ids:
            return False
        rows = []
        try:
            with self.db.get_connection() as conn:
                for start in range(0, len(ids), 500):
                    chunk = ids[start:start + 500]
                    placeholders = ",".join("?" for _ in chunk)
                    rows.extend(dict(row) for row in conn.execute(f"SELECT * FROM assets WHERE id IN ({placeholders})", chunk))
        except Exception as error:
            QMessageBox.critical(self, "Скрытие не выполнено", "Не удалось прочитать изображения: " + str(error))
            return False
        if not rows:
            return False

        parent_window = QApplication.activeWindow() or self
        msg_box = QMessageBox(parent_window)
        msg_box.setIcon(QMessageBox.Icon.Question)

        n = len(rows)
        if config.CURRENT_LANGUAGE == "ru":
            msg_box.setWindowTitle("Скрыть изображения")
            if 11 <= (n % 100) <= 19:
                count_str = f"{n} изображений"
            elif n % 10 == 1:
                count_str = f"{n} изображение"
            elif 2 <= (n % 10) <= 4:
                count_str = f"{n} изображения"
            else:
                count_str = f"{n} изображений"
            msg_box.setText(
                f"Скрыть {count_str} из библиотеки?\n\n"
                "Вернуть их можно через «Каталоги → Скрытые изображения». Файлы, теги и избранное сохраняются."
            )
            yes_btn = msg_box.addButton("Да", QMessageBox.ButtonRole.YesRole)
            no_btn = msg_box.addButton("Нет", QMessageBox.ButtonRole.NoRole)
        else:
            msg_box.setWindowTitle("Hide Images")
            count_str = f"{n} image" if n == 1 else f"{n} images"
            msg_box.setText(
                f"Hide {count_str} from library?\n\n"
                "You can restore them via Catalogs → Hidden Images. Files, tags, and favorites are preserved."
            )
            yes_btn = msg_box.addButton("Yes", QMessageBox.ButtonRole.YesRole)
            no_btn = msg_box.addButton("No", QMessageBox.ButtonRole.NoRole)

        yes_btn.setStyleSheet("""
            QPushButton {
                background-color: #1976D2;
                color: #ffffff;
                font-weight: bold;
                border: 1px solid #2196F3;
                border-radius: 4px;
                padding: 6px 20px;
                min-width: 60px;
            }
            QPushButton:hover {
                background-color: #1565C0;
            }
            QPushButton:focus {
                border: 2px solid #90CAF9;
            }
        """)
        no_btn.setStyleSheet("""
            QPushButton {
                background-color: #2e2e2e;
                color: #cccccc;
                border: 1px solid #444444;
                border-radius: 4px;
                padding: 6px 20px;
                min-width: 60px;
            }
            QPushButton:hover {
                background-color: #3e3e3e;
            }
        """)

        msg_box.setDefaultButton(yes_btn)
        msg_box.setEscapeButton(no_btn)
        yes_btn.setFocus()

        msg_box.exec()
        if msg_box.clickedButton() != yes_btn:
            return False
        try:
            self.visibility.hide(rows)
        except Exception as error:
            QMessageBox.critical(self, "Скрытие не выполнено", str(error))
            return False

        # Мгновенно удаляем скрытые ассеты из памяти модели галереи и таблицы библиотеки
        hidden_ids = set(ids)
        current_assets = [a for a in self.gallery_model.assets if a.id not in hidden_ids]
        self.gallery_model.setAssets(current_assets)
        self._refresh_library()
        self.result_count.setText(f"Показано {len(current_assets)}")

        # Если открыт просмотрщик, убираем удалённые ассеты и из него
        if hasattr(self, 'gallery') and self.gallery._viewer_window and self.gallery._viewer_window.isVisible():
            viewer = self.gallery._viewer_window
            viewer.assets = [a for a in viewer.assets if a.id not in hidden_ids]
            if not viewer.assets:
                viewer.close()
            else:
                if viewer.current_index >= len(viewer.assets):
                    viewer.current_index = len(viewer.assets) - 1
                viewer._load_full_image()

        self._load_assets_for_gallery()
        self.status_label.setText(f"Скрыто изображений: {len(rows)}. Восстановление — «Каталоги → Скрытые изображения».")
        return True

    def _open_hidden_assets(self):
        try:
            dialog = HiddenAssetsDialog(self.db, self.visibility, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._restore_hidden_assets(dialog.restore_ids)
        except Exception as error:
            QMessageBox.critical(self, "Скрытые изображения", str(error))

    def _restore_hidden_assets(self, ids):
        self.visibility.restore(ids)
        self._load_assets_for_gallery()
        self.status_label.setText(f"Восстановлено изображений: {len(ids)}. Текущие фильтры сохраняются.")

    def _cleanup_missing_files(self):
        """Inspect availability; never infer deletion from an unavailable disk."""
        if getattr(self, "_availability_worker", None):
            self._availability_worker.cancellation.set()
            self.status_label.setText("Остановка проверки после текущего обращения к файлу…")
            return
        self.status_label.setText("Проверка доступности файлов…")
        self.progress_bar.show()
        self.progress_bar.setRange(0, 0)

        class CheckSignals(QObject):
            finished = pyqtSignal(object)
            error = pyqtSignal(str)
            progress = pyqtSignal(int, int)

        class CheckWorker(QRunnable):
            def __init__(self, db):
                super().__init__()
                self.db = db
                self.signals = CheckSignals()
                from threading import Event
                self.cancellation = Event()

            def run(self):
                from database.availability import inspect_files
                try:
                    self.signals.finished.emit(inspect_files(self.db, self.cancellation, self.signals.progress.emit))
                except Exception as error:
                    self.signals.error.emit(str(error))

        worker = CheckWorker(self.db)
        self._availability_worker = worker
        self.top_toolbar.btn_cleanup.setText("Остановить проверку")
        worker.signals.finished.connect(self._on_availability_finished)
        worker.signals.error.connect(self._on_availability_error)
        worker.signals.progress.connect(self._on_availability_progress)
        QThreadPool.globalInstance().start(worker)

    def _on_availability_progress(self, checked, total):
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(checked)
        self.status_label.setText(f"Проверка файлов: {checked} из {total}")

    def _on_availability_finished(self, report):
        from ui.availability_dialog import AvailabilityDialog
        self._availability_worker = None
        self.top_toolbar.btn_cleanup.setText("Проверить файлы")
        self.progress_bar.hide()
        prefix = "Проверка остановлена. " if report.cancelled else ""
        self.status_label.setText(prefix + f"Проверено {report.checked}; проблем с доступом: {len(report.issues)}. Данные сохранены.")
        self._availability_dialog = AvailabilityDialog(report, self)
        self._availability_dialog.show()

    def _on_availability_error(self, error):
        self._availability_worker = None
        self.top_toolbar.btn_cleanup.setText("Проверить файлы")
        self.progress_bar.hide()
        self.status_label.setText("Не удалось проверить доступность: " + error)

    def update_sources_panel(self):
        # 1. Получаем "точки входа" из таблицы sources
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT domain FROM sources WHERE domain LIKE '_:%' OR domain LIKE '/%'")
            source_folders = [row['domain'] for row in (cur.fetchall() or [])]

            # 2. Получаем ВСЕ папки, в которых лежат проиндексированные файлы
            cur.execute("SELECT DISTINCT local_path FROM assets WHERE local_path IS NOT NULL AND local_path != ''")
            asset_paths = [row['local_path'] for row in cur.fetchall() if row['local_path']]
            
            import os
            import config
            
            # Пути к системным папкам, которые нужно скрыть из списка источников
            hidden_prefixes = [
                str(config.THUMBNAILS_DIR).lower(),
                str(config.APP_LOCAL_DIR).lower(),
                str(config.APP_ROAMING_DIR).lower()
            ]
            
            # Поддержка старых путей (от предыдущих версий)
            app_data = os.getenv('LOCALAPPDATA')
            if app_data:
                hidden_prefixes.append(os.path.join(app_data, 'ReferAssetManager').lower())
            
            all_folders = set(source_folders)
            direct_folders = set()
            for p in asset_paths:
                folder = os.path.normpath(os.path.dirname(p))
                folder_lower = folder.lower()
                
                # Если папка лежит внутри системной директории (кэш миниатюр и т.д.) - игнорируем
                if any(folder_lower.startswith(hp) for hp in hidden_prefixes):
                    continue
                direct_folders.add(folder)
                    
                # Добавляем саму папку и ВСЕ её родительские папки вверх по дереву
                curr = folder
                while curr and len(curr) > 3:
                    all_folders.add(curr)
                    next_parent = os.path.dirname(curr)
                    if next_parent == curr: break # Дошли до корня
                    curr = next_parent
            
        self.search_panel.update_custom_folders(list(all_folders), direct_folders=direct_folders, group_store=self.group_store)

    def _add_folder(self):
        self._configure_folder()

    def _configure_folder(self, path=""):
        if self.active_scraper:
            self.status_label.setText("Дождитесь завершения текущего добавления изображений.")
            return
        dialog = FolderImportDialog(self, path=path, group_store=self.group_store)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._scan_folder(dialog.options())

    def _scan_folder(self, options):
        if self.active_scraper:
            self.status_label.setText("Добавление изображений уже выполняется.")
            return
        gid = options.get("group_id")
        if gid:
            self.group_store.assign_source(options["path"], gid)
            try:
                self.group_store.save()
            except Exception:
                pass
        self._set_scraping_state(True)
        self.status_label.setText("Сканирование: " + options["path"])
        self.progress_bar.show()
        self.progress_bar.setRange(0, 0)
        self.active_scraper = LocalFolderParser(options["path"], self.db, mode=options["mode"],
                                                recursive=options["recursive"], skip_deleted=options["skip_deleted"])
        self.active_scraper.signals.progress.connect(self._on_local_folder_progress)
        self.active_scraper.signals.finished.connect(self.on_scrape_finished)
        self.active_scraper.signals.error.connect(self.on_scrape_error)
        QThreadPool.globalInstance().start(self.active_scraper)

    def _open_catalogs(self):
        dialog = CatalogDialog(self.db, self.group_store, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.search_panel._sync_store_settings()
        self.update_sources_panel()
        self.search_panel._emit_search()
        if dialog.action:
            if dialog.action[0] == "rescan":
                self._configure_folder(dialog.action[1])
            elif dialog.action[0] == "add":
                self._configure_folder("")

    def _on_local_folder_progress(self, current: int, total: int, info: str):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.status_label.setText(f"Сканирование [{current}/{total}]: {info}")

    def _remove_source_folder(self, path_or_domain: str):
        """Disables a source in group_store; library rows and files are retained."""
        reply = QMessageBox.question(self, "Отключить источник",
            f"Отключить «{path_or_domain}» из поиска?\n\nДанные сохраняются. Локальные каталоги можно включить через «Каталоги».",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.group_store.set_source_disabled(path_or_domain, True)
        try:
            self.group_store.save()
        except Exception:
            pass
        self.search_panel._sync_store_settings()
        self.update_sources_panel()
        self.search_panel._emit_search()

    def _load_assets_for_gallery(self):
        self._update_breadcrumbs()

    def _refresh_library(self):
        from PyQt6.QtGui import QStandardItemModel, QStandardItem
        model = QStandardItemModel(self.library_table)
        model.setHorizontalHeaderLabels(["ID", "Файл", "Источник / URL", "Категория", "Размер", "Дата"])
        for asset in self.gallery_model.assets:
            model.appendRow([QStandardItem(str(asset.id)),
                             QStandardItem(os.path.basename(asset.local_path or asset.thumbnail_path or "")),
                             QStandardItem(asset.original_url or asset.local_path or ""),
                             QStandardItem(asset.category or ""),
                             QStandardItem(f"{asset.width} × {asset.height}"),
                             QStandardItem(str(asset.created_at)[:16])])
        previous = self.library_table.model()
        self.library_table.setModel(model)
        if previous:
            previous.deleteLater()
        self.library_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

    def _on_library_doubleclick(self, index):
        if not index.isValid():
            return
        asset_id = int(self.library_table.model().item(index.row(), 0).text())
        for row, asset in enumerate(self.gallery_model.assets):
            if asset.id == asset_id:
                self.gallery._on_item_clicked(self.gallery_model.index(row))
                return

    def _open_library_original(self):
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        index = self.library_table.currentIndex()
        if not index.isValid():
            return
        value = self.library_table.model().item(index.row(), 2).text()
        if value:
            QDesktopServices.openUrl(QUrl(value) if value.startswith(("http://", "https://")) else QUrl.fromLocalFile(value))

    def _show_library_context_menu(self, pos):
        menu = QMenu(self)
        open_action = QAction(tr("open_url"), self)
        open_action.triggered.connect(self._open_library_original)

        delete_action = QAction(tr("hide"), self)
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
                row = dict(row)
                asset = Asset(
                    id=row['id'], original_url=row['original_url'],
                    thumbnail_path=row['thumbnail_path'], phash=row['phash'],
                    local_path=row.get('local_path') or '',
                    project_id=row.get('project_id'),
                    image_type=row['image_type'] or 'Photography'
                )
                self._delete_assets_batch([asset])

    def retranslate_ui(self):
        self.tabs.blockSignals(True)
        self.tabs.setTabText(0, tr("gallery"))
        self.tabs.setTabText(1, tr("table"))
        self.select_all_button.setText(tr("select_all"))
        self.delete_button.setText(tr("hide"))
        
        self.status_label.setText(tr("ready"))
        self.search_panel.retranslate_ui()
        self.top_toolbar.retranslate_ui()
        
        if self._settings_dialog:
            self._settings_dialog.retranslate_ui()
            
        self.tabs.blockSignals(False)

    def _open_settings(self):
        self._settings_dialog = SettingsDialog(self)
        self._settings_dialog.language_changed.connect(self.retranslate_ui)
        self._settings_dialog.export_requested.connect(self._export_library)
        self._settings_dialog.import_requested.connect(self._import_library)
        self._settings_dialog.backup_requested.connect(self._backup_db)
        self._settings_dialog.relink_requested.connect(self._relink_images)
        self._settings_dialog.show()

    def _export_library(self):
        path, _ = QFileDialog.getSaveFileName(self, tr("export_library"), "", "Refer Package (*.refpack)")
        if path:
            if not path.endswith(".refpack"): path += ".refpack"
            from utils.library_manager import export_library
            try:
                success = export_library(config.DB_PATH, config.FAISS_PATH, path)
                if success:
                    QMessageBox.information(self, "Refer", tr("export_success"))
            except Exception as e:
                logger.error(f"Export failed: {e}")
                QMessageBox.critical(self, "Error", f"Export failed: {e}")

    def _import_library(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("import_library"), "", "Refer Package (*.refpack)")
        if not path: return
        
        # Choice dialog: Merge or Replace
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle(tr("import_mode"))
        msg_box.setText(tr("import_warning"))
        btn_replace = msg_box.addButton(tr("replace_library"), QMessageBox.ButtonRole.DestructiveRole)
        btn_merge = msg_box.addButton(tr("merge_library"), QMessageBox.ButtonRole.AcceptRole)
        btn_cancel = msg_box.addButton(tr("cancel"), QMessageBox.ButtonRole.RejectRole)
        msg_box.exec()
        
        clicked_btn = msg_box.clickedButton()
        if clicked_btn == btn_cancel: return
        
        mode = "replace" if clicked_btn == btn_replace else "merge"
        
        # Ask for images folder
        img_root = QFileDialog.getExistingDirectory(self, tr("select_image_folder"))
        if not img_root: return
        
        from utils.library_manager import import_library_replace, relink_paths, merge_libraries
        import shutil
        
        temp_dir = config.APP_LOCAL_DIR / "temp_import"
        try:
            db_ext, index_ext = import_library_replace(path, temp_dir)
            
            # Relink paths in the imported DB
            # Для импорта указываем img_root как новую папку для локальных файлов,
            # а миниатюры всегда направляем в стандартную папку текущей системы.
            relink_paths(db_ext, new_local_root=img_root, new_thumbnails_root=str(config.THUMBNAILS_DIR))
            
            if mode == "replace":
                # Full replace
                
                # 1. Backup current
                shutil.copy2(config.DB_PATH, str(config.DB_PATH) + ".bak")
                shutil.copy2(config.FAISS_PATH, str(config.FAISS_PATH) + ".bak")
                
                # 2. Overwrite
                # Очищаем старые WAL файлы, чтобы избежать коррупции БД
                wal_path = str(config.DB_PATH) + "-wal"
                shm_path = str(config.DB_PATH) + "-shm"
                if os.path.exists(wal_path): os.remove(wal_path)
                if os.path.exists(shm_path): os.remove(shm_path)
                
                shutil.copy2(db_ext, config.DB_PATH)
                shutil.copy2(index_ext, config.FAISS_PATH)
                
                QMessageBox.information(self, "Refer", tr("import_success"))
                
                # 3. Reload FAISS and DB state
                self.faiss_mgr = FaissManager(config.FAISS_PATH, dimension=config.VECTOR_DIMENSION)
                self._load_assets_for_gallery()
                self._refresh_library()
                self.update_sources_panel()
            else:
                # Merge
                merged, skipped = merge_libraries(self.db, db_ext, index_ext, self.faiss_mgr)
                QMessageBox.information(self, "Refer", f"Merged: {merged}, Skipped: {skipped}")
                self._load_assets_for_gallery()
                self._refresh_library()
                self.update_sources_panel()
        except Exception as e:
            logger.error(f"Import failed: {e}")
            QMessageBox.critical(self, "Error", f"Import failed: {e}")
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _backup_db(self):
        from utils.library_manager import backup_database
        try:
            path = backup_database(config.DB_PATH)
            if path:
                QMessageBox.information(self, "Refer", f"Backup saved: {os.path.basename(path)}")
        except Exception as e:
            logger.error(f"Backup failed: {e}")
            QMessageBox.critical(self, "Error", f"Backup failed: {e}")

    def _relink_images(self, new_root):
        from utils.library_manager import relink_paths
        try:
            # Считаем, что смена папки в настройках меняет папку миниатюр
            count = relink_paths(config.DB_PATH, new_thumbnails_root=new_root)
            QMessageBox.information(self, "Refer", f"Updated {count} paths.")
            self._load_assets_for_gallery()
        except Exception as e:
            logger.error(f"Relinking failed: {e}")
            QMessageBox.critical(self, "Error", f"Relinking failed: {e}")

    def closeEvent(self, event):
        self._closing = True
        self._results_revision += 1
        self._pending_results_worker = None
        if self.ai is not None and hasattr(self.ai, 'close'):
            self.ai.close()
        if getattr(self, "_availability_worker", None):
            self._availability_worker.cancellation.set()
        self.gallery_model.cancel_loads()
        self.gallery.thread_pool.waitForDone(2000)
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
            self._set_scraping_state(False)
            return

        if "behance" in url.lower():
            parser_class = BehanceParser
        elif "archdaily" in url.lower():
            parser_class = ArchDailyParser
        else:
            parser_class = None

        if not parser_class:
            QMessageBox.warning(self, "Ошибка", "Неподдерживаемый сайт. Укажите Behance или ArchDaily URL.")
            self._set_scraping_state(False)
            return

        self._set_scraping_state(True)
        self._scraped_count = 0
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
        self._set_scraping_state(False)
        self.status_label.setText("Остановлено")
        self.progress_bar.setVisible(False)

    @pyqtSlot(object)
    def on_new_asset(self, asset):
        self._scraped_count = getattr(self, '_scraped_count', 0) + 1
        self.status_label.setText(f"Загрузка: {self._scraped_count} изображений...")
        current_assets = self.gallery_model.assets.copy()
        current_assets.insert(0, asset)
        self.gallery_model.setAssets(current_assets)
        self._refresh_library()

    @pyqtSlot(str)
    def on_scrape_finished(self, url):
        self.active_scraper = None
        self._set_scraping_state(False)
        count = getattr(self, '_scraped_count', 0)
        self.status_label.setText(f"✅ Загрузка завершена: добавлено {count} изображений")
        self.progress_bar.setVisible(False)
        self._refresh_library()
        self.update_sources_panel()
        self._load_assets_for_gallery()

    @pyqtSlot(str, str)
    def on_scrape_error(self, url, error):
        self.active_scraper = None
        self._set_scraping_state(False)
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
        from ai.runtime import create_engine
        try:
            self.ai = create_engine()
        finally:
            QApplication.restoreOverrideCursor()
        return self.ai

    def start_indexing(self, force_all: bool = False):
        if self.active_indexer:
            return

        if force_all:
            self.db.reset_all_embeddings()
            self.faiss_mgr.reset_index()

        unindexed = self.db.get_unindexed_assets()
        if not unindexed:
            total_in_index = self.faiss_mgr.index.ntotal
            reply = QMessageBox.question(
                self, 
                "Библиотека проиндексирована", 
                f"Векторов в базе: {total_in_index}.\nВсе изображения уже проиндексированы.\n\nХотите переиндексировать библиотеку заново (например, новой моделью SigLIP 2)?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.start_indexing(force_all=True)
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
        self._query_info = {}
        self._update_breadcrumbs()

    def _on_reset_filters(self):
        self.filter_project_id = None
        self.filter_author = None
        self._update_breadcrumbs()

    @pyqtSlot(str)
    def _extract_tags_from_image(self, image_path: str):
        if not image_path:
            return
            
        if self.active_searcher:
            return
            
        if not self.ai:
            self._background_ai_init()
            self.status_label.setText("Дождитесь загрузки модели и повторите анализ изображения.")
            return
        self._extract_image = image_path
        self.status_label.setText("Анализ изображения (Zero-Shot Classification)...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)

        class ExtractTagsWorker(QRunnable):
            class Signals(QObject):
                result = pyqtSignal(list)
                error = pyqtSignal(str)

            def __init__(self, ai, img_path):
                super().__init__()
                self.ai = ai
                self.img_path = img_path
                self.signals = self.Signals()

            def run(self):
                try:
                    if not self.ai:
                        from ai.runtime import create_engine
                        self.ai = create_engine()
                        
                    tags = self.ai.extract_tags(self.img_path)
                    self.signals.result.emit(tags)
                except Exception as e:
                    self.signals.error.emit(str(e))

        worker = ExtractTagsWorker(self.ai, image_path)
        worker.signals.result.connect(self._on_extract_tags_result)
        worker.signals.error.connect(self._on_search_error)
        self.active_searcher = worker
        QThreadPool.globalInstance().start(worker)

    @pyqtSlot(list)
    def _on_extract_tags_result(self, new_tags: list):
        self.active_searcher = None
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"Извлечено тегов: {len(new_tags)}")
        
        if not new_tags or getattr(self, "_extract_image", None) != self.search_panel.hybrid_input.image_path:
            self._run_requested_search()
            return
            
        current_tags = set(getattr(self.search_panel, 'selected_tags', []))
        for t in new_tags:
            current_tags.add(t)
            
        self.search_panel.set_selected_tags(list(current_tags))
        # Сразу запускаем поиск по новым тегам
        self.search_panel._emit_search()

    def _open_tag_manager(self):
        selected_tags = getattr(self.search_panel, 'selected_tags', [])
        dialog = TagManagerDialog(self.db, selected_tags, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_tags = dialog.get_selected_tags()
            self.search_panel.set_selected_tags(new_tags)
            self.search_panel._emit_search()

    def _update_breadcrumbs(self, text=None, img_path=None, threshold=None, sources=None, tags=None):
        """Обновляет полосу хлебных крошек в углу вкладок."""
        if text is None:
            text = self.search_panel.hybrid_input.text_input.text().strip()
            img_path = self.search_panel.hybrid_input.image_path
            tags = getattr(self.search_panel, 'selected_tags', [])

        # Clear layout
        while self.breadcrumbs_layout.count():
            item = self.breadcrumbs_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        def add_text_crumb(label, filter_type, val=None):
            container = QWidget()
            layout = QHBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4)
            
            lbl = QLabel(label if len(label) < 90 else label[:87] + "…")
            lbl.setToolTip(label)
            lbl.setStyleSheet("color: #888; font-size: 12px;")
            
            btn_close = QPushButton("✕")
            btn_close.setFixedSize(16, 16)
            btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_close.setStyleSheet("""
                QPushButton { 
                    color: #555; background: transparent; border: none; font-weight: bold; font-size: 10px;
                }
                QPushButton:hover { color: #f44336; }
            """)
            btn_close.clicked.connect(lambda: self._remove_breadcrumb_filter(filter_type, val))
            
            layout.addWidget(lbl)
            layout.addWidget(btn_close)
            self.breadcrumbs_layout.addWidget(container)

        # 1. Text Filter
        if text:
            add_text_crumb(f"\"{text}\"", 'text')

        # 2. Image Filter
        if img_path:
            add_text_crumb("🖼️", 'image')

        # 4. Project Filter
        if getattr(self, 'filter_project_id', None) is not None:
            try:
                with self.db.get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT title FROM projects WHERE id = ?", (self.filter_project_id,))
                    p_row = cur.fetchone()
                    p_title = p_row['title'] if p_row else f"Project #{self.filter_project_id}"
                add_text_crumb(f"📁 {p_title}", 'project')
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Error getting project title for breadcrumbs: {e}")

        # 5. Author Filter
        if getattr(self, 'filter_author', None):
            add_text_crumb(f"👤 {self.filter_author}", 'author')

        # 6. Favorites Filter
        if self.search_panel.favorite_check.isChecked():
            add_text_crumb("★ Избранное", 'favorites')

        # 7. Top Filter
        if self.search_panel.top_check.isChecked():
            add_text_crumb("🏆 ТОП", 'top')

        # Trigger the actual search
        self.breadcrumbs_widget.setVisible(self.breadcrumbs_layout.count() > 0)
        self.filters_button.setText("Фильтры" + (f" · {len(tags or []) + len(self.search_panel.excluded_tags())}" if tags or self.search_panel.excluded_tags() else ""))
        self._perform_visual_search(text, img_path, 0.0,
                                  sources if sources is not None else self.search_panel.get_selected_sources(), tags)

    def _remove_breadcrumb_filter(self, filter_type, value=None):
        """Удаляет фильтр через хлебные крошки."""
        if filter_type == 'text':
            self.search_panel.hybrid_input.text_input.clear()
        elif filter_type == 'image':
            self.search_panel.hybrid_input.clear_image()
        elif filter_type == 'tag':
            tags = self.search_panel.selected_tags
            if value in tags:
                tags.remove(value)
                self.search_panel.set_selected_tags(tags)
        elif filter_type == 'project':
            self.filter_project_id = None
        elif filter_type == 'author':
            self.filter_author = None
        elif filter_type == 'exclude':
            self.search_panel.exclude_input.setText(", ".join(t for t in self.search_panel.excluded_tags() if t != value))
        elif filter_type == 'favorites':
            self.search_panel.favorite_check.setChecked(False)
        elif filter_type == 'top':
            self.search_panel.top_check.setChecked(False)
        
        self._update_breadcrumbs() # Re-emit search

    def _current_filters(self):
        panel = self.search_panel
        return SearchFilters(sources=tuple(self.search_sources), section="all",
                             excluded_sources=panel.get_excluded_sources(),
                             tags=tuple(self.search_tags), exclude_tags=panel.excluded_tags(),
                             tag_match=panel.tag_match.currentData(), favorites=panel.favorite_check.isChecked(),
                             top_only=panel.top_check.isChecked(),
                             plants_only=False,
                             project_id=self.filter_project_id, author=self.filter_author)

    def _perform_visual_search(self, text, img_path, threshold, sources, tags=None):
        # Invalidate an in-flight filtering result even before a new embedding is ready.
        self._results_revision += 1
        self._pending_results_worker = None
        self.current_search_text = text or ""
        self.search_sources = list(sources)
        self.search_tags = list(tags or [])
        self.result_limit = config.SEARCH_PAGE_SIZE
        self.more_button.hide()
        self.result_title.setText("Эталонный ТОП" if self.search_panel.top_check.isChecked() else "Референсы")
        if (not text and not img_path) or self.search_panel.text_mode.currentData() == "metadata":
            self._requested_search = None
            self.progress_bar.hide()
            self.search_panel.query_warning.hide()
            self._search_vectors(None, "метаданных")
            return
        self._requested_search = (embedding_key(text, img_path), text, img_path)
        if self.faiss_mgr.index.ntotal == 0:
            self._requested_search = None
            self.status_label.setText("Индекс пуст. Поиск по названию доступен без индексации.")
            self.gallery_model.setAssets([])
            self._refresh_library()
            self.result_count.setText("Индекс пуст")
            self.more_button.hide()
            return
        self._run_requested_search()

    def _run_requested_search(self):
        if self._requested_search is None:
            return
        key, text, img_path = self._requested_search
        if self._embedding_cache and self._embedding_cache[0] == key:
            _, vector, info = self._embedding_cache
            self._show_query_info(info)
            self._search_vectors(vector, "похожих изображений")
            return
        # One inference at a time; only the latest requested input is retained.
        if self.active_searcher:
            return
        if not self.ai:
            self.status_label.setText("Загрузка модели для поиска…")
            self._background_ai_init()
            return
        worker = SearchWorker(self.ai, text, img_path)
        worker.signals.result.connect(self._on_embedding_result)
        worker.signals.error.connect(self._on_embedding_error)
        self.active_searcher = worker
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()
        self.status_label.setText("Обработка поискового запроса…")
        self._start_ai_worker(worker)

    @pyqtSlot(object, object, object)
    def _on_embedding_result(self, key, vector, info):
        self.active_searcher = None
        self._embedding_cache = (key, vector, info)
        self.progress_bar.hide()
        if self._requested_search and self._requested_search[0] == key:
            self._show_query_info(info)
            self._search_vectors(vector, "похожих изображений")
        else:
            self._run_requested_search()

    @pyqtSlot(object, str)
    def _on_embedding_error(self, key, error):
        self.active_searcher = None
        self.progress_bar.hide()
        if self._requested_search and self._requested_search[0] == key:
            self.status_label.setText("Ошибка поиска: " + error)
            self.result_count.setText("Не удалось выполнить запрос")
            self.gallery_model.setAssets([])
            self._refresh_library()
            self.more_button.hide()
        else:
            self._run_requested_search()

    def _show_query_info(self, info):
        self._query_info = info
        warning = self.search_panel.query_warning
        if info.get("truncated"):
            warning.setText(f"Запрос: {info['token_count']} токенов, предел модели — {info['token_limit']}. "
                            "Для поиска использовано начало. Сократите запрос или перенесите главное вперёд.")
            warning.setToolTip("В модель передано: " + info.get("effective_text", ""))
            warning.show()
        else:
            warning.hide()

    @pyqtSlot(str)
    def _on_search_error(self, error):
        self.active_searcher = None
        self.progress_bar.hide()
        self.status_label.setText("Ошибка анализа: " + error)
        self._run_requested_search()

    def _search_vectors(self, vector, query_info):
        if self._closing:
            return
        self._results_revision += 1
        worker = ResultsWorker(self._results_revision, self.db, self.faiss_mgr, self.visibility,
                               self._current_filters(), self.search_panel.source_assignments,
                               self.current_search_text, vector, self.result_limit,
                               self.search_panel.text_mode.currentData() == 'metadata')
        worker.signals.result.connect(self._on_results_ready)
        worker.signals.error.connect(self._on_results_error)
        self._pending_results_worker = worker
        self.more_button.hide()
        self.status_label.setText('Обновление результатов…')
        self._dispatch_results_worker()

    def _start_results_worker(self, worker):
        self._results_pool.start(worker)

    def _dispatch_results_worker(self):
        if not self._closing and self._active_results_worker is None and self._pending_results_worker is not None:
            worker = self._pending_results_worker
            self._pending_results_worker = None
            self._active_results_worker = worker
            self._start_results_worker(worker)

    @pyqtSlot(int, object, object)
    def _on_results_ready(self, revision, assets, total):
        self._active_results_worker = None
        if revision == self._results_revision:
            self.gallery_model.setAssets(assets)
            self._refresh_library()
            label = f"Показано {len(assets)}" + (f" из {total}" if total is not None else " ближайших изображений")
            if not assets:
                label = "Нет совпадений по выбранным условиям"
            self.result_count.setText(label)
            self.status_label.setText(label)
            self.more_button.setVisible(total > len(assets) if total is not None else len(assets) >= self.result_limit)
        self._dispatch_results_worker()

    @pyqtSlot(int, str)
    def _on_results_error(self, revision, error):
        self._active_results_worker = None
        if revision == self._results_revision:
            logger.error('Search failed: %s', error)
            self.status_label.setText("Ошибка поиска: " + error)
            self.result_count.setText("Не удалось выполнить запрос")
            self.gallery_model.setAssets([])
            self._refresh_library()
            self.more_button.hide()
        self._dispatch_results_worker()

    def _show_more_results(self):
        self.result_limit += config.SEARCH_PAGE_SIZE
        vector = self._embedding_cache[1] if self._requested_search and self._embedding_cache and self._requested_search[0] == self._embedding_cache[0] else None
        self._search_vectors(vector, "запроса")

    # === LM Studio / AI Integration ===

    def start_batch_ai_analysis(self, assets):
        """Starts background AI analysis for a list of assets."""
        if not hasattr(self, '_llm_client'):
            from ai.llm_client import LlmClient
            self._llm_client = LlmClient()

        if not assets:
            return

        class LlmSignals(QObject):
            progress = pyqtSignal(int, int) # current, total
            finished_asset = pyqtSignal(int, str) # asset_id, description
            finished_all = pyqtSignal()
            error = pyqtSignal(str)

        class LlmWorker(QRunnable):
            def __init__(self, client, assets, db):
                super().__init__()
                self.client = client
                self.assets = assets
                self.db = db
                self.signals = LlmSignals()

            def run(self):
                total = len(self.assets)
                for i, asset in enumerate(self.assets):
                    self.signals.progress.emit(i + 1, total)
                    try:
                        # Find the best local path to analyze
                        path = asset.thumbnail_path if asset.thumbnail_path else asset.local_path
                        if path and path.startswith('file:///'):
                            path = path[8:]
                            
                        if not path:
                            logger.warning(f"No path for asset {asset.id}")
                            continue

                        desc = self.client.analyze_image(path)
                        
                        if not desc.startswith("Ошибка"):
                            self.db.set_description(asset.id, desc)
                            self.signals.finished_asset.emit(asset.id, desc)
                        else:
                            self.signals.error.emit(f"Asset {asset.id}: {desc}")

                    except Exception as e:
                        logger.error(f"Error analyzing asset {asset.id}: {e}")
                        self.signals.error.emit(str(e))
                
                self.signals.finished_all.emit()

        self.status_label.setText(f"⏳ Анализ {len(assets)} изображений (LM Studio)...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(assets))
        self.progress_bar.setValue(0)

        worker = LlmWorker(self._llm_client, assets, self.db)
        worker.signals.progress.connect(lambda cur, tot: self.progress_bar.setValue(cur))
        
        def on_finished_asset(asset_id, desc):
            # If the viewer is open, update its description
            if getattr(self.gallery, "_viewer_window", None):
                self.gallery._viewer_window.update_description(asset_id, desc)
                
        worker.signals.finished_asset.connect(on_finished_asset)
        
        def on_finished_all():
            self.progress_bar.setVisible(False)
            self.status_label.setText(f"✅ Анализ завершен ({len(assets)} изобр.)")
            
        worker.signals.finished_all.connect(on_finished_all)
        
        def on_error(err):
            logger.error(f"LLM Worker error: {err}")
            
        worker.signals.error.connect(on_error)

        QThreadPool.globalInstance().start(worker)
