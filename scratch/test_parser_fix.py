import logging
import json
import sqlite3
from pathlib import Path
from scrapers.archdaily_parser import ArchDailyParser

logging.basicConfig(level=logging.INFO)

def on_image(data):
    print(f"Captured: {data['url'][:60]}... | Category: {data.get('category')} | Tags: {len(data.get('tags', []))}")
    return False # Stop after first image

def test_parser():
    url = "https://www.archdaily.com/1035414/jinsha-winery-cultural-tourism-complex-hypersity-architects"
    print(f"Testing parser with URL: {url}")
    
    parser = ArchDailyParser(url, on_image_found=on_image)
    parser.run()

if __name__ == "__main__":
    test_parser()
