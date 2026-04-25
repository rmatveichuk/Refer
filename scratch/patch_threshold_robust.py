import sys

with open('ai/engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

import re

new_logic = """
        for category, tags in vocabulary.items():
            best_tag = None
            best_sim = -1.0
            
            for tag in tags:
                if tag not in self._vocab_embeddings:
                    self._vocab_embeddings[tag] = self.get_text_embedding(tag)
                txt_emb = self._vocab_embeddings[tag]
                
                # Cosine similarity между нормализованными векторами
                sim = float(np.dot(img_emb, txt_emb))
                
                if sim > best_sim:
                    best_sim = sim
                    best_tag = tag
                    
            # Берем лучший тег из категории. Порог 0.035
            if best_tag and best_sim > 0.035:
                extracted_tags.append(best_tag)
                logger.info(f"Extracted {category} tag: {best_tag} (sim: {best_sim:.3f})")
"""

# Let's do a more robust replacement by replacing the entire loop
content = re.sub(
    r'for category, tags in vocabulary\.items\(\):.*?return extracted_tags',
    new_logic.strip() + "\n                \n        return extracted_tags",
    content,
    flags=re.DOTALL
)

with open('ai/engine.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched threshold robustly")