from PyQt6.QtWidgets import QWidget, QHBoxLayout, QComboBox, QPushButton, QLabel, QLineEdit, QSizePolicy
from PyQt6.QtCore import pyqtSignal, Qt
import config
from ui.translations import tr

class TopToolbar(QWidget):
    # Signals
    scrape_started = pyqtSignal(str, str) # parser_name, url
    scrape_stopped = pyqtSignal()
    add_folder_requested = pyqtSignal()
    index_requested = pyqtSignal()
    cleanup_requested = pyqtSignal()
    ignore_deleted_toggled = pyqtSignal(bool)
    subfolders_toggled = pyqtSignal(bool) # True = Recursive
    language_changed = pyqtSignal(str)
    settings_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.is_scraping = False
        self._init_ui()

    def _init_ui(self):
        self.setFixedHeight(50)
        self.setStyleSheet("""
            QWidget { background-color: #1a1a1a; color: #ccc; }
            QPushButton { 
                background-color: #2d2d2d; border: 1px solid #333; border-radius: 4px; padding: 6px 12px; font-weight: bold; color: #aaa; 
            }
            QPushButton:hover { background-color: #383838; border-color: #444; color: #eee; }
            QComboBox, QLineEdit { background-color: #252525; border: 1px solid #333; border-radius: 4px; padding: 5px 10px; color: #fff; }
            QComboBox:focus, QLineEdit:focus { border-color: #555; }
            QCheckBox { spacing: 8px; font-size: 11px; color: #777; }
            QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #333; border-radius: 2px; background-color: #252525; }
            QCheckBox::indicator:checked { background-color: #ffffff; border-color: #ffffff; }
            QCheckBox:hover { color: #eee; }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(10)

        layout.setSpacing(10)
        
        # Settings (Hamburger)
        self.btn_settings = QPushButton("☰")
        self.btn_settings.setFixedWidth(40)
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.setStyleSheet("""
            QPushButton { 
                background-color: transparent; 
                border: none; 
                font-size: 20px; 
                color: #888; 
                padding: 0;
            }
            QPushButton:hover { color: #fff; }
        """)
        self.btn_settings.clicked.connect(self.settings_requested.emit)
        layout.addWidget(self.btn_settings)

        # --- Left Block: Parsers ---
        self.parser_combo = QComboBox()
        self.parser_combo.addItems(["Behance", "ArchDaily"])
        self.parser_combo.setFixedWidth(100)
        
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Enter URL...")
        self.url_input.setFixedWidth(200)

        self.btn_scrape = QPushButton(tr("start"))
        self.btn_scrape.setStyleSheet("""
            QPushButton { background-color: #2d2d2d; color: white; border: 1px solid #444; border-radius: 4px; padding: 6px 15px; }
            QPushButton:hover { background-color: #3d3d3d; border-color: #555; }
            QPushButton:pressed { background-color: #222; }
        """)
        self.btn_scrape.clicked.connect(self._on_scrape_clicked)

        layout.addWidget(self.parser_combo)
        layout.addWidget(self.url_input)
        layout.addWidget(self.btn_scrape)

        # --- Center Block: Monitoring ---
        layout.addStretch(1)
        self.status_label = QLabel(tr("ready"))
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #888; font-size: 13px; font-style: italic;")
        self.status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.status_label)
        layout.addStretch(1)

        # --- Right Block: Typing, Adding, Sync ---
        from PyQt6.QtWidgets import QCheckBox
        
        # Help Icon
        self.btn_help = QLabel("ⓘ")
        self.btn_help.setFixedSize(18, 18)
        self.btn_help.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.btn_help.setToolTip(tr("help_modes"))
        self.btn_help.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_help.setStyleSheet("""
            QLabel {
                color: #666;
                background-color: #252525;
                border: 1px solid #333;
                border-radius: 9px;
                font-size: 11px;
                font-weight: bold;
                margin-right: 2px;
            }
            QLabel:hover {
                color: #fff;
                background-color: #333;
                border-color: #555;
            }
        """)
        layout.addWidget(self.btn_help)

        self.check_ignore_deleted = QCheckBox(tr("ignore_deleted"))
        self.check_ignore_deleted.setChecked(True)
        self.check_ignore_deleted.setToolTip(tr("ignore_deleted"))
        self.check_ignore_deleted.toggled.connect(self.ignore_deleted_toggled.emit)
        
        self.check_subfolders = QCheckBox(tr("subfolders"))
        self.check_subfolders.setChecked(True)
        self.check_subfolders.setToolTip(tr("subfolders"))
        self.check_subfolders.toggled.connect(self.subfolders_toggled.emit)

        self.check_no_textures = QCheckBox(tr("no_textures"))
        self.check_no_textures.setChecked(True)
        self.check_no_textures.setToolTip(tr("no_textures"))

        self.btn_add_folder = QPushButton(tr("add_folder"))
        self.btn_add_folder.clicked.connect(self.add_folder_requested.emit)

        self.btn_index = QPushButton(tr("index"))
        self.btn_index.clicked.connect(self.index_requested.emit)
        
        self.btn_cleanup = QPushButton(tr("cleanup"))
        self.btn_cleanup.clicked.connect(self.cleanup_requested.emit)
        
        layout.addWidget(self.check_ignore_deleted)
        layout.addWidget(self.check_subfolders)
        layout.addWidget(self.check_no_textures)
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
            self.btn_scrape.setText(tr("stop"))
            self.btn_scrape.setStyleSheet("background-color: #442222; color: #ff8888; border: 1px solid #663333;")
            self.url_input.setEnabled(False)
            self.parser_combo.setEnabled(False)
        else:
            self.btn_scrape.setText(tr("start"))
            self.btn_scrape.setStyleSheet("background-color: #2d2d2d; color: white; border: 1px solid #444; border-radius: 4px;")
            self.url_input.setEnabled(True)
            self.parser_combo.setEnabled(True)

    def _toggle_language(self):
        # This is now handled in SettingsDialog, but we keep the logic if needed
        pass

    def retranslate_ui(self):
        self.url_input.setPlaceholderText(tr("url_placeholder"))
        if self.is_scraping:
            self.btn_scrape.setText(tr("stop"))
        else:
            self.btn_scrape.setText(tr("start"))
        
        self.status_label.setText(tr("ready"))
        self.check_ignore_deleted.setText(tr("ignore_deleted"))
        self.check_subfolders.setText(tr("subfolders"))
        self.check_no_textures.setText(tr("no_textures"))
        self.btn_add_folder.setText(tr("add_folder"))
        self.btn_index.setText(tr("index"))
        self.btn_cleanup.setText(tr("cleanup"))
        
        # Tooltips
        self.btn_help.setToolTip(tr("help_modes"))
        self.check_ignore_deleted.setToolTip(tr("ignore_deleted"))
        self.check_subfolders.setToolTip(tr("subfolders"))
        self.check_no_textures.setToolTip(tr("no_textures"))
