import logging
import time
import re
import sqlite3
from typing import Callable, Dict, Any, Optional
from curl_cffi import requests
from pathlib import Path

logger = logging.getLogger(__name__)


class BehanceParser:
    """
    Универсальный парсер Behance (облегченная версия).

    Работает только по URL:
    1. Профили пользователей (https://www.behance.net/username)
    2. Конкретные проекты (https://www.behance.net/gallery/12345/...)
    """

    # Приоритет размеров (от лучшего к худшему)
    SIZE_PRIORITY = [
        "source",
        "fs",
        "max_3840",
        "max_1920",
        "hd",
        "max_1200",
        "max_1200_webp",
    ]

    def __init__(
        self,
        start_url: str,
        on_image_found: Callable[[Dict[str, Any]], bool],
        db_path: str = None,
        **kwargs,
    ):
        # Для поиска сохраняем параметры (?field=...), для остального обрезаем
        if "/search/" in start_url:
            self.start_url = start_url.rstrip("/")
        else:
            self.start_url = start_url.split("?")[0].rstrip("/")
        
        self.on_image_found = on_image_found
        self.db_path = db_path
        self._is_cancelled = False

        # Определяем что мы парсим: проект или профиль
        self.is_project = "/gallery/" in self.start_url

    def cancel(self):
        """Signals the parser to stop."""
        self._is_cancelled = True
        logger.info("⛔ Cancellation requested - stopping scrape...")

    def _upgrade_to_best_quality(self, img_url: str) -> str:
        """
        Берёт любую ссылку CDN Behance и пробует подставить /source/ (оригинал).
        """
        source_url = re.sub(
            r"/project_modules/[^/]+/", "/project_modules/source/", img_url
        )
        source_url = source_url.split("?")[0]
        if source_url.endswith("_webp.webp"):
            source_url = source_url.replace("_webp.webp", ".webp")

        try:
            head_res = requests.head(source_url, impersonate="chrome110", timeout=3)
            if head_res.status_code == 200:
                return source_url
        except Exception:
            pass

        for size in self.SIZE_PRIORITY[1:]:
            upgraded = re.sub(
                r"/project_modules/[^/]+/", f"/project_modules/{size}/", img_url
            )
            upgraded = upgraded.split("?")[0]
            if upgraded.endswith("_webp.webp"):
                upgraded = upgraded.replace("_webp.webp", ".webp")
            try:
                head_res = requests.head(upgraded, impersonate="chrome110", timeout=3)
                if head_res.status_code == 200:
                    return upgraded
            except Exception:
                continue

        return img_url.split("?")[0]

    def run(self):
        if self.is_project:
            logger.info(f"Detected project URL - scraping project: {self.start_url}")
            # Извлекаем ID проекта из URL
            match = re.search(r"/gallery/(\d+)", self.start_url)
            if match:
                self._fetch_project_images(match.group(1))
            else:
                logger.error("Could not extract project ID from URL.")
        elif "/search/" in self.start_url:
            logger.info(f"Detected search URL - scraping results: {self.start_url}")
            self._scrape_profile("search_results")
        else:
            username = self.start_url.split("/")[-1]
            logger.info(f"Detected profile URL - scraping user: {username}")
            self._scrape_profile(username)

    def _fetch_project_images(self, project_id: str, author: str = "unknown"):
        """Скачивает все оригинальные изображения из конкретного проекта."""
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

        headers = {
            "User-Agent": "RenderVaultBot/1.0 (contact: test@example.com) Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml,application/xml",
        }

        try:
            proj_url = f"https://www.behance.net/gallery/{project_id}/project"
            proj_res = requests.get(
                proj_url, impersonate="chrome110", headers=headers, timeout=15
            )

            if proj_res.status_code != 200:
                logger.warning(
                    f"Project {project_id} returned status {proj_res.status_code}"
                )
                return

            # Извлекаем заголовок проекта
            title_match = re.search(r"<title>(.*?)</title>", proj_res.text)
            title = (
                title_match.group(1).split(" on Behance")[0].strip()
                if title_match
                else f"Project {project_id}"
            )

            # Находим ВСЕ ссылки на CDN Behance с картинками проекта
            all_cdn_urls = re.findall(
                r'(https://mir-s3-cdn-cf\.behance\.net/project_modules/[^"\'\s,]+\.(?:jpg|jpeg|png|webp))',
                proj_res.text,
            )

            # Дедупликация
            unique_files: Dict[str, str] = {}
            for url in all_cdn_urls:
                filename = url.split("/")[-1].split("?")[0]
                base_name = filename.rsplit(".", 1)[0]
                if base_name not in unique_files:
                    unique_files[base_name] = url

            if not unique_files:
                logger.warning(f"No images found in project {project_id}.")
                return

            logger.info(f"Project '{title}': {len(unique_files)} unique images found.")

            for base_name, raw_url in unique_files.items():
                if self._is_cancelled:
                    return

                best_url = self._upgrade_to_best_quality(raw_url)

                # Проверяем не был ли URL удалён ранее
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
                    except:
                        pass

                asset_data = {
                    "url": best_url,
                    "domain": "behance.net",
                    "project_id": str(project_id),
                    "project_title": title,
                    "author": author,
                }

                if self.on_image_found(asset_data) is False:
                    # Это может быть либо лимит проекта, либо отмена.
                    # В обоих случаях прекращаем обработку ТЕКУЩЕГО проекта.
                    return

        except Exception as e:
            logger.error(f"Failed to fetch project {project_id}: {e}")

    def _scrape_profile(self, username: str):
        """Парсит профиль пользователя."""
        headers = {
            "User-Agent": "RenderVaultBot/1.0 (contact: test@example.com) Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml,application/xml",
        }

        try:
            res = requests.get(
                self.start_url, impersonate="chrome110", headers=headers, timeout=10
            )
            if res.status_code != 200:
                logger.error(f"Behance profile load failed: {res.status_code}")
                return

            # Извлекаем ID проектов из экранированного JSON-состояния страницы
            project_ids = []
            matches = re.finditer(
                r"behance\.net(?:\\/|/+)gallery(?:\\/|/+)(\d+)", res.text
            )
            for m in matches:
                pid = m.group(1)
                if pid not in project_ids:
                    project_ids.append(pid)

            logger.info(f"Found {len(project_ids)} projects for {username}.")

            if not project_ids:
                logger.warning(
                    "No projects found. Profile might be empty or require auth."
                )
                return

            for idx, pid in enumerate(project_ids):
                if self._is_cancelled:
                    logger.info("⛔ Scraping cancelled by user")
                    return

                logger.info(
                    f"Fetching Behance project {idx + 1}/{len(project_ids)} (ID: {pid})"
                )
                time.sleep(1)  # Этичная задержка
                self._fetch_project_images(pid, author=username)

            logger.info(
                f"Finished scraping {len(project_ids)} projects for {username}."
            )

        except Exception as e:
            logger.error(f"Behance profile parser crashed: {e}")
