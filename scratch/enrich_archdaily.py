import os
import sys
from pathlib import Path
import sqlite3
import subprocess
import json
import time

# Inject root project path so we can import modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.db_manager import DatabaseManager
import config

def get_archdaily_projects(db: DatabaseManager):
    """Retrieve all ArchDaily projects from the database."""
    with db.get_connection() as conn:
        cur = conn.cursor()
        # Find project IDs that have assets from 'archdaily.com'
        # We also select project.url which holds the ID on archdaily
        cur.execute('''
            SELECT DISTINCT p.id, p.url, p.title
            FROM projects p
            JOIN assets a ON a.project_id = p.id
            JOIN sources s ON a.source_id = s.id
            WHERE s.domain = 'archdaily.com' AND p.url IS NOT NULL
        ''')
        return [dict(row) for row in cur.fetchall()]

def has_tags(db: DatabaseManager, project_id: int) -> bool:
    """Check if the assets of this project already have tags."""
    # Check if any asset in this project has entries in asset_tags
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute('''
            SELECT count(at.tag_id) as cnt
            FROM asset_tags at
            JOIN assets a ON at.asset_id = a.id
            WHERE a.project_id = ?
        ''', (project_id,))
        row = cur.fetchone()
        return row['cnt'] > 0

def enrich_project(db: DatabaseManager, project: dict, session_name: str, idx: int):
    ad_id = project['url']
    # Project URL is usually archdaily.com/[id]
    project_url = f"https://www.archdaily.com/{ad_id}"
    
    safe_title = project['title'].encode('ascii', 'replace').decode('ascii')
    print(f"[*] Processing project {idx+1}: '{safe_title}' (ID: {ad_id})", flush=True)
    
    # 1. Navigate to page
    nav_cmd = ["browser-act", "--session", session_name, "navigate", project_url]
    subprocess.run(nav_cmd, capture_output=True)
    subprocess.run(["browser-act", "--session", session_name, "wait", "stable"], capture_output=True)
    
    # 2. JS extraction logic
    js_extract = r'''
    (function() {
    var result = {};
    var ldScripts = document.querySelectorAll('script[type$=json]');
    result.ldJson = [];
    ldScripts.forEach(function(s) {
        try { result.ldJson.push(JSON.parse(s.textContent)); } catch(e) {}
    });

    result.categories = [];
    result.tags = [];

    result.ldJson.forEach(function(item) {
        if (item.keywords) {
            if (typeof item.keywords === 'string') {
                var kws = item.keywords.split(',').map(function(s){ return s.trim(); });
                result.tags = result.tags.concat(kws);
            } else if (Array.isArray(item.keywords)) {
                result.tags = result.tags.concat(item.keywords);
            }
        }
        if (item.genre) {
             result.categories.push(item.genre);
        }
        if (item.articleSection) {
             if (Array.isArray(item.articleSection)) {
                  result.categories = result.categories.concat(item.articleSection);
             } else {
                  result.categories.push(item.articleSection);
             }
        }
    });

    document.querySelectorAll('.afd-tags__btn').forEach(function(btn) {
         var txt = btn.textContent.trim();
         if (txt) {
              result.tags.push(txt);
              if (btn.href && btn.href.indexOf('/categories/') > -1) {
                   result.categories.push(txt);
              }
         }
    });

    result.tags = result.tags.filter(function(item, pos) { return result.tags.indexOf(item) == pos; }).filter(Boolean);
    result.categories = result.categories.filter(function(item, pos) { return result.categories.indexOf(item) == pos; }).filter(Boolean);

    return JSON.stringify(result);
    })();
    '''
    
    eval_cmd = ["browser-act", "--session", session_name, "eval", js_extract.replace('\n', ' ')]
    result = subprocess.run(eval_cmd, capture_output=True, text=True, encoding='utf-8')
    
    if result.returncode != 0:
        print(f"[!] Evaluation failed for {ad_id}: {result.stderr}")
        return False
        
    try:
        data = json.loads(result.stdout.strip())
    except Exception as e:
        print(f"[!] Failed to parse JSON for {ad_id}: {result.stdout.strip()}")
        return False
        
    cats = data.get("categories", [])
    tags = data.get("tags", [])
    
    category = cats[0] if cats else None
    
    if not category and not tags:
        print(f"[-] No tags or categories found for {ad_id}. Moving on.")
        return False
        
    print(f"[+] Found {len(tags)} tags. Category: {category}")
    
    # 3. Update the database
    with db.get_connection() as conn:
        cur = conn.cursor()
        
        # Get all asset IDs for this project
        cur.execute("SELECT id FROM assets WHERE project_id = ?", (project['id'],))
        asset_rows = cur.fetchall()
        asset_ids = [row['id'] for row in asset_rows]
        
        if not asset_ids:
            return False
            
        # Update categories if we found one
        if category:
            cur.execute("UPDATE assets SET category = ? WHERE project_id = ?", (category, project['id']))
            
        conn.commit()
    
    # Update tags natively via db schema manager
    if tags:
        for as_id in asset_ids:
            db.add_tags_to_asset(as_id, tags)
            
    return True

def main():
    print("Starting ArchDaily enrichment script...")
    db = DatabaseManager(config.DB_PATH)
    
    projects = get_archdaily_projects(db)
    print(f"Found {len(projects)} ArchDaily projects in database.")
    
    untagged_projects = [p for p in projects if not has_tags(db, p['id'])]
    print(f"Found {len(untagged_projects)} untagged projects requiring enrichment.")
    
    if not untagged_projects:
        print("All projects appear to be tagged. Exiting.")
        return
        
    session_name = f"ad_enrich_{int(time.time())}"
    print(f"Creating browser session: {session_name}")
    
    create_out = subprocess.run(["browser-act", "browser", "create", session_name, "--desc", "Enrichment"], capture_output=True, text=True)
    import re
    b_match = re.search(r"browser_id:\s*(\d+)", create_out.stdout)
    browser_id = b_match.group(1) if b_match else None
    
    if not browser_id:
        print("Failed to start browser-act session.")
        return
        
    try:
        # Avoid about:blank hang
        subprocess.run(["browser-act", "--session", session_name, "browser", "open", browser_id, "https://www.archdaily.com"])
        subprocess.run(["browser-act", "--session", session_name, "wait", "stable"])
        
        for idx, project in enumerate(untagged_projects):
            success = enrich_project(db, project, session_name, idx)
            
            # Simple rate limit for politeness to avoiding ip bans
            time.sleep(1.5)
            
    except KeyboardInterrupt:
        print("\nInterrupted by user. Cleaning up...")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        print("Closing browser-act...")
        subprocess.run(["browser-act", "--session", session_name, "browser", "close"])
        subprocess.run(["browser-act", "browser", "delete", browser_id])
        subprocess.run(["browser-act", "session", "close", session_name])
        print("Done.")

if __name__ == "__main__":
    main()
