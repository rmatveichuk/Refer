import sys

with open('ui/main_window.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_sources_filter = """
            # --- SOURCES FILTER ---
            source_conditions = []
            if web_domains:
                domain_placeholders = ','.join('?' for _ in web_domains)
                cur.execute(f"SELECT id FROM sources WHERE domain IN ({domain_placeholders})", web_domains)
                allowed_source_ids = [row['id'] for row in cur.fetchall()]
                if allowed_source_ids:
                    src_placeholders = ','.join('?' for _ in allowed_source_ids)
                    source_conditions.append(f"a.source_id IN ({src_placeholders})")
"""

new_sources_filter = """
            # --- SOURCES FILTER ---
            source_conditions = []
            if web_domains:
                domain_placeholders = ','.join('?' for _ in web_domains)
                cur.execute(f"SELECT id FROM sources WHERE domain IN ({domain_placeholders})", web_domains)
                allowed_source_ids = [row['id'] for row in cur.fetchall()]
                if allowed_source_ids:
                    src_placeholders = ','.join('?' for _ in allowed_source_ids)
                    source_conditions.append(f"a.source_id IN ({src_placeholders})")
                    params.extend(allowed_source_ids)
"""

content = content.replace(old_sources_filter, new_sources_filter)

with open('ui/main_window.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched params in _search_vectors")