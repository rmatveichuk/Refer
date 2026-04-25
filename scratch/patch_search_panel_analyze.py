import sys

with open('ui/widgets/search_panel.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add signal
if "extract_tags_requested = pyqtSignal(str)" not in content:
    content = content.replace("manage_tags_requested = pyqtSignal()", "manage_tags_requested = pyqtSignal()\n    extract_tags_requested = pyqtSignal(str)")

# 2. Connect from hybrid_input
if "self.hybrid_input.analyze_requested.connect(self.extract_tags_requested.emit)" not in content:
    content = content.replace("self.hybrid_input.search_requested.connect(self._on_hybrid_enter)", "self.hybrid_input.search_requested.connect(self._on_hybrid_enter)\n        self.hybrid_input.analyze_requested.connect(self.extract_tags_requested.emit)")

with open('ui/widgets/search_panel.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched search_panel.py for analyze")