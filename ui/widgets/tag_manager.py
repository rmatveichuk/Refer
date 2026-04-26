from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, 
                             QPushButton, QLabel, QWidget, QScrollArea, QSizePolicy)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from .flow_layout import FlowLayout
from .tag_chip import TagChip
from ui.translations import tr


class TagManagerDialog(QDialog):
    def __init__(self, db, selected_tags=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.selected_tags = set(selected_tags or [])
        self.available_tags = {}
        
        self.setWindowTitle(tr("tag_manager"))
        self.setFixedSize(600, 750) # Increased height as requested
        self.setStyleSheet("""
            QDialog { background-color: #121212; color: #e0e0e0; }
            QLabel { font-size: 14px; font-weight: bold; color: #aaa; margin-bottom: 5px; }
            QLineEdit {
                background-color: #1e1e1e; color: #e0e0e0;
                border: 1px solid #444; border-radius: 8px;
                padding: 10px; font-size: 15px;
            }
            QLineEdit:focus { border-color: #29b6f6; }
            QScrollArea { border: none; background-color: transparent; }
            QWidget#scroll_content { background-color: transparent; }
        """)

        self._init_ui()
        self._load_tags()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        # Selected tags area (Breadcrumbs)
        self.selected_area = QWidget()
        self.selected_layout = FlowLayout(self.selected_area, margin=0, hSpacing=8, vSpacing=8)
        self.selected_area.setVisible(False)
        layout.addWidget(self.selected_area)

        # Search input
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(tr("tag_input_placeholder"))
        self.search_input.textChanged.connect(self._filter_tags)
        self.search_input.returnPressed.connect(self._add_custom_tag)
        layout.addWidget(self.search_input)

        # Available tags area (Suggestions)
        self.lbl_suggested = QLabel(tr("suggested_tags"))
        layout.addWidget(self.lbl_suggested)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("scroll_content")
        self.suggestions_layout = FlowLayout(self.scroll_content, margin=0, hSpacing=8, vSpacing=8)
        self.scroll_area.setWidget(self.scroll_content)
        
        layout.addWidget(self.scroll_area)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        self.btn_cancel = QPushButton(tr("cancel"))
        self.btn_cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: transparent; color: #888;
                border: 1px solid #444; border-radius: 6px; padding: 8px 16px; font-weight: bold;
            }
            QPushButton:hover { background-color: #2d2d2d; color: white; }
        """)
        self.btn_cancel.clicked.connect(self.reject)
        
        self.btn_apply = QPushButton(tr("apply"))
        self.btn_apply.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_apply.setStyleSheet("""
            QPushButton {
                background-color: #29b6f6; color: black;
                border: none; border-radius: 6px; padding: 8px 24px; font-weight: bold;
            }
            QPushButton:hover { background-color: #4fc3f7; }
        """)
        self.btn_apply.clicked.connect(self.accept)
        
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_apply)
        layout.addLayout(btn_layout)

    def _load_tags(self):
        """Loads contextual tag suggestions."""
        search_text = self.search_input.text().strip()
        
        # We use the same smart logic as in the sidebar
        self.available_tags = self.db.get_contextual_suggestions(
            list(self.selected_tags), 
            search_text, 
            limit=100
        )
        self._update_selected_ui()
        self._filter_tags()

    def _update_selected_ui(self):
        # Clear layout
        while self.selected_layout.count():
            item = self.selected_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        if not self.selected_tags:
            self.selected_area.setVisible(False)
            return

        self.selected_area.setVisible(True)
        for tag in sorted(self.selected_tags):
            chip = TagChip(tag, is_active=True, has_delete=True)
            chip.removed_tag.connect(lambda t: self._remove_tag(t))
            self.selected_layout.addWidget(chip)

    def _filter_tags(self):
        # Clear suggestions
        while self.suggestions_layout.count():
            item = self.suggestions_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        for tag, count in self.available_tags.items():
            if tag in self.selected_tags:
                continue
            
            chip = TagChip(tag, count=count)
            chip.clicked_tag.connect(lambda t: self._add_tag(t))
            self.suggestions_layout.addWidget(chip)

    def _add_tag(self, tag):
        self.selected_tags.add(tag)
        self.search_input.clear()
        self._load_tags() # Recalculate related tags

    def _add_custom_tag(self):
        tag = self.search_input.text().strip().lower()
        if tag:
            self.selected_tags.add(tag)
            self.search_input.clear()
            self._load_tags()

    def _remove_tag(self, tag):
        if tag in self.selected_tags:
            self.selected_tags.remove(tag)
            self._load_tags() # Recalculate related tags

    def get_selected_tags(self):
        return list(self.selected_tags)

    def retranslate_ui(self):
        self.setWindowTitle(tr("tag_manager"))
        self.search_input.setPlaceholderText(tr("tag_input_placeholder"))
        self.lbl_suggested.setText(tr("suggested_tags"))
        self.btn_cancel.setText(tr("cancel"))
        self.btn_apply.setText(tr("apply"))