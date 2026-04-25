import sys

with open('ui/main_window.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_worker_code = """
                    if v_final is not None:
                        v_final = v_final.reshape(1, -1)
                        self.signals.result.emit(v_final, "Комбинированный запрос" if self.text and self.img_path else "Векторный поиск")
                except Exception as e:
"""

new_worker_code = """
                    if v_final is not None:
                        v_final = v_final.reshape(1, -1)
                        self.signals.result.emit(v_final, "Комбинированный запрос" if self.text and self.img_path else "Векторный поиск")
                    else:
                        # Если нет ни текста, ни картинки (только теги), передаем пустой массив
                        import numpy as np
                        self.signals.result.emit(np.array([]), "Поиск по тегам")
                except Exception as e:
"""

# Let's try replacing with regex/simple find since the string might have different cyrillic encoding
import re
lines = content.split('\n')
for i, line in enumerate(lines):
    if 'self.signals.result.emit(v_final,' in line:
        # Check if next line is 'except Exception' or 'else:'
        if 'except Exception' in lines[i+1]:
            lines.insert(i+1, '                    else:')
            lines.insert(i+2, '                        import numpy as np')
            lines.insert(i+3, '                        self.signals.result.emit(np.array([]), "Search by tags")')
        break

with open('ui/main_window.py', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
    
print("Patched SearchWorker for tag-only search")
