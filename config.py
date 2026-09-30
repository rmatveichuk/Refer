import os
import sys
import ctypes
from pathlib import Path

VERSION = "1.1.0"
APP_NAME = "Refer"
OLD_APP_NAME = "ReferAssetManager"

# --- Platform Specific Data Directories ---

def get_resource_path(relative_path: str) -> str:
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def get_app_roaming_dir() -> Path:
    r"""Returns the %AppData%\[APP_NAME]\ directory for DB and settings."""
    app_name = APP_NAME
    if sys.platform == "win32":
        # %APPDATA%
        app_data = os.environ.get('APPDATA')
        if not app_data:
            app_data = os.path.join(os.path.expanduser('~'), 'AppData', 'Roaming')
        base_dir = Path(app_data) / app_name
    elif sys.platform == "darwin":
        base_dir = Path.home() / 'Library' / 'Application Support' / app_name
    else:
        base_dir = Path.home() / '.config' / app_name
    return base_dir

def get_app_local_dir() -> Path:
    r"""Returns the %LocalAppData%\[APP_NAME]\ directory for heavy models and cache."""
    app_name = APP_NAME
    if sys.platform == "win32":
        # %LOCALAPPDATA%
        local_data = os.environ.get('LOCALAPPDATA')
        if not local_data:
            local_data = os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
        base_dir = Path(local_data) / app_name
    elif sys.platform == "darwin":
        base_dir = Path.home() / 'Library' / 'Caches' / app_name
    else:
        base_dir = Path.home() / '.local' / 'share' / app_name
    return base_dir

def get_short_path(path_str: str) -> str:
    """
    On Windows, C++ libraries (like FAISS) sometimes fail if the path contains Cyrillic/Unicode characters.
    This function returns the short 8.3 path.
    """
    if sys.platform != "win32":
        return path_str
        
    try:
        buffer_size = 256
        buffer = ctypes.create_unicode_buffer(buffer_size)
        get_short_path_name = ctypes.windll.kernel32.GetShortPathNameW
        result = get_short_path_name(path_str, buffer, buffer_size)
        if result > 0:
            return buffer.value
    except Exception:
        pass
    return path_str

# Base Paths
BASE_DIR = Path(__file__).parent.absolute()
APP_ROAMING_DIR = get_app_roaming_dir()
APP_LOCAL_DIR = get_app_local_dir()

# Database & FAISS (%AppData%\Refer\Database)
DB_DIR = APP_ROAMING_DIR / "Database"
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "collection.db"

# FAISS index (using short path to guarantee C++ compatibility)
_faiss_raw_path = str(DB_DIR / "collection.index")
FAISS_PATH = Path(get_short_path(_faiss_raw_path))

# AI Models (%LocalAppData%\Refer\Models)
MODELS_DIR = APP_LOCAL_DIR / "Models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

# Thumbnails (%LocalAppData%\Refer\Thumbnails)
THUMBNAILS_DIR = APP_LOCAL_DIR / "Thumbnails"
THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)

# AI Settings
SIGLIP_MODEL = "google/siglip2-so400m-patch14-384"
VECTOR_DIMENSION = 1152  # 1152 for siglip2-so400m

# Hardware / Performance Constants
THUMBNAIL_SIZE = 1024 # px
BATCH_LOAD_SIZE = 50  # For Lazy Loading limits

# --- Localization ---
CURRENT_LANGUAGE = "ru" # "ru" or "en"

# Базовый словарь тегов для авто-тегирования
TAG_VOCABULARY = {
    "Material": [
        "concrete", "wood", "brick", "glass", "metal", 
        "stone", "plaster", "marble", "fabric"
    ],
    "Lighting": [
        "natural light", "artificial light", "warm light", 
        "cold light", "sunny", "overcast", "shadows", "neon"
    ],
    "Style": [
        "minimalism", "industrial", "classic", "modern", 
        "brutalism", "futuristic", "cozy", "luxurious"
    ],
    "Type": [
        "exterior", "interior", "residential", "commercial", 
        "public space", "landscape", "close-up", "furniture",
        "living room", "bedroom", "kitchen", "bathroom", "dining room", 
        "kids room", "study", "home office", "hallway", "corridor", 
        "closet", "wardrobe", "balcony", "terrace", "studio apartment", "studio",
        "office", "coworking", "lobby", "reception", "restaurant", "cafe", 
        "hotel room", "conference room", "meeting room", "retail", "shop", 
        "showroom", "gym", "fitness", "spa", "wellness", "library", 
        "gallery", "museum", "photography"
    ]
}
