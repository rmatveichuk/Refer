import sys

with open('ui/main_window.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_faiss = """
        # 2. FAISS Vector Search with pre-filtering
        k = min(500, len(valid_ids))
        distances, ids = self.faiss_mgr.search(vector, k=k, valid_ids=valid_ids)

        import math
        max_distance = 2.0 * math.exp(-5.3 * self.search_threshold)

        results = [(dist, int(aid)) for dist, aid in zip(distances, ids) if aid > 0 and dist <= max_distance]
"""

new_faiss = """
        # 2. FAISS Vector Search with pre-filtering
        results = []
        if vector is not None and len(vector) > 0:
            k = min(500, len(valid_ids))
            distances, ids = self.faiss_mgr.search(vector, k=k, valid_ids=valid_ids)

            import math
            max_distance = 2.0 * math.exp(-5.3 * self.search_threshold)
            results = [(dist, int(aid)) for dist, aid in zip(distances, ids) if aid > 0 and dist <= max_distance]
        else:
            # Если нет вектора (только теги), берем все валидные ID с дистанцией 0
            # Ограничиваем до 500 чтобы не перегружать интерфейс
            results = [(0.0, int(aid)) for aid in valid_ids[:500]]
"""

content = content.replace(old_faiss, new_faiss)

with open('ui/main_window.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Patched _search_vectors to handle empty vector")