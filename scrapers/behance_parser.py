import logging
import json
import re
import time
import sqlite3
import urllib.parse
from typing import Callable, Dict, Any, Optional
from curl_cffi import requests
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

class BehanceHTMLParser(HTMLParser):
    """
    Легковесный HTML-парсер для извлечения тега <script id="beconfig-store_state">.
    """
    def __init__(self):
        super().__init__()
        self.in_script = False
        self.script_attrs = {}
        self.state_content = None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            attrs_dict = dict(attrs)
            if attrs_dict.get("id") == "beconfig-store_state":
                self.in_script = True
                self.script_attrs = attrs_dict

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_script = False

    def handle_data(self, data):
        if self.in_script:
            self.state_content = data


class BehanceParser:
    """
    Полноценный универсальный загрузчик Behance.
    
    Поддерживает:
    1. Конкретные проекты (https://www.behance.net/gallery/12345/...)
    2. Профили пользователей (https://www.behance.net/username)
    3. Страницы поиска (https://www.behance.net/search/projects?search=...)
    4. Текстовые поисковые запросы (автоматическая конвертация)
    """

    def __init__(
        self,
        start_url: str,
        on_image_found: Callable[[Dict[str, Any]], bool],
        db_path: str = None,
        **kwargs,
    ):
        self.on_image_found = on_image_found
        self.db_path = db_path
        self._is_cancelled = False
        
        # Настраиваем сессию и заголовки для обхода блокировок
        self.session = requests.Session()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
        }

        # Определяем, является ли ввод поисковым запросом или готовым URL
        start_url = start_url.strip()
        if not (start_url.startswith("http://") or start_url.startswith("https://")):
            query_encoded = urllib.parse.quote(start_url)
            self.start_url = f"https://www.behance.net/search/projects?search={query_encoded}"
            logger.info(f"Converted search query '{start_url}' to URL: {self.start_url}")
        else:
            self.start_url = start_url

        # Очищаем URL от лишних параметров
        if "/search" in self.start_url:
            self.start_url = self.start_url.rstrip("/")
        else:
            self.start_url = self.start_url.split("?")[0].rstrip("/")

        # Определяем тип парсинга
        self.is_project = "/gallery/" in self.start_url

    def cancel(self):
        """Сигнал скраперу на остановку."""
        self._is_cancelled = True
        logger.info("⛔ Cancellation requested - stopping load...")

    def _get_page(self, url: str) -> Optional[str]:
        """
        Выполняет GET-запрос к Behance с автоматическим обходом JS cookie-челленджа.
        """
        if self._is_cancelled:
            return None

        try:
            res = self.session.get(url, impersonate="chrome110", headers=self.headers, timeout=15)
            
            # Проверка JS cookie-челленджа
            match = re.search(r'document\.cookie\s*=\s*"js_challenge_value=([^"]+)"', res.text)
            if match:
                cookie_val = match.group(1)
                logger.info("Solving Behance JS cookie challenge...")
                self.session.cookies.set("js_challenge_value", cookie_val, domain=".behance.net", path="/")
                
                # Повторный запрос с кукой
                res = self.session.get(url, impersonate="chrome110", headers=self.headers, timeout=15)

            if res.status_code == 200:
                return res.text
            else:
                logger.warning(f"Failed to fetch page {url}: Status {res.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error fetching page {url}: {e}")
            return None

    def _parse_state(self, html: str) -> Optional[dict]:
        """
        Извлекает и парсит JSON-состояние из HTML.
        """
        parser = BehanceHTMLParser()
        try:
            parser.feed(html)
        except Exception as e:
            logger.debug(f"HTMLParser parser error: {e}")
        
        state_content = parser.state_content
        if not state_content:
            # Fallback к регуляркам
            match = re.search(r'<script id="beconfig-store_state"[^>]*>(.*?)</script>', html, re.DOTALL)
            if match:
                state_content = match.group(1).strip()
            else:
                match2 = re.search(r'window\.__be_state__\s*=\s*(.*?);</script>', html)
                if match2:
                    state_content = match2.group(1).strip()

        if state_content:
            try:
                return json.loads(state_content)
            except Exception as e:
                logger.error(f"Failed to decode state JSON: {e}")
        return None

    def run(self):
        if self.is_project:
            logger.info(f"Detected project URL - loading project: {self.start_url}")
            match = re.search(r"/gallery/(\d+)", self.start_url)
            if match:
                self._scrape_single_project(match.group(1))
            else:
                logger.error("Could not extract project ID from URL.")
        elif "/search" in self.start_url:
            logger.info(f"Detected search URL - loading results: {self.start_url}")
            self._scrape_search(self.start_url)
        else:
            username = self.start_url.split("/")[-1]
            logger.info(f"Detected profile URL - loading user: {username}")
            self._scrape_profile(self.start_url, username)

    def _scrape_single_project(self, project_id: str, author: str = "unknown"):
        """Скачивает оригинальные изображения из конкретного проекта."""
        if self._is_cancelled:
            return

        if self.db_path:
            try:
                conn = sqlite3.connect(self.db_path)
                cur = conn.cursor()
                cur.execute("SELECT id FROM projects WHERE url = ?", (str(project_id),))
                if cur.fetchone():
                    logger.info(f"Project {project_id} already in DB, skipping entirely.")
                    conn.close()
                    return
                conn.close()
            except Exception as e:
                logger.warning(f"Failed to check project in DB: {e}")

        proj_url = f"https://www.behance.net/gallery/{project_id}/project"
        html = self._get_page(proj_url)
        if not html:
            return

        state = self._parse_state(html)
        if not state:
            logger.warning(f"Could not parse state for project {project_id}")
            return

        try:
            proj_data = state['project']['project']
            title = proj_data.get('name', f"Project {project_id}")
            
            # Извлекаем авторов
            owners = proj_data.get('owners', [])
            if owners:
                author = owners[0].get('username') or owners[0].get('displayName') or author
            
            # Сбор тегов из разных источников (теги, инструменты, разделы)
            tags = []
            
            # 1. Традиционные теги автора
            raw_tags = proj_data.get('tags') or []
            for tag in raw_tags:
                if isinstance(tag, dict):
                    t_name = tag.get("title") or tag.get("name")
                    if t_name:
                        tags.append(t_name)
                elif isinstance(tag, str):
                    tags.append(tag)

            # 2. Инструменты (софт)
            tools = proj_data.get('tools') or []
            for tool in tools:
                if isinstance(tool, dict):
                    t_name = tool.get("title") or tool.get("name")
                    if t_name:
                        tags.append(t_name)
                elif isinstance(tool, str):
                    tags.append(tool)

            # 3. Названия курируемых разделов (features)
            features = proj_data.get('features') or []
            for feat in features:
                if isinstance(feat, dict) and feat.get("name"):
                    tags.append(feat.get("name"))

            # Очистка и дедупликация тегов
            tags = sorted(list(set([t.strip() for t in tags if t and t.strip()])))

            # Извлекаем модули изображений
            modules = proj_data.get('modules', [])
            image_modules = [m for m in modules if m.get('__typename') == 'ImageModule']

            logger.info(f"Project '{title}': found {len(image_modules)} image modules. Tags: {tags}")

            for idx, mod in enumerate(image_modules):
                if self._is_cancelled:
                    return

                # Получаем оригинальный URL из allAvailable
                best_url = None
                img_sizes = mod.get('imageSizes', {})
                all_available = img_sizes.get('allAvailable', []) if img_sizes else []
                
                if all_available:
                    # Ищем тип "source" (оригинал)
                    source_images = [img for img in all_available if img.get('type') == 'source' or 'source' in img.get('url', '')]
                    if source_images:
                        best_url = source_images[0].get('url')
                    else:
                        # Если "source" нет, сортируем по ширине и берем самое широкое
                        sorted_sizes = sorted(all_available, key=lambda x: x.get('width', 0), reverse=True)
                        best_url = sorted_sizes[0].get('url')
                
                if not best_url:
                    best_url = mod.get('src')
                    
                if not best_url:
                    continue

                best_url = best_url.split('?')[0]

                # Проверяем не был ли этот URL удален ранее
                if self.db_path:
                    try:
                        conn = sqlite3.connect(self.db_path)
                        cur = conn.cursor()
                        cur.execute(
                            "SELECT 1 FROM deleted_assets WHERE original_url = ?",
                            (best_url,),
                        )
                        if cur.fetchone():
                            logger.debug(f"⏭️ Skipping deleted URL: {best_url[:60]}")
                            conn.close()
                            continue
                        conn.close()
                    except Exception:
                        pass

                asset_data = {
                    "url": best_url,
                    "domain": "behance.net",
                    "project_id": str(project_id),
                    "project_title": title,
                    "author": author,
                    "image_type": "Photography",
                    "tags": tags
                }

                # Передаем в callback. Если вернул False, прекращаем парсинг текущего проекта
                if self.on_image_found(asset_data) is False:
                    return

        except Exception as e:
            logger.error(f"Error parsing project {project_id}: {e}")

    def _scrape_profile(self, start_url: str, username: str):
        """Парсит профиль пользователя со всеми страницами проектов."""
        logger.info(f"Starting profile scraping for user: {username}")
        
        current_url = start_url
        projects_scraped = 0
        max_projects = 50  # Разумный лимит проектов для предотвращения бесконечной работы
        
        while not self._is_cancelled and projects_scraped < max_projects:
            logger.info(f"Fetching profile page: {current_url}")
            html = self._get_page(current_url)
            if not html:
                break
                
            state = self._parse_state(html)
            if not state:
                logger.warning(f"Could not parse state for profile URL: {current_url}")
                break
                
            try:
                work_section = state['profile']['activeSection']['work']
                profile_projects = work_section.get('profileProjects', [])
                page_info = work_section.get('user', {}).get('profileProjects', {}).get('pageInfo', {})
            except KeyError as e:
                logger.warning(f"KeyError while parsing profile state: {e}")
                break
                
            if not profile_projects:
                logger.info("No projects found on this profile page.")
                break
                
            project_ids = [str(p['id']) for p in profile_projects]
            logger.info(f"Found {len(project_ids)} projects on current profile page.")
            
            for pid in project_ids:
                if self._is_cancelled:
                    return
                if projects_scraped >= max_projects:
                    logger.info(f"Reached max projects limit ({max_projects}) for profile. Stopping.")
                    return
                    
                logger.info(f"Fetching project {projects_scraped + 1} (ID: {pid})")
                time.sleep(1)  # Задержка вежливости
                self._scrape_single_project(pid, author=username)
                projects_scraped += 1
                
            # Проверяем пагинацию
            if page_info.get('hasNextPage') and page_info.get('endCursor'):
                cursor = page_info.get('endCursor')
                current_url = start_url.split('?')[0] + f"?after={cursor}"
            else:
                logger.info("Reached end of projects (no next page).")
                break

    def _scrape_search(self, start_url: str):
        """Парсит страницу поиска проектов со всеми страницами результатов."""
        logger.info(f"Starting search scraping: {start_url}")
        
        current_url = start_url
        projects_scraped = 0
        max_projects = 50  # Разумный лимит проектов
        
        while not self._is_cancelled and projects_scraped < max_projects:
            logger.info(f"Fetching search page: {current_url}")
            html = self._get_page(current_url)
            if not html:
                break
                
            state = self._parse_state(html)
            if not state:
                logger.warning(f"Could not parse state for search URL: {current_url}")
                break
                
            try:
                search_data = state['search']['projects']['search']
                nodes = search_data.get('nodes', [])
                page_info = search_data.get('pageInfo', {})
            except KeyError as e:
                logger.warning(f"KeyError while parsing search state: {e}")
                break
                
            if not nodes:
                logger.info("No projects found on this search page.")
                break
                
            project_ids = []
            for node in nodes:
                pid = node.get('id')
                if pid:
                    project_ids.append(str(pid))
                    
            logger.info(f"Found {len(project_ids)} projects on current search page.")
            
            for pid in project_ids:
                if self._is_cancelled:
                    return
                if projects_scraped >= max_projects:
                    logger.info(f"Reached max projects limit ({max_projects}) for search. Stopping.")
                    return
                    
                logger.info(f"Fetching search result project {projects_scraped + 1} (ID: {pid})")
                time.sleep(1)  # Задержка вежливости
                self._scrape_single_project(pid)
                projects_scraped += 1
                
            # Проверяем пагинацию
            if page_info.get('hasNextPage') and page_info.get('endCursor'):
                cursor = page_info.get('endCursor')
                parsed_url = urllib.parse.urlparse(start_url)
                params = urllib.parse.parse_qs(parsed_url.query)
                params['after'] = [cursor]
                new_query = urllib.parse.urlencode(params, doseq=True)
                current_url = urllib.parse.urlunparse((
                    parsed_url.scheme,
                    parsed_url.netloc,
                    parsed_url.path,
                    parsed_url.params,
                    new_query,
                    parsed_url.fragment
                ))
            else:
                logger.info("Reached end of search results (no next page).")
                break
