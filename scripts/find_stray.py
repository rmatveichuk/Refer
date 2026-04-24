with open("ui/main_window.py", "r", encoding="utf-8") as f:
    for i, line in enumerate(f):
        if "gallery_layout.addWidget(self.gallery)" in line:
            print(f"Line {i}: {line.strip()}")
