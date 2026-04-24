import re

with open("ui/main_window.py", "r", encoding="utf-8") as f:
    content = f.read()

setup_tabs_original = """        # --- Right: Gallery & Library Tabs ---
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(\"\"\"
            QTabWidget::pane { border: none; border-left: 1px solid #333; }
            QTabBar::tab { background: #1a1a1a; color: #888; padding: 8px 16px; border: none; }
            QTabBar::tab:selected { background: #29b6f6; color: #000; font-weight: bold; }
        \"\"\")
        content_layout.addWidget(self.tabs, 1)

        self._setup_gallery_tab()
        self._setup_library_tab()"""

setup_tabs_new = """        # --- Right: Gallery & Library Tabs ---
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(\"\"\"
            QTabWidget::pane { border: none; border-left: 1px solid #333; }
            QTabBar::tab { background: #1a1a1a; color: #888; padding: 8px 16px; border: none; }
            QTabBar::tab:selected { background: #29b6f6; color: #000; font-weight: bold; }
        \"\"\")
        
        # --- Add Corner Buttons ---
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
        corner_layout.addStretch() # Push left
        
        self.tabs.setCornerWidget(corner)
        
        content_layout.addWidget(self.tabs, 1)

        self._setup_gallery_tab()
        self._setup_library_tab()"""

if setup_tabs_original in content:
    content = content.replace(setup_tabs_original, setup_tabs_new)
    with open("ui/main_window.py", "w", encoding="utf-8") as f:
        f.write(content)
    print("Re-injected corner widget")
else:
    print("Could not find the target string")
