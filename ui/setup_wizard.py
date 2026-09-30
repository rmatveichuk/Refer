import os
import sys
import zipfile
import logging
import threading
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton, QProgressBar, 
    QStackedWidget, QWidget, QFileDialog, QMessageBox, QHBoxLayout
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject
import config
from huggingface_hub import snapshot_download

logger = logging.getLogger(__name__)

class WorkerSignals(QObject):
    progress = pyqtSignal(int, int) # current, total
    finished = pyqtSignal(bool, str) # success, message
    status = pyqtSignal(str)

class ModelDownloader(threading.Thread):
    def __init__(self, model_id, cache_dir):
        super().__init__()
        self.model_id = model_id
        self.cache_dir = cache_dir
        self.signals = WorkerSignals()
        self._stop_event = threading.Event()

    def run(self):
        try:
            self.signals.status.emit(f"Загрузка модели {self.model_id}...")
            # Disable symlinks warning and force no symlinks if possible
            os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
            
            # Since local_dir=cache_dir bypasses the complicated symlink cache structure, 
            # we can download directly to the models folder.
            # But the rest of the app might rely on `cache_dir=config.MODELS_DIR` in AutoModel.from_pretrained.
            # Transformers handles cache_dir. We'll use local_dir to avoid symlinks completely.
            
            # snapshot_download doesn't have a great progress callback for the UI
            # but we can at least run it in a thread.
            snapshot_download(
                repo_id=self.model_id,
                local_dir=str(self.cache_dir / self.model_id.replace("/", "--")),
            )
            self.signals.finished.emit(True, "Модель успешно загружена")
        except Exception as e:
            logger.error(f"Download failed: {e}")
            self.signals.finished.emit(False, str(e))

class SetupWizard(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Refer — Первый запуск")
        self.setFixedSize(500, 400)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        
        self.setup_ui()
        self.check_initial_state()

    def setup_ui(self):
        self.layout = QVBoxLayout(self)
        
        self.stack = QStackedWidget()
        
        # Step 1: AI Model Check/Download
        self.step1 = QWidget()
        s1_layout = QVBoxLayout(self.step1)
        s1_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.model_label = QLabel("Проверка компонентов ИИ...")
        self.model_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.model_label.setWordWrap(True)
        self.model_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, 0) # Indeterminate
        
        self.download_btn = QPushButton("Скачать компоненты (~2.5 ГБ)")
        self.download_btn.setFixedHeight(40)
        self.download_btn.setVisible(False)
        self.download_btn.clicked.connect(self.start_download)
        
        s1_layout.addWidget(self.model_label)
        s1_layout.addSpacing(20)
        s1_layout.addWidget(self.progress_bar)
        s1_layout.addWidget(self.download_btn)
        
        # Step 2: Database Setup
        self.step2 = QWidget()
        s2_layout = QVBoxLayout(self.step2)
        s2_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        s2_title = QLabel("Настройка библиотеки")
        s2_title.setStyleSheet("font-size: 16px; font-weight: bold;")
        s2_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        s2_desc = QLabel("Выберите способ создания вашей библиотеки архитектурных референсов.")
        s2_desc.setWordWrap(True)
        s2_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        btn_layout = QVBoxLayout()
        self.create_new_btn = QPushButton("Создать новую (пустую) библиотеку")
        self.create_new_btn.setFixedHeight(50)
        self.create_new_btn.clicked.connect(self.create_empty_db)
        
        self.import_btn = QPushButton("Импортировать готовый архив (.refpack)")
        self.import_btn.setFixedHeight(50)
        self.import_btn.clicked.connect(self.import_refpack)
        
        btn_layout.addWidget(self.create_new_btn)
        btn_layout.addSpacing(10)
        btn_layout.addWidget(self.import_btn)
        
        s2_layout.addWidget(s2_title)
        s2_layout.addSpacing(10)
        s2_layout.addWidget(s2_desc)
        s2_layout.addSpacing(30)
        s2_layout.addLayout(btn_layout)
        
        self.stack.addWidget(self.step1)
        self.stack.addWidget(self.step2)
        
        self.layout.addWidget(self.stack)

    def check_initial_state(self):
        model_exists = False
        local_model_dir = config.MODELS_DIR / config.SIGLIP_MODEL.replace("/", "--")
        hf_cache_dir = config.MODELS_DIR / f"models--{config.SIGLIP_MODEL.replace('/', '--')}"
        
        if local_model_dir.exists() and any(local_model_dir.iterdir()):
            model_exists = True
        elif hf_cache_dir.exists() and any(hf_cache_dir.iterdir()):
            model_exists = True
        
        if model_exists:
            self.go_to_step2()
        else:
            self.model_label.setText("Для работы программы необходимо скачать ИИ-модель SigLIP.\nЭто потребуется только один раз.")
            self.download_btn.setVisible(True)

    def start_download(self):
        self.download_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        
        self.downloader = ModelDownloader(config.SIGLIP_MODEL, config.MODELS_DIR)
        self.downloader.signals.status.connect(self.model_label.setText)
        self.downloader.signals.finished.connect(self.on_download_finished)
        self.downloader.start()

    def on_download_finished(self, success, message):
        self.progress_bar.setVisible(False)
        if success:
            self.go_to_step2()
        else:
            QMessageBox.critical(self, "Ошибка загрузки", f"Не удалось скачать модель: {message}")
            self.download_btn.setEnabled(True)

    def go_to_step2(self):
        self.stack.setCurrentIndex(1)

    def create_empty_db(self):
        # Just close and let DatabaseManager initialize it normally
        self.accept()

    def import_refpack(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Выберите файл библиотеки", "", "Refer Package (*.refpack);;ZIP Archive (*.zip)"
        )
        if not file_path:
            return

        try:
            from utils.library_manager import import_library_replace, relink_paths
            import shutil
            
            temp_dir = config.APP_DATA_DIR / "temp_import" if hasattr(config, "APP_DATA_DIR") else config.APP_ROAMING_DIR / "temp_import"
            db_ext, index_ext = import_library_replace(file_path, temp_dir)
            
            QMessageBox.information(
                self, "Импорт", 
                "Архив распакован. Теперь укажите корневую папку, где хранятся сами изображения, чтобы обновить пути."
            )
            
            img_root = QFileDialog.getExistingDirectory(self, "Выберите папку с изображениями")
            if img_root:
                relink_paths(db_ext, new_local_root=img_root, new_thumbnails_root=str(config.THUMBNAILS_DIR))
                
            # Перемещаем базу и индекс на их законные места
            for p in [config.DB_PATH, config.FAISS_PATH, 
                      Path(str(config.DB_PATH) + "-wal"), 
                      Path(str(config.DB_PATH) + "-shm")]:
                if p.exists():
                    os.remove(p)
                
            shutil.copy2(db_ext, config.DB_PATH)
            shutil.copy2(index_ext, config.FAISS_PATH)
            
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
                
            self.accept()
            
        except Exception as e:
            logger.error(f"Import failed: {e}")
            QMessageBox.critical(self, "Ошибка импорта", f"Не удалось импортировать библиотеку: {e}")
