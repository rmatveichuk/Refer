import re

with open("ui/main_window.py", "r", encoding="utf-8") as f:
    content = f.read()

# Fix the spacing/addWidget issue in _setup_gallery_tab
old_tab = """    def _setup_gallery_tab(self):
        gallery_tab = QWidget()
        gallery_layout = QVBoxLayout(gallery_tab)
        gallery_layout.setContentsMargins(0, 0, 0, 0)
        gallery_layout.setSpacing(0)

        gallery_layout.addWidget(self.gallery)
        self.gallery = GalleryView()
        self.gallery_model = AssetListModel()
        self.gallery.setModel(self.gallery_model)
        self.gallery.db = self.db
        self.gallery.parent_window = self
        
        self.tabs.addTab(gallery_tab, "🎨 Галерея")"""

# Let's use regex just to be safe if order is messed up
pattern = re.compile(r'    def _setup_gallery_tab\(self\):.*?self\.tabs\.addTab\(gallery_tab, "🎨 Галерея"\)', re.DOTALL)

new_tab = """    def _setup_gallery_tab(self):
        gallery_tab = QWidget()
        gallery_layout = QVBoxLayout(gallery_tab)
        gallery_layout.setContentsMargins(0, 0, 0, 0)
        gallery_layout.setSpacing(0)

        self.gallery = GalleryView()
        self.gallery_model = AssetListModel()
        self.gallery.setModel(self.gallery_model)
        self.gallery.db = self.db
        self.gallery.parent_window = self
        
        gallery_layout.addWidget(self.gallery)
        self.tabs.addTab(gallery_tab, "🎨 Галерея")"""

content = pattern.sub(new_tab, content)

with open("ui/main_window.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Fixed _setup_gallery_tab")
