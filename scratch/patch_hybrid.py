import sys

with open('ui/widgets/hybrid_search_input.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add signal
if "analyze_requested = pyqtSignal(str)" not in content:
    content = content.replace("search_requested = pyqtSignal(str, str)", "search_requested = pyqtSignal(str, str)\n    analyze_requested = pyqtSignal(str)")

# 2. Add button UI
ui_btn_code = """
        # Preview layout to hold both image and button
        self.preview_layout = QVBoxLayout()
        self.preview_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_layout.setContentsMargins(0,0,0,0)

        self.lbl_preview = QLabel()
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview.setStyleSheet("border: none; background: transparent;")
        self.lbl_preview.setVisible(False)
        
        self.btn_analyze = QPushButton("✨ Авто-теги")
        self.btn_analyze.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_analyze.setStyleSheet(\"\"\"
            QPushButton {
                background-color: #29b6f6; color: black; border: none;
                border-radius: 4px; padding: 4px 8px; font-weight: bold; font-size: 11px;
                margin-top: 5px;
            }
            QPushButton:hover { background-color: #4fc3f7; }
        \"\"\")
        self.btn_analyze.setVisible(False)
        self.btn_analyze.clicked.connect(self._on_analyze_clicked)
        
        self.preview_layout.addWidget(self.lbl_preview)
        self.preview_layout.addWidget(self.btn_analyze, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.drop_layout.addStretch()
        self.drop_layout.addWidget(self.lbl_placeholder)
        self.drop_layout.addLayout(self.preview_layout)
        self.drop_layout.addStretch()
"""

# Find where to replace
start_idx = content.find("        self.lbl_preview = QLabel()")
end_idx = content.find("        self.drop_layout.addStretch()\n\n        # Connect events")

if start_idx != -1 and end_idx != -1:
    content = content[:start_idx] + ui_btn_code.strip() + "\n\n" + content[end_idx + len("        self.drop_layout.addStretch()"):].lstrip()


# 3. Add _on_analyze_clicked
method_code = """
    def _on_analyze_clicked(self):
        if self.image_path:
            self.analyze_requested.emit(self.image_path)
"""
if "def _on_analyze_clicked" not in content:
    content = content.replace("    def set_image(self, path: str):", method_code + "\n    def set_image(self, path: str):")

# 4. Show/Hide button in set_image and clear_image
if "self.btn_analyze.setVisible(True)" not in content:
    content = content.replace("self.lbl_preview.setVisible(True)", "self.lbl_preview.setVisible(True)\n            self.btn_analyze.setVisible(True)")
if "self.btn_analyze.setVisible(False)" not in content:
    content = content.replace("self.lbl_preview.setVisible(False)", "self.lbl_preview.setVisible(False)\n        self.btn_analyze.setVisible(False)")


with open('ui/widgets/hybrid_search_input.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched hybrid_search_input.py")