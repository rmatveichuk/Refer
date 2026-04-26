from PyQt6.QtWidgets import QPushButton
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor

class TagChip(QPushButton):
    """
    A reusable chip component for tags.
    Can be used for suggestions (without delete) or as active filter (with delete).
    """
    clicked_tag = pyqtSignal(str)
    removed_tag = pyqtSignal(str)

    def __init__(self, text, count=None, is_active=False, has_delete=False, parent=None):
        super().__init__(parent)
        self.tag_name = text
        self.is_active = is_active
        self.has_delete = has_delete
        
        display_text = text
        if count is not None and count > 0:
            display_text += f" ({count})"
        if has_delete:
            display_text += "  ✕"
            
        self.setText(display_text)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setMinimumHeight(28)
        
        self.update_style()
        self.clicked.connect(self._handle_click)

    def update_style(self):
        if self.is_active:
            # Blue active state
            style = """
                QPushButton {
                    background-color: #29b6f6; color: black;
                    border: none; border-radius: 14px; padding: 4px 14px;
                    font-weight: bold; font-size: 12px;
                }
                QPushButton:hover { background-color: #4fc3f7; }
            """
            if self.has_delete:
                style += "QPushButton:hover { background-color: #e57373; color: white; }"
        else:
            # Dark suggestion state
            style = """
                QPushButton {
                    background-color: #1e1e1e; color: #e0e0e0;
                    border: 1px solid #444; border-radius: 14px; padding: 4px 14px;
                    font-size: 12px;
                }
                QPushButton:hover { background-color: #2d2d2d; border-color: #29b6f6; color: #29b6f6; }
            """
        self.setStyleSheet(style)

    def _handle_click(self):
        if self.has_delete:
            self.removed_tag.emit(self.tag_name)
        else:
            self.clicked_tag.emit(self.tag_name)
