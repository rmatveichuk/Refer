import sys

with open('ai/engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Change threshold from 0.19 to 0.12 to be safer, or just return the best always. Let's return best if > 0.12
content = content.replace("if best_tag and best_sim > 0.19:", "if best_tag and best_sim > 0.12:")

with open('ai/engine.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Lowered threshold in engine.py")