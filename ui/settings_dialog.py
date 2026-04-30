"""Settings dialog for the Refer app."""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, 
    QPushButton, QLabel, QGroupBox, QLineEdit, QTextEdit, QMessageBox, QStyle
)
import config
from ui.translations import tr
from PyQt6.QtCore import Qt, pyqtSignal, QSettings
from ai.llm_client import LlmClient

class SettingsDialog(QDialog):
    """Simple settings dialog."""
    language_changed = pyqtSignal(str)
    export_requested = pyqtSignal()
    import_requested = pyqtSignal()
    backup_requested = pyqtSignal()
    relink_requested = pyqtSignal(str) # new_root
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("settings"))
        self.setMinimumWidth(400)
        self.setModal(True)
        
        self.settings = QSettings("ReferApp", "ReferSettings")
        self.llm_client = LlmClient()
        self._init_ui()
    
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        
        # === AI Engine Info ===
        self.ai_group = QGroupBox(tr("ai_engine"))
        ai_layout = QVBoxLayout()
        self.ai_info = QLabel()
        self._update_ai_info()
        self.ai_info.setStyleSheet("color: #aaa; font-size: 13px; padding: 10px;")
        ai_layout.addWidget(self.ai_info)
        self.ai_group.setLayout(ai_layout)
        layout.addWidget(self.ai_group)
        
        # === Storage Info ===
        self.storage_group = QGroupBox(tr("storage"))
        storage_layout = QVBoxLayout()
        self.storage_info = QLabel()
        self._update_storage_info()
        self.storage_info.setStyleSheet("color: #aaa; font-size: 11px; padding: 10px;")
        storage_layout.addWidget(self.storage_info)
        self.storage_group.setLayout(storage_layout)
        layout.addWidget(self.storage_group)
        
        # === Location (Storage management) ===
        self.loc_group = QGroupBox(tr("location"))
        loc_layout = QVBoxLayout()
        
        # DB Path row
        db_row = QHBoxLayout()
        db_row.addWidget(QLabel(f"{tr('db')}:"))
        self.db_path_input = QLineEdit()
        self.db_path_input.setText(str(config.DB_PATH))
        self.db_path_input.setReadOnly(True)
        self.db_path_input.setStyleSheet("background-color: #222; color: #888;")
        db_row.addWidget(self.db_path_input)
        
        self.btn_backup = QPushButton()
        self.btn_backup.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DriveHDIcon))
        self.btn_backup.setToolTip(tr("backup_db"))
        self.btn_backup.setFixedWidth(40)
        self.btn_backup.clicked.connect(self.backup_requested.emit)
        db_row.addWidget(self.btn_backup)
        loc_layout.addLayout(db_row)
        
        # Images Path row
        img_row = QHBoxLayout()
        img_row.addWidget(QLabel(f"{tr('thumbnails')}:"))
        self.img_path_input = QLineEdit()
        self.img_path_input.setText(str(config.THUMBNAILS_DIR))
        self.img_path_input.setReadOnly(True)
        self.img_path_input.setStyleSheet("background-color: #222; color: #888;")
        img_row.addWidget(self.img_path_input)
        
        self.btn_change_img = QPushButton()
        self.btn_change_img.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.btn_change_img.setToolTip(tr("change_path"))
        self.btn_change_img.setFixedWidth(40)
        self.btn_change_img.clicked.connect(self._on_change_img_path)
        img_row.addWidget(self.btn_change_img)
        loc_layout.addLayout(img_row)
        
        # Export/Import buttons
        lib_btns = QHBoxLayout()
        self.btn_export = QPushButton(tr("export_library"))
        self.btn_export.setStyleSheet("background-color: #2e3b4e; color: #fff; padding: 10px;")
        self.btn_export.clicked.connect(self.export_requested.emit)
        lib_btns.addWidget(self.btn_export)
        
        self.btn_import = QPushButton(tr("import_library"))
        self.btn_import.setStyleSheet("background-color: #3b4e2e; color: #fff; padding: 10px;")
        self.btn_import.clicked.connect(self.import_requested.emit)
        lib_btns.addWidget(self.btn_import)
        
        loc_layout.addLayout(lib_btns)
        self.loc_group.setLayout(loc_layout)
        layout.addWidget(self.loc_group)
        
        # === LM Studio (LLM/VLM) ===
        self.lm_group = QGroupBox("LM Studio API")
        lm_layout = QVBoxLayout()
        
        # URL
        url_layout = QHBoxLayout()
        url_layout.addWidget(QLabel("API URL:"))
        self.url_input = QLineEdit()
        self.url_input.setText(self.settings.value("lm_studio_url", self.llm_client.default_url))
        url_layout.addWidget(self.url_input)
        lm_layout.addLayout(url_layout)
        
        # Model
        model_layout = QHBoxLayout()
        model_layout.addWidget(QLabel("Model:"))
        self.model_input = QLineEdit()
        self.model_input.setText(self.settings.value("lm_studio_model", self.llm_client.default_model))
        model_layout.addWidget(self.model_input)
        lm_layout.addLayout(model_layout)
        
        # Prompt
        lm_layout.addWidget(QLabel(tr("prompt") if "prompt" in config.CURRENT_LANGUAGE else "System Prompt:"))
        self.prompt_input = QTextEdit()
        self.prompt_input.setMaximumHeight(60)
        self.prompt_input.setText(self.settings.value("lm_studio_prompt", self.llm_client.default_prompt))
        lm_layout.addWidget(self.prompt_input)
        
        # Test Button
        self.btn_test_lm = QPushButton("Проверить подключение" if config.CURRENT_LANGUAGE == "ru" else "Test Connection")
        self.btn_test_lm.clicked.connect(self._test_lm_connection)
        lm_layout.addWidget(self.btn_test_lm)
        
        self.lm_group.setLayout(lm_layout)
        layout.addWidget(self.lm_group)
        
        # === Localization ===
        self.lang_group = QGroupBox("Локализация" if config.CURRENT_LANGUAGE == "ru" else "Localization")
        lang_layout = QHBoxLayout()
        
        self.btn_lang = QPushButton("RU | EN")
        self.btn_lang.setFixedWidth(80)
        self.btn_lang.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_lang.setStyleSheet("""
            QPushButton { 
                background-color: #1e2a30; color: #29b6f6; 
                border: 1px solid #29b6f6; border-radius: 4px; 
                padding: 6px; font-weight: bold; 
            }
            QPushButton:hover { background-color: #29b6f6; color: black; }
        """)
        self.btn_lang.clicked.connect(self._toggle_language)
        lang_layout.addWidget(self.btn_lang)
        lang_layout.addStretch()
        
        self.lang_group.setLayout(lang_layout)
        layout.addWidget(self.lang_group)

        # === Buttons ===
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.close_btn = QPushButton(tr("close"))
        self.close_btn.setStyleSheet("""
            QPushButton {
                background-color: #555; color: #e0e0e0;
                border-radius: 4px; padding: 8px 20px;
                font-size: 13px;
            }
            QPushButton:hover { background-color: #666; }
        """)
        self.close_btn.clicked.connect(self._on_close)
        button_layout.addWidget(self.close_btn)
        
        layout.addLayout(button_layout)

    def _update_ai_info(self):
        self.ai_info.setText(
            f"<b>{tr('model')}:</b> SigLIP-so400m<br>"
            f"<b>{tr('dimension')}:</b> {config.VECTOR_DIMENSION}<br>"
            f"<b>{tr('search_type')}:</b> {tr('support_hybrid')}<br>"
            f"<b>{tr('status')}:</b> {tr('offline')}"
        )

    def _update_storage_info(self):
        self.storage_info.setText(
            f"<b>{tr('db')}:</b> {config.DB_PATH.name}<br>"
            f"<b>{tr('thumbnails')}:</b> {config.THUMBNAILS_DIR}"
        )

    def retranslate_ui(self):
        self.setWindowTitle(tr("settings"))
        self.ai_group.setTitle(tr("ai_engine"))
        self.storage_group.setTitle(tr("storage"))
        self.loc_group.setTitle(tr("location"))
        self.btn_export.setText(tr("export_library"))
        self.btn_import.setText(tr("import_library"))
        self.btn_backup.setToolTip(tr("backup_db"))
        self.btn_change_img.setToolTip(tr("change_path"))
        self.lang_group.setTitle("Локализация" if config.CURRENT_LANGUAGE == "ru" else "Localization")
        self.btn_test_lm.setText("Проверить подключение" if config.CURRENT_LANGUAGE == "ru" else "Test Connection")
        self.close_btn.setText(tr("close"))
        self._update_ai_info()
        self._update_storage_info()

    def _toggle_language(self):
        new_lang = "en" if config.CURRENT_LANGUAGE == "ru" else "ru"
        config.CURRENT_LANGUAGE = new_lang
        self.language_changed.emit(new_lang)

    def _test_lm_connection(self):
        # Save temp settings to client
        self.settings.setValue("lm_studio_url", self.url_input.text().strip())
        success, msg = self.llm_client.test_connection()
        
        if success:
            QMessageBox.information(self, "LM Studio", msg)
        else:
            QMessageBox.warning(self, "LM Studio", msg)

    def _on_change_img_path(self):
        from PyQt6.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(self, tr("select_image_folder"), str(config.THUMBNAILS_DIR))
        if path:
            self.img_path_input.setText(path)
            self.relink_requested.emit(path)
            
    def _on_close(self):
        # Save settings
        self.settings.setValue("lm_studio_url", self.url_input.text().strip())
        self.settings.setValue("lm_studio_model", self.model_input.text().strip())
        self.settings.setValue("lm_studio_prompt", self.prompt_input.toPlainText().strip())
        self.accept()
