import sys

with open('ui/main_window.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if line.startswith('def _search_vectors(self, vector: np.ndarray, query_info: str):'):
        lines[i] = '    ' + line
        break

with open('ui/main_window.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)
    
print("Fixed indentation of _search_vectors")
