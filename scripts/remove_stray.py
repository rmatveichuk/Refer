with open("ui/main_window.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

lines[147] = "\n"

with open("ui/main_window.py", "w", encoding="utf-8") as f:
    f.writelines(lines)
print("Removed stray line")
