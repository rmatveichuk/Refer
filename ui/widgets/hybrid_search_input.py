import os
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFrame, QSizePolicy
from PyQt6.QtCore import pyqtSignal, Qt, QSize
from PyQt6.QtGui import QPixmap, QGuiApplication, QKeySequence, QImage
from PyQt6.QtCore import QEvent
import tempfile
import config
from ui.translations import tr

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
                border-color: #fff;
            }
        """)

class HybridSearchInput(QWidget):
    search_requested = pyqtSignal(str, str)
    analyze_requested = pyqtSignal(str)
    
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
        self.drop_zone.setFixedHeight(148)
        
        self.drop_layout = QVBoxLayout(self.drop_zone)
        self.drop_layout.setContentsMargins(4, 4, 4, 4)

        self.lbl_placeholder = QLabel(tr("drop_zone"))
        self.lbl_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_placeholder.setStyleSheet("color: #888; font-size: 13px; border: none; background: transparent;")

        # Preview layout to hold both image and button
        self.preview_layout = QVBoxLayout()
        self.preview_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_layout.setSpacing(6)

        # Container for image and close button
        self.img_container = QWidget(self.drop_zone)
        self.img_container.setFixedSize(100, 100)
        self.img_container.setVisible(False)
        
        self.lbl_preview = QLabel(self.img_container)
        self.lbl_preview.setGeometry(0, 0, 100, 100)
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview.setStyleSheet("border: none; background: transparent;")
        
        self.btn_remove_img = QPushButton("✕", self.img_container)
        self.btn_remove_img.setGeometry(80, 0, 20, 20)
        self.btn_remove_img.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_remove_img.setStyleSheet("""
            QPushButton {
                background-color: rgba(0,0,0,0.6); color: white; border: none;
                border-radius: 10px; font-weight: bold; font-size: 10px;
                padding-bottom: 2px;
            }
            QPushButton:hover { background-color: #f44336; }
        """)
        self.btn_remove_img.clicked.connect(self.clear_image)
        
        self.preview_layout.addWidget(self.img_container)

        self.btn_analyze = QPushButton(tr("auto_tags"), self.drop_zone)
        self.btn_analyze.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_analyze.setStyleSheet("""
            QPushButton {
                background-color: #2d2d2d; color: #888; 
                border: 1px solid #444; border-radius: 4px; 
                padding: 4px 8px; font-weight: bold; font-size: 11px;
                margin: 0;
            }
            QPushButton:hover { 
                background-color: #3d3d3d; 
                color: #e0e0e0;
                border-color: #666;
            }
        """)
        self.btn_analyze.setVisible(False)
        self.btn_analyze.clicked.connect(self._on_analyze_clicked)
        self.preview_layout.addWidget(self.btn_analyze)

        self.drop_layout.addStretch()
        self.drop_layout.addWidget(self.lbl_placeholder)
        self.drop_layout.addLayout(self.preview_layout)
        self.drop_layout.addStretch()

        # Connect events
        self.drop_zone.dragEnterEvent = self._dragEnterEvent
        self.drop_zone.dragLeaveEvent = self._dragLeaveEvent
        self.drop_zone.dropEvent = self._dropEvent

        self.layout.addWidget(self.drop_zone)

        # Text Input
        self.text_input = QLineEdit()
        self.text_input.setFixedHeight(36)
        self.text_input.setPlaceholderText(tr("search_placeholder"))
        self.text_input.setStyleSheet("""
            QLineEdit {
                background-color: #1a1a1a; color: #fff;
                border: 1px solid #333; border-radius: 4px;
                padding: 5px 12px; font-size: 13px;
            }
            QLineEdit:focus { border-color: #fff; }
        """)
        self.text_input.returnPressed.connect(self._on_enter)
        self.layout.addWidget(self.text_input)

        # Enable Paste support
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.drop_zone.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.text_input.installEventFilter(self)

    def _dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].toLocalFile().lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                event.acceptProposedAction()
                self.drop_zone.setStyleSheet("QFrame { background-color: #222; border: 2px dashed #fff; border-radius: 8px; }")

    def _dragLeaveEvent(self, event):
        self.drop_zone.setStyleSheet("""
            QFrame {
                background-color: #1e1e1e;
                border: 2px dashed #444;
                border-radius: 8px;
            }
            QFrame:hover {
                border-color: #fff;
            }
        """)

    def _dropEvent(self, event):
        self._dragLeaveEvent(event)
        urls = event.mimeData().urls()
        if not urls:
            return
        file_path = urls[0].toLocalFile()
        if file_path.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
            event.acceptProposedAction()
            self.set_image(file_path)

    def _on_analyze_clicked(self):
        if self.image_path:
            self.analyze_requested.emit(self.image_path)

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
            self.img_container.setVisible(True)
            self.btn_analyze.setVisible(True)
            self.text_input.setPlaceholderText(tr("refine_placeholder"))

    def clear_image(self):
        self.image_path = ""
        self.lbl_preview.clear()
        self.img_container.setVisible(False)
        self.btn_analyze.setVisible(False)
        self.lbl_placeholder.setVisible(True)
        self.text_input.setPlaceholderText(tr("search_placeholder"))

    def clear_all(self):
        self.clear_image()
        self.text_input.clear()

    def _on_enter(self):
        self.search_requested.emit(self.text_input.text().strip(), self.image_path)


    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress:
            if event.matches(QKeySequence.StandardKey.Paste):
                if self._handle_paste():
                    return True # Intercept paste if it was an image
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Paste):
            if self._handle_paste():
                return
        super().keyPressEvent(event)

    def _handle_paste(self):
        clipboard = QGuiApplication.clipboard()
        mime = clipboard.mimeData()
        
        if mime.hasImage():
            image = clipboard.image()
            if not image.isNull():
                temp_path = os.path.join(tempfile.gettempdir(), "refer_pasted_image.jpg")
                image.save(temp_path, "JPG")
                self.set_image(temp_path)
                return True
        elif mime.hasUrls():
            urls = mime.urls()
            if urls and urls[0].toLocalFile().lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                self.set_image(urls[0].toLocalFile())
                return True
        return False

    def retranslate_ui(self):
        self.lbl_placeholder.setText(tr("drop_zone"))
        self.btn_analyze.setText(tr("auto_tags"))
        if self.image_path:
            self.text_input.setPlaceholderText(tr("refine_placeholder"))
        else:
            self.text_input.setPlaceholderText(tr("search_placeholder"))
