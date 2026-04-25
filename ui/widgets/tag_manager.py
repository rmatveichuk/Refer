from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, 
                             QListWidget, QListWidgetItem, QPushButton, QLabel, QWidget)
from PyQt6.QtCore import Qt, pyqtSignal

class TagManagerDialog(QDialog):
    def __init__(self, db, selected_tags=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.selected_tags = set(selected_tags or [])
        self.all_tags = {}
        
        self.setWindowTitle("Менеджер тегов")
        self.setFixedSize(500, 600)
        self.setStyleSheet("""
            QDialog { background-color: #121212; color: #e0e0e0; }
            QLabel { font-size: 13px; font-weight: bold; color: #888; }
            QLineEdit {
                background-color: #1e1e1e; color: #e0e0e0;
                border: 1px solid #444; border-radius: 6px;
                padding: 8px; font-size: 14px;
            }
            QLineEdit:focus { border-color: #29b6f6; }
            QListWidget {
                background-color: #1e1e1e; border: 1px solid #333;
                border-radius: 6px; outline: none; padding: 5px;
            }
            QListWidget::item { padding: 8px; border-bottom: 1px solid #2a2a2a; color: #ccc; }
            QListWidget::item:hover { background-color: #2a2a2a; }
            QListWidget::item:selected { background-color: #1e2a30; color: #29b6f6; font-weight: bold;}
            QPushButton {
                background-color: #2d2d2d; color: white;
                border: 1px solid #444; border-radius: 6px; padding: 8px 16px; font-weight: bold;
            }
            QPushButton:hover { background-color: #3d3d3d; }
            QPushButton#btn_apply { background-color: #29b6f6; color: black; border: none; }
            QPushButton#btn_apply:hover { background-color: #4fc3f7; }
        """)

        self._init_ui()
        self._load_tags()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        # Search
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Поиск тегов...")
        self.search_input.textChanged.connect(self._filter_tags)
        layout.addWidget(self.search_input)

        # Main lists layout
        lists_layout = QHBoxLayout()
        
        # Available tags
        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("Доступные теги (двойной клик):"))
        self.list_available = QListWidget()
        self.list_available.itemDoubleClicked.connect(self._add_tag)
        left_layout.addWidget(self.list_available)
        
        # Selected tags
        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("Выбранные (двойной клик):"))
        self.list_selected = QListWidget()
        self.list_selected.itemDoubleClicked.connect(self._remove_tag)
        right_layout.addWidget(self.list_selected)

        lists_layout.addLayout(left_layout)
        lists_layout.addLayout(right_layout)
        layout.addLayout(lists_layout)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        
        btn_apply = QPushButton("Применить")
        btn_apply.setObjectName("btn_apply")
        btn_apply.clicked.connect(self.accept)
        
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_apply)
        layout.addLayout(btn_layout)

    def _load_tags(self):
        # Fetch tags from DB
        try:
            self.all_tags = self.db.get_all_tags() # {tag_name: count}
        except Exception as e:
            print(f"Error loading tags: {e}")
            self.all_tags = {}

        self._populate_lists()

    def _populate_lists(self):
        self.list_available.clear()
        self.list_selected.clear()

        # Populate selected list
        for tag in sorted(self.selected_tags):
            item = QListWidgetItem(tag)
            item.setData(Qt.ItemDataRole.UserRole, tag)
            self.list_selected.addItem(item)

        # Populate available list
        search_text = self.search_input.text().lower().strip()
        
        for tag, count in self.all_tags.items():
            if tag in self.selected_tags:
                continue
            if search_text and search_text not in tag.lower():
                continue
                
            display_text = f"{tag} ({count})"
            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, tag)
            self.list_available.addItem(item)

    def _filter_tags(self):
        self._populate_lists()

    def _add_tag(self, item):
        tag = item.data(Qt.ItemDataRole.UserRole)
        self.selected_tags.add(tag)
        self.search_input.clear()
        self._populate_lists()

    def _remove_tag(self, item):
        tag = item.data(Qt.ItemDataRole.UserRole)
        if tag in self.selected_tags:
            self.selected_tags.remove(tag)
            self._populate_lists()

    def get_selected_tags(self):
        return list(self.selected_tags)
