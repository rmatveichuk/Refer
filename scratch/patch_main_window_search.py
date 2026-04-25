import sys
import re

with open('ui/main_window.py', 'r', encoding='utf-8') as f:
    content = f.read()

new_search_vectors = """
    def _search_vectors(self, vector: np.ndarray, query_info: str):
        # 1. Pre-filtering: Gather IDs based on sources and tags
        web_domains = []
        folder_paths = []
        for src in self.search_sources:
            if src == 'archdaily':
                web_domains.append('archdaily.com')
            elif src == 'behance':
                web_domains.append('behance.net')
            else:
                folder_paths.append(src)

        valid_ids = []
        
        with self.db.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            
            # Base query parts
            joins = []
            where_clauses = []
            params = []
            
            # --- SOURCES FILTER ---
            source_conditions = []
            if web_domains:
                domain_placeholders = ','.join('?' for _ in web_domains)
                cur.execute(f"SELECT id FROM sources WHERE domain IN ({domain_placeholders})", web_domains)
                allowed_source_ids = [row['id'] for row in cur.fetchall()]
                if allowed_source_ids:
                    src_placeholders = ','.join('?' for _ in allowed_source_ids)
                    source_conditions.append(f"a.source_id IN ({src_placeholders})")
            
            if folder_paths:
                folder_sub_conditions = []
                for path in folder_paths:
                    search_path = path.replace('\\\\', '/')
                    if not search_path.endswith('/'): search_path += '/'
                    folder_sub_conditions.append("REPLACE(a.local_path, '\\\\', '/') LIKE ?")
                    params.append(search_path + "%")
                if folder_sub_conditions:
                    source_conditions.append(f"({' OR '.join(folder_sub_conditions)})")
                    
            if source_conditions:
                where_clauses.append(f"({' OR '.join(source_conditions)})")
            else:
                where_clauses.append("1=0") # No valid sources selected

            # --- TAGS FILTER ---
            tags = getattr(self, 'search_tags', [])
            if tags:
                # We need assets that have ALL selected tags
                # Join with asset_tags and tags, group by asset_id and count
                joins.append("JOIN asset_tags at ON a.id = at.asset_id")
                joins.append("JOIN tags t ON at.tag_id = t.id")
                
                tag_placeholders = ','.join('?' for _ in tags)
                where_clauses.append(f"t.name IN ({tag_placeholders})")
                params.extend(tags)
                
                # Build the query with GROUP BY and HAVING
                where_sql = " AND ".join(where_clauses)
                query = f"SELECT a.id FROM assets a {' '.join(joins)} WHERE {where_sql} GROUP BY a.id HAVING COUNT(DISTINCT t.name) = ?"
                params.append(len(tags))
            else:
                # Simple query without tags
                where_sql = " AND ".join(where_clauses)
                query = f"SELECT a.id FROM assets a WHERE {where_sql}"

            cur.execute(query, params)
            valid_ids = [row['id'] for row in cur.fetchall()]

        if not valid_ids:
            self.status_label.setText("Ничего не найдено по выбранным фильтрам (источники/теги).")
            self.gallery_model.setAssets([])
            return

        # 2. FAISS Vector Search with pre-filtering
        k = min(500, len(valid_ids))
        distances, ids = self.faiss_mgr.search(vector, k=k, valid_ids=valid_ids)

        import math
        max_distance = 2.0 * math.exp(-5.3 * self.search_threshold)

        results = [(dist, int(aid)) for dist, aid in zip(distances, ids) if aid > 0 and dist <= max_distance]
        
        if not results:
            self.status_label.setText("Не найдено визуально похожих изображений (уменьшите строгость поиска).")
            self.gallery_model.setAssets([])
            return

        # 3. Fetch full asset data for the results
        final_asset_ids = [aid for _, aid in results]
        
        with self.db.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            placeholders = ','.join('?' for _ in final_asset_ids)
            cur.execute(f"SELECT * FROM assets WHERE id IN ({placeholders})", final_asset_ids)
            rows = {row['id']: dict(row) for row in cur.fetchall()}

        search_assets = []
        for dist, aid in results:
            if aid in rows:
                row = rows[aid]
                search_assets.append(Asset(
                    id=row['id'], original_url=row['original_url'],
                    thumbnail_path=row['thumbnail_path'], phash=row['phash'],
                    width=row['width'], height=row['height'],
                    category=row.get('category', '3d_render'),
                    image_type=row.get('image_type', 'Photography'),
                    local_path=row.get('local_path', ''),
                    is_favorite=bool(row.get('is_favorite', 0))
                ))

        self.gallery_model.setAssets(search_assets)
        self.status_label.setText(f"Найдено {len(search_assets)} изображений")
        self.tabs.setCurrentIndex(0)
"""

# Extract the old _search_vectors to replace
# Find def _search_vectors... and the next def
start_idx = content.find("    def _search_vectors(self")
next_def_idx = content.find("    def ", start_idx + 10)

if start_idx != -1 and next_def_idx != -1:
    content = content[:start_idx] + new_search_vectors.lstrip() + "\n" + content[next_def_idx:]
elif start_idx != -1:
    content = content[:start_idx] + new_search_vectors.lstrip() + "\n"

with open('ui/main_window.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Patched _search_vectors in main_window.py")
