import re

with open("ui/main_window.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Изменить CornerWidget, чтобы кнопки сдвинулись влево (поменять addStretch), 
# убрать эмодзи и сделать стиль как у вкладки "Таблица".
# Стиль вкладки (неактивной): background: #1a1a1a; color: #888; padding: 8px 16px; border: none;

corner_widget_old = """        # --- Add Corner Buttons ---
        corner = QWidget()
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(5, 0, 0, 0)
        corner_layout.setSpacing(5)
        
        btn_select_all = QPushButton("✅ Выделить все")
        btn_select_all.setStyleSheet("QPushButton { background-color: #333; color: white; border: 1px solid #555; border-radius: 4px; padding: 4px 10px; font-weight: bold; } QPushButton:hover { background-color: #444; }")
        btn_select_all.clicked.connect(self._select_all_gallery)
        
        btn_delete_selected = QPushButton("🗑️ Удалить")
        btn_delete_selected.setStyleSheet("QPushButton { background-color: #f44336; color: white; border: none; border-radius: 4px; padding: 4px 10px; font-weight: bold; } QPushButton:hover { background-color: #d32f2f; }")
        btn_delete_selected.clicked.connect(self._delete_selected_gallery)
        
        corner_layout.addWidget(btn_select_all)
        corner_layout.addWidget(btn_delete_selected)
        corner_layout.addStretch() # Push left"""

corner_widget_new = """        # --- Add Corner Buttons ---
        corner = QWidget()
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(5, 0, 0, 0)
        corner_layout.setSpacing(0)
        
        btn_style = "QPushButton { background: #1a1a1a; color: #888; padding: 8px 16px; border: none; } QPushButton:hover { background: #2d2d2d; color: #ccc; }"
        
        btn_select_all = QPushButton("Выделить все")
        btn_select_all.setStyleSheet(btn_style)
        btn_select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_select_all.clicked.connect(self._select_all_gallery)
        
        btn_delete_selected = QPushButton("Удалить")
        btn_delete_selected.setStyleSheet(btn_style)
        btn_delete_selected.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_delete_selected.clicked.connect(self._delete_selected_gallery)
        
        corner_layout.addWidget(btn_select_all)
        corner_layout.addWidget(btn_delete_selected)
        corner_layout.addStretch() # Push left"""

if corner_widget_old in content:
    content = content.replace(corner_widget_old, corner_widget_new)
    print("Replaced CornerWidget successfully.")
else:
    print("Could not find CornerWidget string.")

# 2. Убрать эмодзи из вкладок Галерея и Таблица

content = content.replace('self.tabs.addTab(gallery_tab, "🎨 Галерея")', 'self.tabs.addTab(gallery_tab, "Галерея")')
content = content.replace('self.tabs.addTab(library_tab, "📋 Таблица")', 'self.tabs.addTab(library_tab, "Таблица")')
content = content.replace('self.tabs.addTab(gallery_tab, "?? Галерея")', 'self.tabs.addTab(gallery_tab, "Галерея")')
content = content.replace('self.tabs.addTab(library_tab, "?? Таблица")', 'self.tabs.addTab(library_tab, "Таблица")')

# Replace exact string just in case emojis were corrupted by powershell encoding
pattern_gallery = re.compile(r'self\.tabs\.addTab\(gallery_tab, ".*?\s*Галерея"\)')
content = pattern_gallery.sub('self.tabs.addTab(gallery_tab, "Галерея")', content)

pattern_table = re.compile(r'self\.tabs\.addTab\(library_tab, ".*?\s*Таблица"\)')
content = pattern_table.sub('self.tabs.addTab(library_tab, "Таблица")', content)


with open("ui/main_window.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Emojis removed.")
