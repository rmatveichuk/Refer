import faiss
import sqlite3
import config
import numpy as np
from database.faiss_manager import FaissManager
from database.db_manager import DatabaseManager
from ai.engine import AiEngine
import math

db = DatabaseManager(config.DB_PATH)
faiss_mgr = FaissManager(config.FAISS_PATH)
ai = AiEngine()

print("Testing different threshold values:")

def test_threshold(threshold):
    # original formula
    old_max = 2.0 * math.exp(-5.3 * threshold)
    
    # new formulas? We want distance threshold to be larger at 0.5.
    # At 0.5 we want max_dist to be around 0.5-0.7 instead of 0.14
    # Let's say dist goes from 0 to 2.
    # threshold 0.0 -> dist 2.0
    # threshold 1.0 -> dist ~0.05
    # Linear: 2.0 * (1 - threshold)
    linear_max = 2.0 * (1.0 - threshold)
    
    # Quadratic: 2.0 * (1 - threshold)^2
    # 0.0 -> 2.0
    # 0.5 -> 2.0 * 0.25 = 0.5
    # 1.0 -> 0.0
    quad_max = 2.0 * ((1.0 - threshold) ** 2)
    
    # Cubic
    cubic_max = 2.0 * ((1.0 - threshold) ** 3)
    
    print(f"Slider {threshold*100:.0f}%: Old Exp={old_max:.3f} | Linear={linear_max:.3f} | Quad={quad_max:.3f} | Cubic={cubic_max:.3f}")

for t in [0.0, 0.2, 0.4, 0.5, 0.6, 0.8, 0.9, 1.0]:
    test_threshold(t)
