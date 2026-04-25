import sys

with open('ui/main_window.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Line 855 and 857 are duplicate "class SearchWorker(QRunnable):"
# One of them has no body (IndentationError). We should delete line 855 and 856.
del lines[854:856]

with open('ui/main_window.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("Fixed IndentationError")