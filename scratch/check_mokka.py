import re
import logging
from curl_cffi import requests

url = "https://www.behance.net/Mokka"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
}

try:
    print(f"Checking profile: {url}")
    res = requests.get(url, impersonate="chrome110", headers=headers, timeout=10)
    
    # Регулярка для поиска всех ID галерей на странице
    project_ids = []
    # Ищем все вхождения /gallery/ID
    matches = re.finditer(r"behance\.net(?:\\/|/+)gallery(?:\\/|/+)(\d+)", res.text)
    
    for m in matches:
        pid = m.group(1)
        if pid not in project_ids:
            project_ids.append(pid)
            
    print(f"DONE! Parser found {len(project_ids)} unique projects on the main page.")
    if project_ids:
        print(f"Projects IDs found: {', '.join(project_ids[:5])}...")
        
except Exception as e:
    print(f"Error: {e}")
