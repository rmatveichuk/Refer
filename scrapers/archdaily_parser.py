import logging
import json
import subprocess
import time
import re
import sqlite3
from typing import Callable, Dict, Any

logger = logging.getLogger(__name__)

class ArchDailyParser:
    """
    Parser for ArchDaily.com using browser-act CLI for robust data ingestion.
    Supports high-quality architectural photography extraction.
    """

    def __init__(self, start_url: str, on_image_found: Callable[[Dict[str, Any]], bool], db_path: str = None):
        self.start_url = start_url.split("?")[0] if "/search/" not in start_url else start_url
        self.on_image_found = on_image_found
        self.db_path = db_path
        self._is_cancelled = False
        self.session_name = f"archdaily_{int(time.time())}"
        
        # Check if project or search
        self.is_project = "/search/" not in self.start_url

    def cancel(self):
        self._is_cancelled = True
        logger.info("⛔ Cancellation requested - stopping ArchDaily data ingestion...")

    def _run_browser_cmd(self, *args) -> str:
        cmd = ["browser-act", "--session", self.session_name] + list(args)
        # Using utf-8 explicitly since ArchDaily contains many unicode titles
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        if result.returncode != 0:
            logger.error(f"browser-act error (cmd={cmd[2:4]}): {result.stderr}")
        return result.stdout.strip()
        
    def run(self):
        logger.info(f"Creating browser-act session: {self.session_name}")
        create_out = self._run_browser_cmd("browser", "create", self.session_name, "--desc", "ArchDaily Connector")
        b_match = re.search(r"browser_id:\s*(\d+)", create_out)
        self.browser_id = b_match.group(1) if b_match else None

        try:
            if self.browser_id:
                # Open browser directly to start_url to avoid about:blank navigation error
                self._run_browser_cmd("browser", "open", self.browser_id, self.start_url)
                self._run_browser_cmd("wait", "stable")

            if self.is_project:
                logger.info(f"Detected ArchDaily project URL: {self.start_url}")
                self._fetch_project_images(self.start_url)
            else:
                logger.info(f"Detected ArchDaily search URL: {self.start_url}")
                self._scrape_search_results(self.start_url)
        finally:
            self._run_browser_cmd("browser", "close")
            if hasattr(self, 'browser_id') and self.browser_id:
                subprocess.run(["browser-act", "browser", "delete", self.browser_id], capture_output=True)

    def _scrape_search_results(self, start_url: str):
        # We start looking through pages
        base_url = start_url.split("?")[0]
        # if user passed a page, respect it, else 1
        page_match = re.search(r"page=(\d+)", start_url)
        page = int(page_match.group(1)) if page_match else 1
        
        while not self._is_cancelled:
            page_url = f"{base_url}?page={page}"
            logger.info(f"Loading search page: {page_url}")
            
            self._run_browser_cmd("navigate", page_url)
            self._run_browser_cmd("wait", "stable")
            
            js_script = """
            JSON.stringify(Array.from(document.querySelectorAll('a')).filter(a => /archdaily\\.com\\/\\d+\\//.test(a.href)).map(a => a.href).filter((v,i,a) => a.indexOf(v) === i))
            """
            out = self._run_browser_cmd("eval", js_script)
            try:
                project_urls = json.loads(out)
            except Exception as e:
                logger.error(f"Failed to parse project URLs on page {page}. End of pages or error.")
                break
                
            if not project_urls:
                logger.info("No more projects found.")
                break
                
            logger.info(f"Found {len(project_urls)} projects on page {page}.")
            for p_url in project_urls:
                if self._is_cancelled:
                    return
                # Remove tracking queries
                p_url = p_url.split("?")[0]
                self._fetch_project_images(p_url)
                
            page += 1

    def _fetch_project_images(self, project_url: str):
        if self._is_cancelled:
            return
            
        match = re.search(r"archdaily\.com/(\d+)", project_url)
        if not match:
            logger.warning(f"Could not extract project ID from {project_url}")
            return
            
        project_id = match.group(1)
        
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
            except Exception as e:
                pass
                
        logger.info(f"Scraping project: {project_url}")
        self._run_browser_cmd("navigate", project_url)
        self._run_browser_cmd("wait", "stable")
        self._run_browser_cmd("wait", "3000")
        
        js_extract = r'''
        (function() {
            var res = {title: document.querySelector('h1')?.textContent.trim() || '', ldJson: [], author: "Unknown Architect", location: "", categories: [], tags: []};
            document.querySelectorAll('script[type$=json]').forEach(s => { try { res.ldJson.push(JSON.parse(s.textContent)); } catch(e) {} });
            res.ldJson.forEach(item => {
                if (item.contentLocation?.address) {
                    var a = item.contentLocation.address;
                    res.location = [a.addressLocality, a.addressCountry].filter(Boolean).join(", ");
                }
                if (item.headline?.includes(" / ")) res.author = item.headline.split(" / ")[1].trim();
                if (!res.title && item.headline) res.title = item.headline.split(" / ")[0].trim();
                var k = item.keywords;
                if (k) res.tags = res.tags.concat(typeof k === 'string' ? k.split(',').map(s => s.strip()) : k);
                if (item.genre) res.categories.push(item.genre);
                if (item.articleSection) res.categories = res.categories.concat(item.articleSection);
            });
            document.querySelectorAll('.afd-tags__btn').forEach(b => {
                var t = b.textContent.trim();
                if (t) {
                    res.tags.push(t);
                    if (b.href?.includes('/categories/')) res.categories.push(t);
                }
            });
            res.tags = [...new Set(res.tags)].filter(Boolean);
            res.categories = [...new Set(res.categories)].filter(Boolean);
            res.uniqueImages = [];
            var seen = {};
            document.querySelectorAll('img').forEach(img => {
                var src = img.src || '';
                if (!src.includes('adsttc.com') || src.includes('logo') || src.includes('loader')) return;
                var base = src.replace(/\/(newsletter|medium_jpg|large_jpg|thumb|slideshow)\//g, '/SIZE/').split('?')[0];
                if (!seen[base]) {
                    seen[base] = true;
                    var hr = src.replace(/\/(newsletter|thumb_jpg|slideshow|medium_jpg)\//, '/large_jpg/');
                    res.uniqueImages.push({src: hr, alt: img.alt || ''});
                }
            });
            return JSON.stringify(res);
        })();
        '''
        
        # Robust JS cleaning: strip each line and join with single space
        js_clean = " ".join([l.strip() for l in js_extract.splitlines() if l.strip()])
        out = self._run_browser_cmd("eval", js_clean)
        
        # Parse output safely
        if out is None or out == "(no return value)" or not out.strip():
            logger.error(f"Failed to parse project data for {project_id}. Empty or null output from eval.")
            return

        try:
            if '{' in out:
                out = out[out.find('{'):]
            if '}' in out:
                out = out[:out.rfind('}')+1]
            data = json.loads(out)
        except Exception as e:
            logger.error(f"Failed to parse project data for {project_id}. Out (first 200 chars): {out[:200]}")
            return
            
        images = data.get("uniqueImages", [])
        if not images:
            logger.warning(f"No images found for project {project_id}")
            return
            
        # Filter drawings and plans
        photo_images = []
        skip_keywords = ["plan", "section", "elevation", "detail", "diagram", "axonometric", "sketch"]
        
        for img in images:
            alt_lower = img.get("alt", "").lower()
            is_drawing = False
            for kw in skip_keywords:
                if kw in alt_lower:
                    is_drawing = True
                    break
                    
            if not is_drawing:
                photo_images.append(img)
                
        logger.info(f"Found {len(photo_images)} photos (after filtering out drawings) in project {project_id}")
        
        for img in photo_images:
            if self._is_cancelled:
                return
                
            cats = data.get("categories", [])
            asset_data = {
                "url": img["src"],
                "domain": "archdaily.com",
                "project_id": project_id,
                "project_title": data.get("title", f"Project {project_id}"),
                "author": data.get("author"),
                "location": data.get("location"),
                "image_type": "Photography",
                "category": cats[0] if cats else None,
                "tags": data.get("tags", [])
            }
            
            # The manager checks limits (_max_images_per_project). 
            # If function returns False, it means limit reached (or cancelled)
            cont = self.on_image_found(asset_data)
            if cont is False:
                return
