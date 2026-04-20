import sys
import logging
from pathlib import Path

# Setup simple logging
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')

from database.db_manager import DatabaseManager
from scrapers.manager import ScraperManager
from scrapers.archdaily_parser import ArchDailyParser
import config

def test_archdaily():
    db = DatabaseManager(config.DB_PATH)
    
    # We will test on a single project URL
    test_url = "https://www.archdaily.com/1033119/jiuyao-kindergarten-zhubo-design"
    print(f"Testing ArchDaily scraper on: {test_url}")
    
    # In main_window.py, we do: max_images = 10 if parser_class == ArchDailyParser else 5
    manager = ScraperManager(ArchDailyParser, test_url, db, category="3d_render", max_images_per_project=10)
    
    # Normally QThreadPool runs this. We'll run it synchronously for the test
    manager.run()
    
    print("Test completed.")

if __name__ == "__main__":
    test_archdaily()
