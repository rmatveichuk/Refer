from PyQt6.QtWidgets import QWidget, QHBoxLayout, QComboBox, QPushButton, QLabel, QLineEdit, QSizePolicy
from PyQt6.QtCore import pyqtSignal, Qt

class TopToolbar(QWidget):
    # Signals
    scrape_started = pyqtSignal(str, str) # parser_name, url
    scrape_stopped = pyqtSignal()
    add_folder_requested = pyqtSignal()
    index_requested = pyqtSignal()
    cleanup_requested = pyqtSignal()
    category_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.is_scraping = False
        self._init_ui()

    def _init_ui(self):
        self.setFixedHeight(50)
        self.setStyleSheet("""
            QWidget { background-color: #1a1a1a; color: #e0e0e0; }
            QPushButton { background-color: #2d2d2d; border: 1px solid #444; border-radius: 6px; padding: 5px 12px; font-weight: bold; }
            QPushButton:hover { background-color: #3d3d3d; border-color: #555; }
            QComboBox, QLineEdit { background-color: #2d2d2d; border: 1px solid #444; border-radius: 6px; padding: 5px 10px; }
            QComboBox:focus, QLineEdit:focus { border-color: #29b6f6; }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(10)

        # --- Left Block: Parsers ---
        self.parser_combo = QComboBox()
        self.parser_combo.addItems(["Behance", "ArchDaily"])
        self.parser_combo.setFixedWidth(100)
        
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Enter URL...")
        self.url_input.setFixedWidth(200)

        self.btn_scrape = QPushButton("▶ Start")
        self.btn_scrape.setStyleSheet("background-color: #29b6f6; color: black; border: none;")
        self.btn_scrape.clicked.connect(self._on_scrape_clicked)

        layout.addWidget(self.parser_combo)
        layout.addWidget(self.url_input)
        layout.addWidget(self.btn_scrape)

        # --- Center Block: Monitoring ---
        layout.addStretch(1)
        self.status_label = QLabel("Ready")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #888; font-size: 13px; font-style: italic;")
        self.status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.status_label)
        layout.addStretch(1)

        # --- Right Block: Typing, Adding, Sync ---
        self.type_combo = QComboBox()
        self.type_combo.addItems(["All", "Textures", "3D Models"])
        self.type_combo.currentIndexChanged.connect(lambda: self.category_changed.emit(self.type_combo.currentText()))

        self.btn_add_folder = QPushButton("+ Add Folder")
        self.btn_add_folder.clicked.connect(self.add_folder_requested.emit)

        self.btn_index = QPushButton("🔍 Index")
        self.btn_index.setStyleSheet("background-color: #7b1fa2; color: white; border: none;")
        self.btn_index.clicked.connect(self.index_requested.emit)
        
        self.btn_cleanup = QPushButton("🧹 Cleanup")
        self.btn_cleanup.setStyleSheet("background-color: #546e7a; color: white; border: none;")
        self.btn_cleanup.clicked.connect(self.cleanup_requested.emit)

        layout.addWidget(self.type_combo)
        layout.addWidget(self.btn_add_folder)
        layout.addWidget(self.btn_index)
        layout.addWidget(self.btn_cleanup)

    def set_status(self, text: str):
        self.status_label.setText(text)

    def _on_scrape_clicked(self):
        if self.is_scraping:
            self.scrape_stopped.emit()
            self._set_btn_state(False)
        else:
            url = self.url_input.text().strip()
            parser = self.parser_combo.currentText()
            self.scrape_started.emit(parser, url)
            # The window will tell us to toggle button state via toggle_scrape_state logic
            
    def set_scraping_state(self, is_scraping: bool):
        self.is_scraping = is_scraping
        self._set_btn_state(self.is_scraping)

    def _set_btn_state(self, is_scraping: bool):
        if is_scraping:
            self.btn_scrape.setText("⏹ Stop")
            self.btn_scrape.setStyleSheet("background-color: #f44336; color: white; border: none;")
            self.url_input.setEnabled(False)
            self.parser_combo.setEnabled(False)
        else:
            self.btn_scrape.setText("▶ Start")
            self.btn_scrape.setStyleSheet("background-color: #29b6f6; color: black; border: none;")
            self.url_input.setEnabled(True)
            self.parser_combo.setEnabled(True)
