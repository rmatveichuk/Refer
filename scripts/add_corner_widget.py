import re

with open("ui/main_window.py", "r", encoding="utf-8") as f:
    content = f.read()

# Находим инициализацию вкладок
setup_tabs = """        self.tabs = QTabWidget()
        self.tabs.setStyleSheet('''
            QTabWidget::pane { border: none; border-top: 1px solid #333; }
            QTabBar::tab { background: #252525; color: #aaa; padding: 8px 20px; border: none; border-top-left-radius: 4px; border-top-right-radius: 4px; }
            QTabBar::tab:selected { background: #1e1e1e; color: white; border-bottom: 2px solid #29b6f6; }
            QTabBar::tab:hover:!selected { background: #2d2d2d; }
        ''')
        
        self._setup_gallery_tab()
        self._setup_library_tab()"""

new_setup_tabs = """        self.tabs = QTabWidget()
        self.tabs.setStyleSheet('''
            QTabWidget::pane { border: none; border-top: 1px solid #333; }
            QTabBar::tab { background: #252525; color: #aaa; padding: 8px 20px; border: none; border-top-left-radius: 4px; border-top-right-radius: 4px; }
            QTabBar::tab:selected { background: #1e1e1e; color: white; border-bottom: 2px solid #29b6f6; }
            QTabBar::tab:hover:!selected { background: #2d2d2d; }
        ''')
        
        # Corner widget for Gallery actions
        corner = QWidget()
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(5, 0, 0, 0)
        corner_layout.setSpacing(5)
        
        btn_select_all = QPushButton("✅ Выделить все")
        btn_select_all.setStyleSheet("QPushButton { background-color: #333; color: white; border: 1px solid #555; border-radius: 4px; padding: 4px 10px; } QPushButton:hover { background-color: #444; }")
        btn_select_all.clicked.connect(self._select_all_gallery)
        
        btn_delete_selected = QPushButton("🗑️ Удалить")
        btn_delete_selected.setStyleSheet("QPushButton { background-color: #f44336; color: white; border: none; border-radius: 4px; padding: 4px 10px; } QPushButton:hover { background-color: #d32f2f; }")
        btn_delete_selected.clicked.connect(self._delete_selected_gallery)
        
        corner_layout.addWidget(btn_select_all)
        corner_layout.addWidget(btn_delete_selected)
        corner_layout.addStretch()
        
        self.tabs.setCornerWidget(corner)
        
        self._setup_gallery_tab()
        self._setup_library_tab()"""

content = content.replace(setup_tabs, new_setup_tabs)

with open("ui/main_window.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Added corner widget")
