import sys

with open('ui/widgets/hybrid_search_input.py', 'r', encoding='utf-8') as f:
    content = f.read()

btn_code = """
        self.btn_analyze = QPushButton("✨ Авто-теги")
        self.btn_analyze.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_analyze.setStyleSheet(\"\"\"
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
        \"\"\")
        self.btn_analyze.setVisible(False)
        self.btn_analyze.clicked.connect(self._on_analyze_clicked)
"""

if "self.btn_analyze = QPushButton" not in content:
    # Insert it right before the preview_layout is added
    content = content.replace("        self.drop_layout.addStretch()\n        self.drop_layout.addWidget(self.lbl_placeholder)", btn_code + "\n        self.drop_layout.addStretch()\n        self.drop_layout.addWidget(self.lbl_placeholder)")

with open('ui/widgets/hybrid_search_input.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Restored btn_analyze in hybrid_search_input.py")