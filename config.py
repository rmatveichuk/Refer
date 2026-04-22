import os
import sys
import ctypes
from pathlib import Path

# --- Platform Specific Data Directories ---
def get_app_data_dir() -> Path:
    """Returns the correct application data directory depending on the OS."""
    app_name = "ReferAssetManager"
    
    if sys.platform == "win32":
        # %LOCALAPPDATA%\ReferAssetManager
        local_app_data = os.getenv('LOCALAPPDATA')
        if not local_app_data:
            # Fallback
            local_app_data = os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
        base_dir = Path(local_app_data) / app_name
    elif sys.platform == "darwin":
        # ~/Library/Application Support/ReferAssetManager
        base_dir = Path.home() / 'Library' / 'Application Support' / app_name
    else:
        # ~/.local/share/ReferAssetManager (Linux)
        base_dir = Path.home() / '.local' / 'share' / app_name
        
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir

def get_short_path(path_str: str) -> str:
    """
    On Windows, C++ libraries (like FAISS) sometimes fail if the path contains Cyrillic/Unicode characters.
    This function returns the short 8.3 path (e.g., C:\\Users\\ПРИВЕТ~1\\...) which is strictly ASCII.
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
APP_DATA_DIR = get_app_data_dir()

# User Data
THUMBNAILS_DIR = APP_DATA_DIR / "thumbnails"
THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = APP_DATA_DIR / "refer.db"

# FAISS index (using short path to guarantee C++ compatibility even if username is in Cyrillic)
_faiss_raw_path = str(APP_DATA_DIR / "refer_faiss.index")
FAISS_PATH = Path(get_short_path(_faiss_raw_path))

# AI Settings
SIGLIP_MODEL = "google/siglip-so400m-patch14-384"
VECTOR_DIMENSION = 1152  # Updated for so400m model

# Hardware / Performance Constants
THUMBNAIL_SIZE = 1024 # px
BATCH_LOAD_SIZE = 50  # For Lazy Loading limits
