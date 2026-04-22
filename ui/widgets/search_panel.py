from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSlider, QLabel, QCheckBox, QFrame, QScrollArea
from PyQt6.QtCore import pyqtSignal, Qt, QTimer

from ui.widgets.hybrid_search_input import HybridSearchInput

class SearchPanel(QWidget):
    search_triggered = pyqtSignal(str, str, float, list) # text, image_path, threshold, sources
    clear_triggered = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # Debounce timer — prevents rapid-fire searches when slider is adjusted quickly
        self._search_debounce = QTimer()
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(500)
        self._search_debounce.timeout.connect(self._emit_search)
        self._init_ui()

    def _init_ui(self):
        self.setFixedWidth(300)
        self.setStyleSheet("""
            QWidget { background-color: #121212; color: #e0e0e0; }
            QLabel { font-weight: bold; margin-top: 10px; margin-bottom: 5px; }
            QCheckBox { margin-left: 5px; padding: 4px; }
            QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #555; border-radius: 3px; background-color: #1e1e1e; }
            QCheckBox::indicator:checked { background-color: #29b6f6; border-color: #29b6f6; }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)

        # Block 1: Search and Clear Actions
        actions_layout = QHBoxLayout()
        self.btn_search = QPushButton("Поиск")
        self.btn_search.setStyleSheet("background-color: #29b6f6; color: black; font-weight: bold; border-radius: 6px; padding: 8px;")
        self.btn_search.clicked.connect(self._emit_search)

        self.btn_clear = QPushButton("Очистить")
        self.btn_clear.setStyleSheet("background-color: #2d2d2d; color: white; border: 1px solid #444; border-radius: 6px; padding: 8px;")
        self.btn_clear.clicked.connect(self._clear_all)

        actions_layout.addWidget(self.btn_search, 1)
        actions_layout.addWidget(self.btn_clear, 1)
        main_layout.addLayout(actions_layout)

        # Block 2: Hybrid Input
        self.hybrid_input = HybridSearchInput()
        self.hybrid_input.search_requested.connect(self._on_hybrid_enter)
        main_layout.addWidget(self.hybrid_input)

        # Line separator
        line1 = QFrame()
        line1.setFrameShape(QFrame.Shape.HLine)
        line1.setStyleSheet("border-top: 1px solid #333;")
        main_layout.addWidget(line1)

        # Block 3: Sensitivity
        sens_header_layout = QHBoxLayout()
        lbl_sens = QLabel("Чувствительность")
        lbl_sens.setStyleSheet("font-weight: bold; margin-top: 10px; margin-bottom: 5px;")
        self.lbl_sens_value = QLabel("60%")
        self.lbl_sens_value.setStyleSheet("""
            font-weight: bold; font-size: 12px; color: #29b6f6;
            background-color: #1e2a30; border: 1px solid #29b6f6;
            border-radius: 4px; padding: 1px 6px;
            margin-top: 10px; margin-bottom: 5px;
        """)
        self.lbl_sens_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sens_header_layout.addWidget(lbl_sens)
        sens_header_layout.addStretch()
        sens_header_layout.addWidget(self.lbl_sens_value)
        main_layout.addLayout(sens_header_layout)

        self.slider_sens = QSlider(Qt.Orientation.Horizontal)
        self.slider_sens.setRange(0, 100)
        self.slider_sens.setValue(60) # Default
        self.slider_sens.setStyleSheet("""
            QSlider::groove:horizontal { border: 1px solid #444; height: 4px; background: #1e1e1e; border-radius: 2px; }
            QSlider::handle:horizontal { background: #29b6f6; width: 14px; margin: -5px 0; border-radius: 7px; }
        """)
        # Update label live while dragging, debounced search on release
        self.slider_sens.valueChanged.connect(self._on_slider_value_changed)
        self.slider_sens.sliderReleased.connect(self._on_slider_released)
        
        lbl_wide = QLabel("Широкий")
        lbl_wide.setStyleSheet("font-size: 10px; font-weight: normal; color: #888;")
        lbl_exact = QLabel("Точный")
        lbl_exact.setStyleSheet("font-size: 10px; font-weight: normal; color: #888;")

        sens_labels_layout = QHBoxLayout()
        sens_labels_layout.addWidget(lbl_wide)
        sens_labels_layout.addStretch()
        sens_labels_layout.addWidget(lbl_exact)
        
        main_layout.addWidget(self.slider_sens)
        main_layout.addLayout(sens_labels_layout)

        # Line separator
        line2 = QFrame()
        line2.setFrameShape(QFrame.Shape.HLine)
        line2.setStyleSheet("border-top: 1px solid #333;")
        main_layout.addWidget(line2)

        # Block 4: Sources
        lbl_sources = QLabel("Источники")
        main_layout.addWidget(lbl_sources)

        self.chk_archdaily = QCheckBox("ArchDaily")
        self.chk_archdaily.setChecked(True)
        self.chk_behance = QCheckBox("Behance")
        
        main_layout.addWidget(self.chk_archdaily)
        main_layout.addWidget(self.chk_behance)

        # Dynamic Custom Folders
        self.folders_layout = QVBoxLayout()
        self.folders_layout.setContentsMargins(0, 0, 0, 0)
        self.folders_layout.setSpacing(5)
        self.folder_checkboxes = {} # {folder_path: QCheckBox}
        
        main_layout.addLayout(self.folders_layout)

        main_layout.addStretch(1)

    def update_custom_folders(self, folders: list):
        # Remove old checkboxes
        for i in reversed(range(self.folders_layout.count())): 
            widget = self.folders_layout.itemAt(i).widget()
            if widget is not None:
                widget.deleteLater()
        
        self.folder_checkboxes.clear()
        
        # Add new checkboxes
        import os
        for folder in folders:
            # Показываем только имя папки для красоты, но храним полный путь
            folder_name = os.path.basename(folder.rstrip('\\/'))
            if not folder_name:
                folder_name = folder # fallback (e.g. "C:\")
                
            chk = QCheckBox(f"📁 {folder_name}")
            chk.setChecked(True)
            chk.setToolTip(folder)
            self.folders_layout.addWidget(chk)
            self.folder_checkboxes[folder] = chk

    def _emit_search(self):
        text = self.hybrid_input.text_input.text().strip()
        img_path = self.hybrid_input.image_path
        threshold = self.slider_sens.value() / 100.0
        
        sources = []
        if self.chk_archdaily.isChecked(): sources.append('archdaily')
        if self.chk_behance.isChecked(): sources.append('behance')
        
        for folder_path, chk in self.folder_checkboxes.items():
            if chk.isChecked():
                sources.append(folder_path)

        self.search_triggered.emit(text, img_path, threshold, sources)

    def _on_hybrid_enter(self, text, img_path):
        self._emit_search()

    def _on_slider_value_changed(self, value: int):
        self.lbl_sens_value.setText(f"{value}%")

    def _on_slider_released(self):
        """Restart debounce timer — search fires 500ms after last release."""
        self._search_debounce.start()

    def _clear_all(self):
        self.hybrid_input.clear_all()
        self.slider_sens.setValue(60)
        self.chk_archdaily.setChecked(True)
        self.chk_behance.setChecked(False)
        for chk in self.folder_checkboxes.values():
            chk.setChecked(True)
        self.clear_triggered.emit()
