import sys

with open('ui/widgets/search_panel.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Imports
imports_addition = """
from ui.widgets.flow_layout import FlowLayout

class TagBubble(QFrame):
    removed = pyqtSignal(str)

    def __init__(self, tag_name, parent=None):
        super().__init__(parent)
        self.tag_name = tag_name
        self.setStyleSheet(\"\"\"
            QFrame {
                background-color: #1e2a30; border: 1px solid #29b6f6;
                border-radius: 10px; padding: 2px 6px;
            }
            QLabel { color: #29b6f6; font-size: 11px; font-weight: bold; border: none; margin: 0; padding: 0; }
            QPushButton {
                background-color: transparent; color: #888; border: none; font-size: 10px; font-weight: bold;
                margin: 0; padding: 0 4px;
            }
            QPushButton:hover { color: #f44336; }
        \"\"\")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)
        
        lbl = QLabel(tag_name)
        btn_close = QPushButton("✕")
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.clicked.connect(lambda: self.removed.emit(self.tag_name))
        
        layout.addWidget(lbl)
        layout.addWidget(btn_close)
"""
if "class TagBubble" not in content:
    content = content.replace("from ui.widgets.hybrid_search_input import HybridSearchInput", imports_addition + "\nfrom ui.widgets.hybrid_search_input import HybridSearchInput")

# 2. Add signal to SearchPanel
if "manage_tags_requested = pyqtSignal()" not in content:
    content = content.replace("remove_source_requested = pyqtSignal(str) # path or domain", "remove_source_requested = pyqtSignal(str) # path or domain\n    manage_tags_requested = pyqtSignal()")

# 3. Modify signature of search_triggered
if "search_triggered = pyqtSignal(str, str, float, list)" in content:
    content = content.replace("search_triggered = pyqtSignal(str, str, float, list) # text, image_path, threshold, sources", "search_triggered = pyqtSignal(str, str, float, list, list) # text, image_path, threshold, sources, tags")

# 4. Add UI elements for Tags
ui_addition = """
        # Block 2.5: Tags
        tags_header_layout = QHBoxLayout()
        self.btn_manage_tags = QPushButton("🏷️ Теги (0)")
        self.btn_manage_tags.setStyleSheet("background-color: #2d2d2d; color: #29b6f6; border: 1px solid #444; border-radius: 4px; padding: 4px 8px; font-size: 11px;")
        self.btn_manage_tags.clicked.connect(self.manage_tags_requested.emit)
        tags_header_layout.addWidget(self.btn_manage_tags)
        tags_header_layout.addStretch()
        main_layout.addLayout(tags_header_layout)

        self.tags_flow_layout = FlowLayout()
        self.selected_tags = []
        main_layout.addLayout(self.tags_flow_layout)

        # Line separator 1
"""
if "Block 2.5: Tags" not in content:
    content = content.replace("""        # Line separator
        line1 = QFrame()""", ui_addition + """        line1 = QFrame()""")

# 5. Add methods for managing tags
methods_addition = """
    def set_selected_tags(self, tags: list):
        self.selected_tags = tags
        self.btn_manage_tags.setText(f"🏷️ Теги ({len(tags)})")
        
        # Clear layout
        while self.tags_flow_layout.count():
            item = self.tags_flow_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
                
        # Add bubbles
        for tag in tags:
            bubble = TagBubble(tag)
            bubble.removed.connect(self._on_tag_removed)
            self.tags_flow_layout.addWidget(bubble)
            
    def _on_tag_removed(self, tag: str):
        if tag in self.selected_tags:
            self.selected_tags.remove(tag)
            self.set_selected_tags(self.selected_tags)
            self._emit_search()

"""
if "def set_selected_tags" not in content:
    content = content.replace("    def _emit_search(self):", methods_addition + "    def _emit_search(self):")

# 6. Update _emit_search to emit tags
if "sources = self.get_selected_sources()" in content:
    content = content.replace("sources = self.get_selected_sources()\n        self.search_triggered.emit(text, img, thresh, sources)", "sources = self.get_selected_sources()\n        tags = getattr(self, 'selected_tags', [])\n        self.search_triggered.emit(text, img, thresh, sources, tags)")

with open('ui/widgets/search_panel.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched search_panel.py")