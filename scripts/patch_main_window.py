import sys

old_code = """    def _setup_gallery_tab(self):
        gallery_tab = QWidget()
        gallery_layout = QVBoxLayout(gallery_tab)
        gallery_layout.setContentsMargins(0, 0, 0, 0)

        self.gallery = GalleryView()
        self.gallery_model = AssetListModel()
        self.gallery.setModel(self.gallery_model)
        self.gallery.db = self.db
        self.gallery.parent_window = self
        
        gallery_layout.addWidget(self.gallery)
        self.tabs.addTab(gallery_tab, "🎨 Галерея")"""

new_code = """    def _setup_gallery_tab(self):
        gallery_tab = QWidget()
        gallery_layout = QVBoxLayout(gallery_tab)
        gallery_layout.setContentsMargins(0, 0, 0, 0)
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
        action_layout.addStretch()

        self.gallery = GalleryView()
        self.gallery_model = AssetListModel()
        self.gallery.setModel(self.gallery_model)
        self.gallery.db = self.db
        self.gallery.parent_window = self
        
        gallery_layout.addWidget(action_toolbar)
        gallery_layout.addWidget(self.gallery)
        self.tabs.addTab(gallery_tab, "🎨 Галерея")"""

with open("ui/main_window.py", "r", encoding="utf-8") as f:
    content = f.read()

if old_code in content:
    content = content.replace(old_code, new_code)
    with open("ui/main_window.py", "w", encoding="utf-8") as f:
        f.write(content)
    print("Success")
else:
    print("Code not found")
