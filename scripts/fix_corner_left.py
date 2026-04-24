import re

with open("ui/main_window.py", "r", encoding="utf-8") as f:
    content = f.read()

# Instead of using setCornerWidget which inherently anchors to the right,
# we can wrap the QTabBar in a custom layout OR simply place the buttons above the QTabWidget in the content_layout!
# Wait, QTabWidget's stylesheet already draws the tab background.
# Actually, the QTabWidget::pane has a top border. If we make a custom row of buttons that LOOKS like tabs, it's easier.
# But since we already have QTabWidget, let's use the layout approach:
# Create a horizontal layout for the custom tab bar row.
# Hide the QTabWidget's built-in tab bar? No, that's too much work.

# Let's see if we can use a spacer in the corner widget to push it LEFT.
# A corner widget is just a QWidget positioned in the layout of the QTabWidget.
# If we set the corner widget's size policy to expanding, it will fill the space between tabs and the right edge.
# Then a left-aligned layout inside it will put the buttons next to the tabs!

old_corner = """        # --- Add Corner Buttons ---
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
        corner_layout.addStretch() # Push left
        
        self.tabs.setCornerWidget(corner)"""

new_corner = """        # --- Add Corner Buttons ---
        corner = QWidget()
        from PyQt6.QtWidgets import QSizePolicy
        corner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(5, 0, 0, 0)
        corner_layout.setSpacing(0)
        
        btn_style = "QPushButton { background: #1a1a1a; color: #888; padding: 8px 16px; border: none; border-top-left-radius: 4px; border-top-right-radius: 4px; } QPushButton:hover { background: #2d2d2d; color: #ccc; }"
        
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
        corner_layout.addStretch() # Push left
        
        self.tabs.setCornerWidget(corner)"""

if old_corner in content:
    content = content.replace(old_corner, new_corner)
    with open("ui/main_window.py", "w", encoding="utf-8") as f:
        f.write(content)
    print("Updated corner widget size policy")
else:
    print("Could not find corner widget code")
