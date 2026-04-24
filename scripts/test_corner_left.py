import sys
from PyQt6.QtWidgets import QApplication, QMainWindow, QTabWidget, QWidget, QHBoxLayout, QPushButton
from PyQt6.QtCore import Qt

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(800, 600)
        self.setStyleSheet("QMainWindow { background-color: #121212; }")

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet('''
            QTabWidget::pane { border: none; border-left: 1px solid #333; }
            QTabBar::tab { background: #1a1a1a; color: #888; padding: 8px 16px; border: none; }
            QTabBar::tab:selected { background: #29b6f6; color: #000; font-weight: bold; }
        ''')

        # To align corner widget to the left of the corner area (right after the tabs)
        # we actually need to specify the TopLeft corner if we want it *before* the tabs,
        # but Qt places tabs at TopLeft by default.
        # The TopRight corner widget is anchored to the right side of the TabBar area.
        # To push buttons to the left (next to tabs), we need to set corner widget alignment,
        # but QTabWidget doesn't easily support that for TopRight corner.
        
        # We can add a stretch to the RIGHT side of the buttons in the corner layout.
        corner = QWidget()
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(5, 0, 0, 0)
        corner_layout.setSpacing(0)
        
        btn_style = "QPushButton { background: #1a1a1a; color: #888; padding: 8px 16px; border: none; } QPushButton:hover { background: #2d2d2d; color: #ccc; }"
        
        btn_select_all = QPushButton("Выделить все")
        btn_select_all.setStyleSheet(btn_style)
        
        btn_delete_selected = QPushButton("Удалить")
        btn_delete_selected.setStyleSheet(btn_style)
        
        corner_layout.addWidget(btn_select_all)
        corner_layout.addWidget(btn_delete_selected)
        corner_layout.addStretch(1) # Stretch on the right pushes buttons to the left!

        self.tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)

        self.tabs.addTab(QWidget(), "Галерея")
        self.tabs.addTab(QWidget(), "Таблица")

        self.setCentralWidget(self.tabs)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
