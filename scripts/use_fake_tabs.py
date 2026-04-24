import re

with open("ui/main_window.py", "r", encoding="utf-8") as f:
    content = f.read()

# Since QTabWidget strictly anchors the TopRightCorner widget to the right side,
# using a QWidget with Expanding size policy won't push it to the left edge of that area.
# To put buttons NEXT to the tabs, we can simply add custom tabs to the QTabWidget!
# Wait, we can't easily make a tab act like a button without selecting it.
# BUT we can put a widget inside the tab bar! `tabs.tabBar().setTabButton()` ? No, that's inside a tab.
# `tabs.tabBar().setMinimumWidth()` ?
# The standard trick to add buttons to QTabWidget immediately following tabs is:
# Add tabs that have no content, but we catch their clicks? No, that's hacky.

# Let's remove the CornerWidget and instead build a custom Header layout above the QTabWidget pane.
# OR just add the buttons to the Search Panel row?
# The user wants them in the "Tab row".
# We can make a separate QHBoxLayout that holds the TabBar AND the buttons, and then the StackedWidget.
# BUT QTabWidget encapsulates the TabBar and the StackedWidget.
# If we want to intercept the QTabBar, we can just position the buttons absolutely? No.

# Let's try inserting the buttons into the QTabBar directly using a spacer tab?
# No, let's just make the QTabWidget transparent and put our custom header above it.
# Wait, what if we just use a separate row entirely?
# "переместить новые кнопки в левую часть, сделать в стеле кнопки "Таблица""
# Let's just create a custom TabBar class that includes the buttons. Or better yet:
# Hide the QTabWidget's tab bar, and make our own custom tab bar row!

# Let's just create a completely custom top bar for the tabs!
# Actually, the simplest workaround is to NOT use QTabWidget's corner widget, but inject our own QWidget right above the tabs?
# But we already have the tabs there. 

# Let's check how QTabWidget handles stylesheets for left-aligned tabs.
# By default, tabs are left-aligned. The corner widget is right-aligned. 
# There's no "LeftCorner" for after the tabs. There's TopLeftCorner which is BEFORE the tabs.
# If we set corner widget to TopLeftCorner, it puts the buttons on the far left, and the tabs are pushed to the right of them!
# "сделать в стеле кнопки "Таблица"" means they should look like tabs.
# Let's just add them as regular tabs, but intercept the `currentChanged` signal!
# Yes! We can add "Выделить все" and "Удалить" as actual tabs, and when clicked, we trigger the action and switch back to the previous tab!

old_corner_block = """        # --- Add Corner Buttons ---
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

new_corner_block = """        # --- We will add the buttons as "fake tabs" instead ---
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._previous_tab_index = 0"""

if old_corner_block in content:
    content = content.replace(old_corner_block, new_corner_block)
else:
    print("Could not find corner block")

content = content.replace('self.tabs.addTab(gallery_tab, "Галерея")', 'self.tabs.addTab(gallery_tab, "Галерея")\n        self.tabs.addTab(QWidget(), "Выделить все")\n        self.tabs.addTab(QWidget(), "Удалить")')

# Now add the method to handle fake tabs
tab_handler = """    def _on_tab_changed(self, index):
        tab_text = self.tabs.tabText(index)
        if tab_text == "Выделить все":
            self.tabs.setCurrentIndex(self._previous_tab_index)
            self._select_all_gallery()
        elif tab_text == "Удалить":
            self.tabs.setCurrentIndex(self._previous_tab_index)
            self._delete_selected_gallery()
        else:
            self._previous_tab_index = index

    def _select_all_gallery"""

content = content.replace('    def _select_all_gallery', tab_handler)

with open("ui/main_window.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Replaced corner widget with fake tabs")
