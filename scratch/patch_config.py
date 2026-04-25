import sys

with open('config.py', 'r', encoding='utf-8') as f:
    content = f.read()

vocab_code = """

# Базовый словарь тегов для авто-тегирования (Zero-Shot Classification)
# Вы можете редактировать этот список под свои нужды.
TAG_VOCABULARY = {
    "Material": [
        "concrete", "wood", "brick", "glass", "metal", 
        "stone", "plaster", "marble", "fabric"
    ],
    "Lighting": [
        "natural light", "artificial light", "warm light", 
        "cold light", "sunny", "overcast", "shadows", "neon"
    ],
    "Style": [
        "minimalism", "industrial", "classic", "modern", 
        "brutalism", "futuristic", "cozy", "luxurious"
    ],
    "Type": [
        "exterior", "interior", "residential", "commercial", 
        "public space", "landscape", "close-up", "furniture"
    ]
}
"""

if "TAG_VOCABULARY" not in content:
    content += vocab_code

with open('config.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Added TAG_VOCABULARY to config.py")