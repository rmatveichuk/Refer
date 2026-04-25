import sys

with open('ui/main_window.py', 'r', encoding='utf-8') as f:
    content = f.read()

import re

# Fix SearchWorker in main_window.py to use `object` correctly and import numpy
patch = """
                    else:
                        import numpy as np
                        self.signals.result.emit(np.array([], dtype=np.float32), "Search by tags")
"""

content = re.sub(
    r'else:\n\s*import numpy as np\n\s*self\.signals\.result\.emit\(np\.array\(\[\]\), "Search by tags"\)',
    patch.strip(),
    content,
    flags=re.DOTALL
)

with open('ui/main_window.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Patched numpy array type in SearchWorker")