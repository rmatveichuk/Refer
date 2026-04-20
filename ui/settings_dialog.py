"""Settings dialog for the Refer app."""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, 
    QPushButton, QLabel, QGroupBox
)
from PyQt6.QtCore import Qt
import config

class SettingsDialog(QDialog):
    """Simple settings dialog."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(400)
        self.setModal(True)
        
        self._init_ui()
    
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        
        # === AI Engine Info ===
        ai_group = QGroupBox("AI Engine")
        ai_layout = QVBoxLayout()
        ai_info = QLabel(
            f"<b>Model:</b> SigLIP-so400m<br>"
            f"<b>Vector Dimension:</b> {config.VECTOR_DIMENSION}<br>"
            f"<b>Search:</b> Support text AND image query<br>"
            f"<b>Status:</b> Local (Offline)"
        )
        ai_info.setStyleSheet("color: #aaa; font-size: 13px; padding: 10px;")
        ai_layout.addWidget(ai_info)
        ai_group.setLayout(ai_layout)
        layout.addWidget(ai_group)
        
        # === Storage Info ===
        storage_group = QGroupBox("Storage")
        storage_layout = QVBoxLayout()
        storage_info = QLabel(
            f"<b>Database:</b> {config.DB_PATH.name}<br>"
            f"<b>Thumbnails:</b> {config.APP_DATA_DIR / 'thumbnails'}"
        )
        storage_info.setStyleSheet("color: #aaa; font-size: 11px; padding: 10px;")
        storage_layout.addWidget(storage_info)
        storage_group.setLayout(storage_layout)
        layout.addWidget(storage_group)

        # === Buttons ===
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #555; color: #e0e0e0;
                border-radius: 4px; padding: 8px 20px;
                font-size: 13px;
            }
            QPushButton:hover { background-color: #666; }
        """)
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)
        
        layout.addLayout(button_layout)
