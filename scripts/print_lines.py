import sys
with open("ui/main_window.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "def _setup_gallery_tab" in line:
        print("".join(lines[i:i+15]))
        break
