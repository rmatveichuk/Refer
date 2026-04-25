import sys

with open('ai/engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

import re

# Update logic: Instead of a hard threshold, let's pick the top-1 from each category,
# but only if the similarity is greater than 0.035 (since some scores naturally cluster around 0.0)
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
                    
            # Для SigLIP scores могут быть очень низкими для фоновых элементов.
            # Мы берем лучший тег в каждой категории, если скор хотя бы положительный и больше 0.035
            if best_tag and best_sim > 0.035:
                extracted_tags.append(best_tag)
                logger.info(f"Extracted {category} tag: {best_tag} (sim: {best_sim:.3f})")
"""

content = re.sub(
    r'for category, tags in vocabulary\.items\(\):.*?if best_tag and best_sim > 0\.04:\s*extracted_tags\.append\(best_tag\)\s*logger\.info\(f"Extracted tag: \{best_tag\} \(sim: \{best_sim:\.3f\}\)"\)',
    new_logic.strip(),
    content,
    flags=re.DOTALL
)

with open('ai/engine.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Patched threshold to dynamic best-in-category")