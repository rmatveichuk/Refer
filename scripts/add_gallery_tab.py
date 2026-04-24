with open("ui/main_window.py", "r", encoding="utf-8") as f:
    content = f.read()

# I need to insert it right before _setup_library_tab
method = """    def _setup_gallery_tab(self):
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
        self.tabs.addTab(gallery_tab, "🎨 Галерея")

    def _setup_library_tab"""

content = content.replace("    def _setup_library_tab", method)

with open("ui/main_window.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Added clean tab")
