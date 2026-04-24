with open('ui/main_window.py', 'r', encoding='utf-8') as f:
    lines = [line.strip() for line in f if 'status_label' in line]
with open('scripts/status_label.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
