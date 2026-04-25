import sys

with open('ui/main_window.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if 'def _perform_visual_search(self, text: str, img_path: str, threshold: float, sources: list, tags: list = None):' in line:
        # Check the next lines
        if 'if not text and not img_path:' in lines[i+1]:
            # Replace lines[i+1] and lines[i+2]
            lines[i+1] = '        if not text and not img_path and not tags:\n'
            lines[i+2] = '            return\n'
        break

with open('ui/main_window.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)
    
print("Patched empty search case")
