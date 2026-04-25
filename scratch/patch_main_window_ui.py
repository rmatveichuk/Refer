import sys

with open('ui/main_window.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Imports
if "from ui.widgets.tag_manager import TagManagerDialog" not in content:
    content = content.replace("from ui.widgets.search_panel import SearchPanel", "from ui.widgets.search_panel import SearchPanel\nfrom ui.widgets.tag_manager import TagManagerDialog")

# 2. Connect signal
if "self.search_panel.manage_tags_requested.connect(self._open_tag_manager)" not in content:
    content = content.replace("self.search_panel.search_triggered.connect(self._perform_visual_search)", "self.search_panel.search_triggered.connect(self._perform_visual_search)\n        self.search_panel.manage_tags_requested.connect(self._open_tag_manager)")

# 3. Add _open_tag_manager method
open_tag_mgr_code = """
    def _open_tag_manager(self):
        selected_tags = getattr(self.search_panel, 'selected_tags', [])
        dialog = TagManagerDialog(self.db, selected_tags, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_tags = dialog.get_selected_tags()
            self.search_panel.set_selected_tags(new_tags)
            self.search_panel._emit_search()

"""
if "def _open_tag_manager" not in content:
    content = content.replace("    def _perform_visual_search", open_tag_mgr_code + "    def _perform_visual_search")

# 4. Update _perform_visual_search signature
if "def _perform_visual_search(self, text: str, img_path: str, threshold: float, sources: list, tags: list):" not in content:
    content = content.replace("def _perform_visual_search(self, text: str, img_path: str, threshold: float, sources: list):", "def _perform_visual_search(self, text: str, img_path: str, threshold: float, sources: list, tags: list = None):")

if "self.search_sources = sources" in content and "self.search_tags = tags" not in content:
    content = content.replace("self.search_sources = sources", "self.search_sources = sources\n        self.search_tags = tags or []")

with open('ui/main_window.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched main_window.py (UI part)")
