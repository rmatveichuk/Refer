import sys

with open('ai/engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_logic = """
            # Порог уверенности для SigLIP. 
            # 0.18-0.20 обычно означает хорошее совпадение для этой модели.
            if best_tag and best_sim > 0.12:
                extracted_tags.append(best_tag)
                logger.info(f"Extracted tag: {best_tag} (sim: {best_sim:.3f})")
"""

new_logic = """
            # В SigLIP dot-product обычно лежит в диапазоне 0.0 - 0.25
            # Для архитектуры мы снижаем порог, чтобы давать хоть какие-то результаты
            # Но если уверенность совсем низкая (меньше 0.04), лучше ничего не выдавать
            if best_tag and best_sim > 0.04:
                extracted_tags.append(best_tag)
                logger.info(f"Extracted tag: {best_tag} (sim: {best_sim:.3f})")
"""

# Try to replace safely
import re
content = re.sub(r'if best_tag and best_sim > 0\.12:.*?\)\)', new_logic.strip(), content, flags=re.DOTALL)

with open('ai/engine.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched threshold to 0.04")