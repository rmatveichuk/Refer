import os
import sys
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).parent.absolute()
APP_DATA_DIR = BASE_DIR / "app_data"
THUMBNAILS_DIR = APP_DATA_DIR / "thumbnails"
DB_PATH = APP_DATA_DIR / "refer.db"

# FAISS index: use temp dir to avoid Cyrillic path issues on Windows
import tempfile
FAISS_PATH = Path(tempfile.gettempdir()) / "refer_faiss.index"

# Create necessary directories
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)

# AI Settings
SIGLIP_MODEL = "google/siglip-so400m-patch14-384"
VECTOR_DIMENSION = 1152  # Updated for so400m model

# Hardware / Performance Constants
THUMBNAIL_SIZE = 1024 # px
BATCH_LOAD_SIZE = 50  # For Lazy Loading limits
