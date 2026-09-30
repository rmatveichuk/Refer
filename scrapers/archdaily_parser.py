import logging
import json
import subprocess
import time
import re
import html
import urllib.request
import sqlite3
import os
from typing import Callable, Dict, Any, List, Optional

from scrapers.cdn_resolver import resolve_master_url
from scrapers.plan_filter import is_drawing_by_text, is_drawing_by_header

logger = logging.getLogger(__name__)

# Minimum allowed project year (2021–2026 curated window)
MIN_PROJECT_YEAR = 2021
MAX_PROJECT_YEAR = 2026

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


class ArchDailyParser:
    """
    Parser for ArchDaily.com.
    Supports single projects, studio office pages, and search results.
    Incorporates:
      - cXenseParse metadata extraction (exact photographers, offices, materials, year)
      - Date filtering (2021-2026)
      - Two-tier architectural plan/drawing filtering (Tier 0 text + Tier 1 Range-GET header)
      - High-res CDN resolution (large_jpg 2000px)
    """

    def __init__(
        self,
        start_url: str,
        on_image_found: Callable[[Dict[str, Any]], bool],
        db_path: Optional[str] = None,
        min_year: int = MIN_PROJECT_YEAR,
        max_year: int = MAX_PROJECT_YEAR,
    ):
        self.start_url = start_url.strip()
        self.on_image_found = on_image_found
        self.db_path = db_path
        self.min_year = min_year
        self.max_year = max_year
        self._is_cancelled = False
        self.session_name = f"archdaily_{int(time.time())}"

        # Detect URL mode
        self.is_single_project = bool(re.search(r"archdaily\.com/(\d+)", self.start_url))
        self.is_office = bool(re.search(r"archdaily\.com/office/", self.start_url))

    def cancel(self):
        self._is_cancelled = True
        logger.info("⛔ Cancellation requested - stopping ArchDaily data ingestion...")

    def run(self):
        logger.info(f"Starting ArchDaily parser on: {self.start_url}")

        if self.is_single_project:
            logger.info("Detected single project URL.")
            self._fetch_project_images(self.start_url)
        elif self.is_office:
            logger.info("Detected office/studio page URL.")
            self._scrape_office_projects(self.start_url)
        else:
            logger.info("Detected search or category URL.")
            self._scrape_search_results(self.start_url)

    def _scrape_office_projects(self, office_url: str):
        """Scrapes projects published by a specific architecture bureau."""
        clean_url = office_url.split("?")[0]
        logger.info(f"Extracting projects for bureau from {clean_url}...")

        # 1. First attempt: lightweight stealth-extract via browser-act
        project_urls = self._extract_projects_via_stealth(clean_url)
        
        # 2. Fallback: direct HTTP fetch
        if not project_urls:
            project_urls = self._extract_projects_via_http(clean_url)

        if not project_urls:
            logger.warning(f"No projects found for office: {clean_url}")
            return

        logger.info(f"Discovered {len(project_urls)} projects for office {clean_url}")
        for p_url in project_urls:
            if self._is_cancelled:
                return
            self._fetch_project_images(p_url)

    def _extract_projects_via_stealth(self, url: str) -> List[str]:
        """Extract project links using browser-act stealth-extract."""
        try:
            cflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            res = subprocess.run(
                ["browser-act", "stealth-extract", url],
                capture_output=True,
                text=True,
                encoding="utf-8",
                creationflags=cflags,
                timeout=60,
            )
            if res.returncode == 0 and res.stdout:
                matches = re.finditer(r"(https://www\.archdaily\.com/\d+/[a-zA-Z0-9_-]+)", res.stdout)
                seen = set()
                urls = []
                for m in matches:
                    u = m.group(1).split("?")[0]
                    if u not in seen:
                        seen.add(u)
                        urls.append(u)
                return urls
        except Exception as e:
            logger.debug(f"stealth-extract failed for {url}: {e}")
        return []

    def _extract_projects_via_http(self, url: str) -> List[str]:
        """Fallback to direct HTTP regex link extraction."""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as resp:
                content = resp.read().decode("utf-8", errors="ignore")
                matches = re.findall(r"href=[\"'](https://www\.archdaily\.com/\d+/[^\"']+)[\"']", content)
                seen = set()
                urls = []
                for u in matches:
                    clean = u.split("?")[0]
                    if clean not in seen:
                        seen.add(clean)
                        urls.append(clean)
                return urls
        except Exception as e:
            logger.debug(f"HTTP link extraction failed for {url}: {e}")
        return []

    def _scrape_search_results(self, start_url: str):
        """Scrapes projects across paginated search results."""
        base_url = start_url.split("?")[0]
        page_match = re.search(r"page=(\d+)", start_url)
        page = int(page_match.group(1)) if page_match else 1

        while not self._is_cancelled:
            page_url = f"{base_url}?page={page}"
            logger.info(f"Loading search page: {page_url}")

            project_urls = self._extract_projects_via_stealth(page_url)
            if not project_urls:
                project_urls = self._extract_projects_via_http(page_url)

            if not project_urls:
                logger.info(f"No more projects found on page {page}.")
                break

            logger.info(f"Found {len(project_urls)} projects on page {page}.")
            for p_url in project_urls:
                if self._is_cancelled:
                    return
                self._fetch_project_images(p_url)

            page += 1

    def _fetch_project_html(self, project_url: str) -> Optional[str]:
        """Fetches project HTML with fast HTTP, falling back to stealth-extract."""
        req = urllib.request.Request(
            project_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://www.archdaily.com/",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            logger.debug(f"Direct HTTP fetch failed for {project_url}: {e}. Retrying via stealth-extract...")

        # Fallback to stealth-extract if HTTP fails
        try:
            cflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            res = subprocess.run(
                ["browser-act", "stealth-extract", project_url],
                capture_output=True,
                text=True,
                encoding="utf-8",
                creationflags=cflags,
                timeout=45,
            )
            if res.returncode == 0:
                return res.stdout
        except Exception as e:
            logger.error(f"Fallback stealth-extract also failed for {project_url}: {e}")

        return None

    def _extract_project_metadata(self, raw_html: str, project_url: str) -> Dict[str, Any]:
        """
        Extracts structured metadata using cXenseParse meta tags and JSON-LD.
        Returns:
            title, year, offices, photographers, materials, location, categories, images
        """
        # 1. Title
        h1_m = re.search(r"<h1[^>]*>(.*?)</h1>", raw_html, re.DOTALL)
        title = html.unescape(re.sub(r"<[^>]+>", "", h1_m.group(1)).strip()) if h1_m else ""

        # 2. cXenseParse metadata tags
        cx_meta = {}
        for m in re.finditer(r"<meta[^>]+(?:name|property)=['\"](cXenseParse:[^'\"]+)['\"][^>]+content=['\"]([^'\"]*)['\"]|<meta[^>]+content=['\"]([^'\"]*)['\"][^>]+(?:name|property)=['\"](cXenseParse:[^'\"]+)['\"]", raw_html):
            k = m.group(1) or m.group(4)
            v = m.group(2) or m.group(3)
            k = k.replace("cXenseParse:", "").strip()
            v = html.unescape(v).strip()
            if k not in cx_meta:
                cx_meta[k] = []
            cx_meta[k].append(v)

        # Year
        year = None
        if "project-year" in cx_meta and cx_meta["project-year"]:
            try:
                year = int(cx_meta["project-year"][0])
            except ValueError:
                pass

        if not year and "publishtime" in cx_meta:
            m = re.search(r"(\d{4})", cx_meta["publishtime"][0])
            if m:
                year = int(m.group(1))

        # Specs fallback for year
        if not year:
            year_m = re.search(r"Year:\s*(?:&nbsp;)?\s*(\d{4})", raw_html, re.I)
            if year_m:
                year = int(year_m.group(1))

        # Offices (Architects)
        offices = []
        for o_str in cx_meta.get("project-office", []):
            for part in o_str.split(","):
                part = part.strip()
                if part and part not in offices:
                    offices.append(part)

        # Photographers
        photographers = []
        for p_str in cx_meta.get("project-photographer", []):
            for part in re.split(r"[,&;]", p_str):
                part = part.strip()
                if part and part not in photographers:
                    photographers.append(part)

        # Materials
        materials = []
        for m_str in cx_meta.get("project-material", []):
            for part in m_str.split(","):
                part = part.strip()
                if part and part not in materials:
                    materials.append(part)

        # Categories
        categories = []
        for cat_key in ["project-category-tier-1", "project-category-tier-2"]:
            for c_val in cx_meta.get(cat_key, []):
                if c_val and c_val not in categories:
                    categories.append(c_val)

        # Location
        location = ""
        if "project-location" in cx_meta and cx_meta["project-location"]:
            location = cx_meta["project-location"][0]

        # Images extraction (URL + alt)
        raw_images = []
        # Pattern 1: img tags with src and alt
        for m in re.finditer(r"<img[^>]+src=['\"](https://images\.adsttc\.com/media/images/[^'\"]+)['\"][^>]*alt=['\"]([^'\"]*)['\"]|<img[^>]+alt=['\"]([^'\"]*)['\"][^>]*src=['\"](https://images\.adsttc\.com/media/images/[^'\"]+)['\"]", raw_html):
            src = m.group(1) or m.group(4)
            alt = html.unescape(m.group(2) or m.group(3) or "")
            if not any(x in src for x in ["logo", "avatar", "icon", "loader", "favicon"]):
                raw_images.append((src, alt))

        # Fallback if few img tags: match adsttc image URLs directly
        if not raw_images:
            for src in set(re.findall(r"https://images\.adsttc\.com/media/images/[a-f0-9/]+/(?:large_jpg|medium_jpg|newsletter|slideshow|original_jpg)/[^\s\"'<>]+\.jpg", raw_html)):
                raw_images.append((src, ""))

        return {
            "title": title,
            "year": year,
            "offices": offices,
            "photographers": photographers,
            "materials": materials,
            "categories": categories,
            "location": location,
            "raw_images": raw_images,
        }

    def _fetch_project_images(self, project_url: str):
        if self._is_cancelled:
            return

        match = re.search(r"archdaily\.com/(\d+)", project_url)
        if not match:
            logger.warning(f"Could not extract project ID from {project_url}")
            return

        project_id = match.group(1)

        # Check existing project in database
        if self.db_path:
            try:
                conn = sqlite3.connect(self.db_path)
                cur = conn.cursor()
                cur.execute("SELECT id FROM projects WHERE url = ?", (str(project_id),))
                if cur.fetchone():
                    logger.info(f"Project {project_id} already in DB, skipping.")
                    conn.close()
                    return
                conn.close()
            except Exception:
                pass

        logger.info(f"Scraping project {project_id}: {project_url}")
        raw_html = self._fetch_project_html(project_url)
        if not raw_html:
            logger.error(f"Failed to fetch content for project {project_id}")
            return

        meta = self._extract_project_metadata(raw_html, project_url)

        # Date Filtering: strictly 2021–2026
        year = meta.get("year")
        if year is not None:
            if year < self.min_year or year > self.max_year:
                logger.info(f"Skipping project {project_id}: completion year {year} is outside target window ({self.min_year}–{self.max_year})")
                return
        else:
            logger.info(f"Project {project_id} has no explicit year; proceeding with caution.")

        raw_images = meta.get("raw_images", [])
        if not raw_images:
            logger.warning(f"No candidate images found for project {project_id}")
            return

        # Prepare rich tag list
        tags = []
        if meta["materials"]:
            tags.extend(meta["materials"])
        if meta["photographers"]:
            tags.extend([f"Photographer: {p}" for p in meta["photographers"]])
        if meta["offices"]:
            tags.extend([f"Architect: {o}" for o in meta["offices"]])
        if meta["categories"]:
            tags.extend(meta["categories"])
        if year:
            tags.append(f"Year: {year}")

        primary_author = meta["offices"][0] if meta["offices"] else "Unknown Architect"
        primary_category = meta["categories"][0] if meta["categories"] else "Architecture"

        # Image filtering cascade (Tier 0 & Tier 1)
        seen_master_urls = set()
        approved_count = 0

        for src, alt in raw_images:
            if self._is_cancelled:
                return

            master_url = resolve_master_url(src)
            if master_url in seen_master_urls:
                continue
            seen_master_urls.add(master_url)

            # Tier 0: URL / Alt text check (zero-cost)
            if is_drawing_by_text(master_url, alt):
                logger.debug(f"Tier 0 plan filter rejected: {master_url} (alt='{alt}')")
                continue

            # Tier 1: Range-GET 64 KiB header check (aspect ratio & size)
            is_dwg, reason = is_drawing_by_header(master_url, timeout=5, parse_non_range=True)
            if is_dwg:
                logger.info(f"Tier 1 header filter rejected ({reason}): {master_url}")
                continue

            asset_data = {
                "url": master_url,
                "domain": "archdaily.com",
                "project_id": project_id,
                "project_title": meta["title"] or f"Project {project_id}",
                "author": primary_author,
                "architects": meta["offices"],
                "photographers": meta["photographers"],
                "location": meta["location"],
                "image_type": "Photography",
                "category": primary_category,
                "year": year,
                "tags": tags,
            }

            approved_count += 1
            cont = self.on_image_found(asset_data)
            if cont is False:
                logger.info(f"Limit reached for project {project_id} (collected {approved_count} images).")
                return

        logger.info(f"Project {project_id} finished: ingested {approved_count} approved reference photos.")
