import re

with open("ui/main_window.py", "r", encoding="utf-8") as f:
    content = f.read()

pattern = re.compile(
    r'(def _setup_gallery_tab\(self\):.*?gallery_layout\.setContentsMargins\(0, 0, 0, 0\))',
    re.DOTALL
)

replacement = r'''\1
        gallery_layout.setSpacing(0)

        action_toolbar = QWidget()
        action_toolbar.setStyleSheet("background-color: #252525; border-bottom: 1px solid #333;")
        action_layout = QHBoxLayout(action_toolbar)
        action_layout.setContentsMargins(10, 5, 10, 5)
        
        btn_select_all = QPushButton("✅ Выделить все")
        btn_select_all.setStyleSheet("QPushButton { background-color: #333; color: white; border: 1px solid #555; border-radius: 4px; padding: 4px 10px; } QPushButton:hover { background-color: #444; }")
        btn_select_all.clicked.connect(self._select_all_gallery)
        
        btn_delete_selected = QPushButton("🗑️ Удалить")
        btn_delete_selected.setStyleSheet("QPushButton { background-color: #f44336; color: white; border: none; border-radius: 4px; padding: 4px 10px; } QPushButton:hover { background-color: #d32f2f; }")
        btn_delete_selected.clicked.connect(self._delete_selected_gallery)
        
        action_layout.addWidget(btn_select_all)
        action_layout.addWidget(btn_delete_selected)
        action_layout.addStretch()'''

new_content = pattern.sub(replacement, content, count=1)

pattern2 = re.compile(
    r'(gallery_layout\.addWidget\(self\.gallery\))',
    re.DOTALL
)
replacement2 = r'gallery_layout.addWidget(action_toolbar)\n        \1'

new_content = pattern2.sub(replacement2, new_content, count=1)

with open("ui/main_window.py", "w", encoding="utf-8") as f:
    f.write(new_content)
print("Regex patch applied")
