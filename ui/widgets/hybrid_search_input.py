import os
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFrame, QSizePolicy
from PyQt6.QtCore import pyqtSignal, Qt, QSize
from PyQt6.QtGui import QPixmap

class DropZoneFrame(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setStyleSheet("""
            QFrame {
                background-color: #1e1e1e;
                border: 2px dashed #444;
                border-radius: 8px;
            }
            QFrame:hover {
                border-color: #29b6f6;
            }
        """)

class HybridSearchInput(QWidget):
    search_requested = pyqtSignal(str, str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image_path = ""
        self._init_ui()

    def _init_ui(self):
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(10)

        # Drop Zone
        self.drop_zone = DropZoneFrame()
        self.drop_zone.setFixedHeight(140)
        
        self.drop_layout = QVBoxLayout(self.drop_zone)
        self.drop_layout.setContentsMargins(4, 4, 4, 4)

        self.lbl_placeholder = QLabel("📥 Перетащите картинку\n(Drag & Drop)")
        self.lbl_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_placeholder.setStyleSheet("color: #888; font-size: 13px; border: none; background: transparent;")

        self.lbl_preview = QLabel()
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview.setStyleSheet("border: none; background: transparent;")
        self.lbl_preview.setVisible(False)
        
        self.drop_layout.addStretch()
        self.drop_layout.addWidget(self.lbl_placeholder)
        self.drop_layout.addWidget(self.lbl_preview)
        self.drop_layout.addStretch()

        # Connect events
        self.drop_zone.dragEnterEvent = self._dragEnterEvent
        self.drop_zone.dragLeaveEvent = self._dragLeaveEvent
        self.drop_zone.dropEvent = self._dropEvent

        self.layout.addWidget(self.drop_zone)

        # Text Input
        self.text_input = QLineEdit()
        self.text_input.setFixedHeight(36)
        self.text_input.setPlaceholderText("Или введите поисковый запрос...")
        self.text_input.setStyleSheet("""
            QLineEdit {
                background-color: #1e1e1e; color: #e0e0e0;
                border: 1px solid #444; border-radius: 6px;
                padding: 5px 10px; font-size: 13px;
            }
            QLineEdit:focus { border-color: #29b6f6; }
        """)
        self.text_input.returnPressed.connect(self._on_enter)
        self.layout.addWidget(self.text_input)

    def _dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].toLocalFile().lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                event.acceptProposedAction()
                self.drop_zone.setStyleSheet("QFrame { background-color: #2a2a2a; border: 2px dashed #29b6f6; border-radius: 8px; }")

    def _dragLeaveEvent(self, event):
        self.drop_zone.setStyleSheet("""
            QFrame { background-color: #1e1e1e; border: 2px dashed #444; border-radius: 8px; }
            QFrame:hover { border-color: #29b6f6; }
        """)

    def _dropEvent(self, event):
        self._dragLeaveEvent(event)
        urls = event.mimeData().urls()
        if not urls:
            return
        file_path = urls[0].toLocalFile()
        if file_path.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
            self.set_image(file_path)

    def set_image(self, path: str):
        self.image_path = path
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                100, 100,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.lbl_preview.setPixmap(scaled)
            self.lbl_placeholder.setVisible(False)
            self.lbl_preview.setVisible(True)
            self.text_input.setPlaceholderText("Уточняющий запрос к картинке...")

    def clear_image(self):
        self.image_path = ""
        self.lbl_preview.clear()
        self.lbl_preview.setVisible(False)
        self.lbl_placeholder.setVisible(True)
        self.text_input.setPlaceholderText("Или введите поисковый запрос...")

    def clear_all(self):
        self.clear_image()
        self.text_input.clear()

    def _on_enter(self):
        self.search_requested.emit(self.text_input.text().strip(), self.image_path)
