import sys

with open('ui/main_window.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace("result = pyqtSignal(np.ndarray, str)", "result = pyqtSignal(object, str)")

with open('ui/main_window.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Patched pyqtSignal")