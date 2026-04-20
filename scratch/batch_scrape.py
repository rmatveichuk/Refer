import sys
from pathlib import Path
import logging
import time

# Add root to path
sys.path.append(str(Path(__file__).parent.parent.absolute()))

from scrapers.manager import ScraperManager
from scrapers.behance_parser import BehanceParser
from database.db_manager import DatabaseManager
import config

logging.basicConfig(level=logging.INFO)

urls = [
    "https://www.behance.net/gallery/239121311/Tadao-Ando-Pringiers-House",
    "https://www.behance.net/gallery/239437121/MIGUELETES-(Residential-Building-Visualization)",
    "https://www.behance.net/gallery/245891571/Modern-Woodland-House",
    "https://www.behance.net/gallery/240717867/CUERPO-ARQUITECTONICO-(Interactive-archvis)",
    "https://www.behance.net/gallery/246879721/The-noise-of-silence",
    "https://www.behance.net/gallery/239672677/Seregina-5",
    "https://www.behance.net/gallery/247043675/Ho-Chi-Minh-City-Creative-Arts-Office-Tower",
    "https://www.behance.net/gallery/246883231/Shatt-Al-Arab-House",
    "https://www.behance.net/gallery/246689481/Jardi-dAlzina",
    "https://www.behance.net/gallery/246555985/Museum-OAPAA-SPECTACLE"
]

db = DatabaseManager(config.DB_PATH)

def main():
    print(f"Starting batch scrape of {len(urls)} projects...")
    
    for i, url in enumerate(urls):
        print(f"[{i+1}/{len(urls)}] Scraping: {url}")
        # Run synchronous for script
        scraper_task = ScraperManager(BehanceParser, url, db, category="3d_render")
        scraper_task.run() 
        time.sleep(2) 

    print("Batch scrape completed!")

if __name__ == "__main__":
    main()
