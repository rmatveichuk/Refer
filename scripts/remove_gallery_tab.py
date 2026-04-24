import re

with open("ui/main_window.py", "r", encoding="utf-8") as f:
    content = f.read()

# Just forcefully rewrite _setup_gallery_tab
pattern = re.compile(r'    def _setup_gallery_tab\(self\):.*?self\.tabs\.addTab.*?$', re.MULTILINE | re.DOTALL)

def repl(m):
    text = m.group(0)
    # cut it out
    return ""

new_content = pattern.sub(repl, content)

with open("ui/main_window.py", "w", encoding="utf-8") as f:
    f.write(new_content)
print("Removed old tab")
