import sys

with open('ai/engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

extract_tags_code = """
    def extract_tags(self, image_path: str, vocabulary: dict = None) -> list[str]:
        \"\"\"
        Zero-Shot Classification: извлекает теги из картинки на основе базового словаря.
        Возвращает список лучших тегов (по 1-2 из каждой категории).
        \"\"\"
        if vocabulary is None:
            vocabulary = getattr(config, 'TAG_VOCABULARY', {})
            
        if not vocabulary:
            return []
            
        img_emb = self.get_image_embedding(image_path)
        if img_emb is None or np.all(img_emb == 0):
            return []
            
        # Инициализируем кэш векторов слов, если его нет
        if not hasattr(self, '_vocab_embeddings'):
            self._vocab_embeddings = {}
            
        extracted_tags = []
        
        for category, tags in vocabulary.items():
            best_tag = None
            best_sim = -1.0
            
            for tag in tags:
                if tag not in self._vocab_embeddings:
                    self._vocab_embeddings[tag] = self.get_text_embedding(tag)
                txt_emb = self._vocab_embeddings[tag]
                
                # Cosine similarity между нормализованными векторами - это просто dot product
                sim = np.dot(img_emb, txt_emb)
                
                if sim > best_sim:
                    best_sim = sim
                    best_tag = tag
                    
            # Порог уверенности для SigLIP. 
            # 0.18-0.20 обычно означает хорошее совпадение для этой модели.
            if best_tag and best_sim > 0.19:
                extracted_tags.append(best_tag)
                logger.info(f"Extracted tag: {best_tag} (sim: {best_sim:.3f})")
                
        return extracted_tags
"""

if "def extract_tags" not in content:
    content += extract_tags_code

with open('ai/engine.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Added extract_tags to ai/engine.py")