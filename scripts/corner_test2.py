import sys
from PyQt6.QtWidgets import QApplication, QMainWindow, QTabWidget, QWidget, QHBoxLayout, QPushButton, QTabBar
from PyQt6.QtCore import Qt

class CustomTabWidget(QTabWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Tab bar itself
        self.btn_select_all = QPushButton("Выделить все")
        self.btn_delete_selected = QPushButton("Удалить")
        
        btn_style = "QPushButton { background: #1a1a1a; color: #888; padding: 8px 16px; border: none; } QPushButton:hover { background: #2d2d2d; color: #ccc; }"
        self.btn_select_all.setStyleSheet(btn_style)
        self.btn_delete_selected.setStyleSheet(btn_style)

app = QApplication(sys.argv)
win = QMainWindow()
tabs = CustomTabWidget()
tabs.addTab(QWidget(), "Галерея")
tabs.addTab(QWidget(), "Таблица")

# We can actually use setCornerWidget with TopLeftCorner? No, that pushes the tabs to the right.
# Let's try adding a QWidget with a stretch to the TopRightCorner.
corner = QWidget()
l = QHBoxLayout(corner)
l.setContentsMargins(0, 0, 0, 0)
l.setSpacing(0)
l.addWidget(tabs.btn_select_all)
l.addWidget(tabs.btn_delete_selected)
l.addStretch()

# Set corner widget style so it fills space
corner.setStyleSheet("background: transparent;")
tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)

# Actually, the corner widget only takes up as much space as it needs, UNLESS we tell it to expand.
# BUT it anchors to the right edge.
# To make it anchor to the left edge (right after tabs), we need to make the tab bar itself expand?

win.setCentralWidget(tabs)
win.resize(600, 400)
# win.show()
