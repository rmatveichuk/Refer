"""Settings dialog for the Refer app."""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, 
    QPushButton, QLabel, QGroupBox
)
import config
from ui.translations import tr
from PyQt6.QtCore import Qt, pyqtSignal

class SettingsDialog(QDialog):
    """Simple settings dialog."""
    language_changed = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("settings"))
        self.setMinimumWidth(400)
        self.setModal(True)
        
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
        self.close_btn.clicked.connect(self.accept)
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
            f"<b>{tr('thumbnails')}:</b> {config.APP_DATA_DIR / 'thumbnails'}"
        )

    def retranslate_ui(self):
        self.setWindowTitle(tr("settings"))
        self.ai_group.setTitle(tr("ai_engine"))
        self.storage_group.setTitle(tr("storage"))
        self.lang_group.setTitle("Локализация" if config.CURRENT_LANGUAGE == "ru" else "Localization")
        self.close_btn.setText(tr("close"))
        self._update_ai_info()
        self._update_storage_info()

    def _toggle_language(self):
        new_lang = "en" if config.CURRENT_LANGUAGE == "ru" else "ru"
        config.CURRENT_LANGUAGE = new_lang
        self.language_changed.emit(new_lang)
        self.retranslate_ui()
