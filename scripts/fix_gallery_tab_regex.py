import re

with open("ui/main_window.py", "r", encoding="utf-8") as f:
    content = f.read()

# Let's fix corner widget instantiation first, because we attached it before self.gallery exists!
# The corner widget calls self._select_all_gallery which accesses self.gallery
# Wait, self._select_all_gallery is a method reference, so it doesn't need self.gallery to exist yet.
# BUT let's see _setup_gallery_tab again.

pattern = re.compile(
    r'(def _setup_gallery_tab\(self\):.*?gallery_layout\.setSpacing\(0\)\n\n)\s+gallery_layout\.addWidget\(self\.gallery\)\n(.*?self\.tabs\.addTab\(gallery_tab,.*?\n)',
    re.DOTALL
)

def repl(m):
    return m.group(1) + m.group(2) + "        gallery_layout.addWidget(self.gallery)\n"

new_content = pattern.sub(repl, content)

with open("ui/main_window.py", "w", encoding="utf-8") as f:
    f.write(new_content)
print("Regex fix applied")
