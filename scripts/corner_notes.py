import re

with open("ui/main_window.py", "r", encoding="utf-8") as f:
    content = f.read()

# I used addStretch() at the end of the layout, which pushed the buttons to the left of the corner widget area.
# BUT the corner widget area ITSELF is anchored to the right side of the window.
# In Qt, if we want buttons immediately after the last tab, it's very difficult using CornerWidget if the window is wide.
# Let's fix this by adding a custom "tab" to the TabBar, or simply changing the order of stretches.
# Wait, if we put the stretch on the RIGHT, they push LEFT. 
# But the QTabWidget allocates the *entire remaining space* to the corner widget. 
# Let's verify by replacing addStretch() with addStretch() on the right side.

# My previous code:
# corner_layout.addWidget(btn_select_all)
# corner_layout.addWidget(btn_delete_selected)
# corner_layout.addStretch() # Push left (stretch is on the right)

# Ah! If the stretch is on the right, they ARE pushed left. But the CornerWidget itself might be right-aligned by Qt!
# Actually, if we use QTabWidget::CornerWidget, it's anchored to the right. 
# To make buttons stick to the tabs, we need to add them differently.

# Let's change the layout structure slightly. Instead of a corner widget, we can create a horizontal layout for the TabBar!
# Or, Qt allows us to put buttons in a custom layout right on top of the tab widget, but that's messy.
# The simplest trick is to set the QTabWidget's corner widget to TopRight, and the layout stretch handles the rest.
# Let's check how I wrote it.
