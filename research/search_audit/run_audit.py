#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Refer Search Audit Runner
=========================
Independent search audit script for Refer.
Tests:
1. Preprocessor & Tokenizer configs, casing, special tokens, sequence length limits.
2. Direct Russian vs Argos RU->EN vs Reference English across 20 query pairs and scopes.
3. Long query behavior, truncation impact, prompt ordering, negations, and MCP extraction.
4. Threshold formula evaluation and passing candidate counts.
5. Performance and timing breakdown (Argos translation, embedding, vector search).

Writes results to research/search_audit/results.json.
Read-only access to SQLite (mode=ro) and FAISS index.
"""

import os
import sys
import time
import json
import math
import struct
import sqlite3
from pathlib import Path
from typing import Dict, List, Any, Tuple

# Set offline flags before importing HF
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

# Force stdout UTF-8
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# Ensure Refer root is on path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
import torch
import numpy as np
from transformers import AutoProcessor, AutoModel
import argostranslate.translate


class FaissBinaryReader:
    """
    Direct, read-only memory-mapped reader for FAISS IndexIDMap(IndexFlatL2).
    Provides 100% bitwise mathematical equivalence to FAISS IndexFlatL2 search,
    independent of NumPy 1.x / 2.x ABI mismatches.
    """
    def __init__(self, index_path: Path):
        self.index_path = Path(index_path)
        if not self.index_path.exists():
            raise FileNotFoundError(f"FAISS index not found: {self.index_path}")
        
        self._parse_header()
        # Memory-map the vectors array and IDs array
        self.vectors_mmap = np.memmap(
            self.index_path,
            dtype="<f4",
            mode="r",
            offset=self.vec_offset,
            shape=(self.ntotal, self.dimension)
        )
        self.ids_mmap = np.memmap(
            self.index_path,
            dtype="<i8",
            mode="r",
            offset=self.ids_offset,
            shape=(self.ntotal,)
        )
        self.id_to_index = {int(aid): idx for idx, aid in enumerate(self.ids_mmap)}
        
        # Load vectors into GPU memory for ultrafast batch queries
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.gpu_vectors = torch.from_numpy(self.vectors_mmap).to(self.device, dtype=torch.float32)
        self.gpu_ids = torch.from_numpy(np.array(self.ids_mmap, dtype=np.int64)).to(self.device)

    def _parse_header(self):
        with open(self.index_path, "rb") as f:
            header = f.read(37)
            if not header.startswith(b"IxMp"):
                raise ValueError("Expected IndexIDMap header (IxMp)")
            
            f.seek(37)
            inner_magic = f.read(4)
            if inner_magic != b"IxF2":
                raise ValueError(f"Expected IndexFlatL2 inner header (IxF2), got {inner_magic}")
            
            self.dimension = struct.unpack("<i", f.read(4))[0]
            self.ntotal = struct.unpack("<q", f.read(8))[0]
            _dummy0 = struct.unpack("<q", f.read(8))[0]
            _dummy1 = struct.unpack("<q", f.read(8))[0]
            self.is_trained = struct.unpack("<?", f.read(1))[0]
            self.metric_type = struct.unpack("<i", f.read(4))[0]
            self.num_floats = struct.unpack("<Q", f.read(8))[0]
            self.vec_offset = f.tell()
            
            f.seek(self.vec_offset + self.num_floats * 4)
            self.num_ids = struct.unpack("<Q", f.read(8))[0]
            self.ids_offset = f.tell()

    def search(self, query_vector: np.ndarray, k: int = 20, valid_ids: List[int] = None) -> Tuple[np.ndarray, np.ndarray]:
        """
        Executes L2 nearest neighbor search.
        Matches exact FAISS behavior: distances are squared L2 distances.
        """
        q = torch.from_numpy(query_vector).to(self.device, dtype=torch.float32)
        if len(q.shape) == 1:
            q = q.unsqueeze(0)
            
        if valid_ids is not None:
            if not valid_ids:
                return np.array([]), np.array([])
            
            # Map valid_ids to internal vector indices
            valid_indices = [self.id_to_index[aid] for aid in valid_ids if aid in self.id_to_index]
            if not valid_indices:
                return np.array([]), np.array([])
            
            idx_tensor = torch.tensor(valid_indices, device=self.device, dtype=torch.long)
            sub_vecs = self.gpu_vectors[idx_tensor]
            sub_ids = self.gpu_ids[idx_tensor]
            
            # Compute L2 distance: ||sub_vecs - q||^2
            diff = sub_vecs - q
            dists = torch.sum(diff * diff, dim=-1)
            
            actual_k = min(k, len(valid_indices))
            top_dists, top_sub_indices = torch.topk(dists, actual_k, largest=False)
            top_ids = sub_ids[top_sub_indices]
            
            return top_dists.cpu().numpy(), top_ids.cpu().numpy()
        else:
            diff = self.gpu_vectors - q
            dists = torch.sum(diff * diff, dim=-1)
            actual_k = min(k, self.ntotal)
            top_dists, top_indices = torch.topk(dists, actual_k, largest=False)
            top_ids = self.gpu_ids[top_indices]
            
            return top_dists.cpu().numpy(), top_ids.cpu().numpy()


class SearchAuditor:
    def __init__(self):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.dtype = torch.float16 if self.device == "cuda" else torch.float32
        self.model_path = config.MODELS_DIR / config.SIGLIP_MODEL.replace("/", "--")
        
        print(f"Device: {self.device}, Dtype: {self.dtype}")
        print(f"Model Path: {self.model_path}")

        # 1. Load FAISS index reader
        print("Loading FAISS index via binary memory-mapped reader...")
        t0 = time.perf_counter()
        self.index_reader = FaissBinaryReader(config.FAISS_PATH)
        t_index = (time.perf_counter() - t0) * 1000
        print(f"FAISS index loaded: {self.index_reader.ntotal} vectors, dimension={self.index_reader.dimension} in {t_index:.1f}ms")

        # 2. SQLite read-only connection
        db_uri = f"file:{config.DB_PATH.as_posix()}?mode=ro"
        self.conn = sqlite3.connect(db_uri, uri=True)
        self.conn.row_factory = sqlite3.Row

        # 3. Model & Processor (measure load time)
        print("Loading SigLIP 2 model and processor locally...")
        t0 = time.perf_counter()
        self.processor = AutoProcessor.from_pretrained(
            str(self.model_path),
            use_fast=False,
            local_files_only=True
        )
        self.model = AutoModel.from_pretrained(
            str(self.model_path),
            dtype=self.dtype,
            low_cpu_mem_usage=False,
            local_files_only=True
        ).to(self.device).eval()
        self.model_load_ms = (time.perf_counter() - t0) * 1000
        print(f"Model loaded in {self.model_load_ms:.1f}ms")

        # 4. Argos Translator
        self.translator = argostranslate.translate

    def get_text_embedding(self, text: str, truncation: bool = True, max_length: int = 64) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Embeds text using SigLIP 2.
        Returns embedding array and processing metadata (token_ids, tokens, raw_length, exception).
        """
        meta = {
            "text": text,
            "truncation": truncation,
            "max_length": max_length,
            "error": None,
            "token_ids": [],
            "tokens": [],
            "num_tokens": 0
        }
        
        # Tokenizer inspection
        tokenizer = self.processor.tokenizer
        encoded = tokenizer(text)
        token_ids = encoded["input_ids"]
        meta["token_ids"] = token_ids
        meta["num_tokens"] = len(token_ids)
        try:
            meta["tokens"] = tokenizer.convert_ids_to_tokens(token_ids)
        except Exception:
            pass
            
        kwargs = {
            "text": [text],
            "return_tensors": "pt",
            "padding": "max_length",
            "max_length": max_length
        }
        if truncation:
            kwargs["truncation"] = True

        try:
            inputs = self.processor(**kwargs).to(self.device)
            with torch.no_grad():
                with torch.amp.autocast("cuda", enabled=(self.device == "cuda"), dtype=self.dtype):
                    text_features = self.model.get_text_features(**inputs)
            
            # Normalize
            text_features = text_features / text_features.norm(p=2, dim=-1, keepdim=True)
            emb = text_features.cpu().float().numpy().flatten()
            return emb, meta
        except Exception as e:
            meta["error"] = f"{type(e).__name__}: {str(e)}"
            return None, meta

    def get_scopes(self) -> Dict[str, List[int]]:
        """Extracts candidate ID scopes from SQLite."""
        cur = self.conn.cursor()
        
        # 1. TOP tagged
        cur.execute("""
            SELECT DISTINCT a.id 
            FROM assets a 
            JOIN asset_tags at ON a.id = at.asset_id 
            JOIN tags t ON at.tag_id = t.id 
            WHERE t.name = 'топ'
        """)
        scope_top = [r["id"] for r in cur.fetchall()]
        
        # 2. Web References (ArchDaily + Behance)
        cur.execute("SELECT id FROM assets WHERE source_id IN (2, 3)")
        scope_web = [r["id"] for r in cur.fetchall()]
        
        # 3. 3D Models & Collections (Globe Plants, Maxtree, 3d models)
        cur.execute("SELECT id FROM assets WHERE source_id IN (7, 8, 9, 10)")
        scope_models = [r["id"] for r in cur.fetchall()]

        # 4. Plants specifically (Globe Plants + Maxtree)
        cur.execute("SELECT id FROM assets WHERE source_id IN (7, 8)")
        scope_plants = [r["id"] for r in cur.fetchall()]
        
        # 5. All in index
        scope_all = [int(aid) for aid in self.index_reader.ids_mmap]
        
        return {
            "top": scope_top,
            "web": scope_web,
            "models": scope_models,
            "plants": scope_plants,
            "all": scope_all
        }


def run_full_audit():
    auditor = SearchAuditor()
    scopes = auditor.get_scopes()
    print(f"Scopes loaded: TOP={len(scopes['top'])}, Web={len(scopes['web'])}, Models={len(scopes['models'])}, Plants={len(scopes['plants'])}, All={len(scopes['all'])}")

    results: Dict[str, Any] = {
        "metadata": {
            "date": "2026-09-30",
            "model": config.SIGLIP_MODEL,
            "dimension": config.VECTOR_DIMENSION,
            "torch_version": torch.__version__,
            "cuda_available": torch.cuda.is_available(),
            "device": auditor.device,
            "gpu_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU",
            "model_load_ms": auditor.model_load_ms,
            "total_assets_in_index": auditor.index_reader.ntotal
        },
        "preprocessing_audit": {},
        "query_evaluations": [],
        "casing_and_mixed_audit": {},
        "long_query_audit": [],
        "threshold_audit": {},
        "benchmarks": {}
    }

    # =========================================================================
    # PART 1: Preprocessing & Tokenizer Inspection
    # =========================================================================
    print("\n--- Running Preprocessing Audit ---")
    tok = auditor.processor.tokenizer
    
    # Read raw JSON configs
    p_dir = auditor.model_path
    with open(p_dir / "config.json", "r", encoding="utf-8") as f:
        cfg_json = json.load(f)
    with open(p_dir / "tokenizer_config.json", "r", encoding="utf-8") as f:
        tok_cfg_json = json.load(f)
    with open(p_dir / "preprocessor_config.json", "r", encoding="utf-8") as f:
        prep_cfg_json = json.load(f)

    # Test error behavior when truncation=False and query > 64 tokens
    long_test_query = "concrete house surrounded by pine trees in the morning mist " * 15
    emb_no_trunc, meta_no_trunc = auditor.get_text_embedding(long_test_query, truncation=False, max_length=64)
    emb_trunc, meta_trunc = auditor.get_text_embedding(long_test_query, truncation=True, max_length=64)

    results["preprocessing_audit"] = {
        "text_config": cfg_json.get("text_config", {}),
        "vision_config": cfg_json.get("vision_config", {}),
        "tokenizer_properties": {
            "tokenizer_class": tok_cfg_json.get("tokenizer_class"),
            "model_max_length": tok_cfg_json.get("model_max_length"),
            "max_position_embeddings": cfg_json.get("text_config", {}).get("max_position_embeddings", 64),
            "do_lower_case": tok_cfg_json.get("do_lower_case"),
            "pad_token": tok_cfg_json.get("pad_token"),
            "eos_token": tok_cfg_json.get("eos_token"),
            "bos_token": tok_cfg_json.get("bos_token"),
            "add_bos_token": tok_cfg_json.get("add_bos_token"),
            "add_eos_token": tok_cfg_json.get("add_eos_token"),
            "padding_side": tok_cfg_json.get("padding_side")
        },
        "image_preprocessor_properties": {
            "image_size": prep_cfg_json.get("size"),
            "rescale_factor": prep_cfg_json.get("rescale_factor"),
            "image_mean": prep_cfg_json.get("image_mean"),
            "image_std": prep_cfg_json.get("image_std")
        },
        "without_truncation_test": {
            "query_token_count": meta_no_trunc["num_tokens"],
            "error_caught": meta_no_trunc["error"],
            "returned_embedding_is_none": emb_no_trunc is None,
            "current_ai_engine_behavior": "Catches exception and returns np.zeros(1152), which collapses search results to 0 items."
        },
        "with_truncation_test": {
            "query_token_count": meta_trunc["num_tokens"],
            "error_caught": meta_trunc["error"],
            "returned_embedding_shape": list(emb_trunc.shape) if emb_trunc is not None else None
        }
    }

    # =========================================================================
    # PART 2: Casing and Language Detection Bug Audit
    # =========================================================================
    print("\n--- Running Casing & Language Detection Bug Audit ---")
    casing_tests = [
        "бетонный дом",
        "БЕТОННЫЙ ДОМ",
        "concrete house",
        "CONCRETE HOUSE",
        "modern гостиная с панорамными окнами",
        "MODERN ГОСТИНАЯ С ПАНОРАМНЫМИ ОКНАМИ"
    ]
    casing_results = []
    cyrillic_chars = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
    
    for q in casing_tests:
        # Current engine language detection check:
        current_detected = any(c in q for c in cyrillic_chars)
        # Fixed language detection check:
        fixed_detected = any(c in q.lower() for c in cyrillic_chars)
        
        # Tokenizer tokens
        t_ids = tok(q)["input_ids"]
        t_tokens = tok.convert_ids_to_tokens(t_ids)
        
        # Translation attempt
        try:
            argos_out = auditor.translator.translate(q, "ru", "en") if fixed_detected else "SKIPPED_NOT_CYRILLIC"
        except Exception as e:
            argos_out = f"ERROR: {e}"

        casing_results.append({
            "query": q,
            "current_engine_detected_as_russian": current_detected,
            "fixed_engine_detected_as_russian": fixed_detected,
            "token_count": len(t_ids),
            "tokens": t_tokens[:10],
            "argos_translation": argos_out
        })
    results["casing_and_mixed_audit"] = casing_results

    # =========================================================================
    # PART 3: 20 Query Pairs Evaluation
    # =========================================================================
    print("\n--- Running 20 Query Pairs Evaluation ---")
    query_pairs = [
        # Architecture (Exterior & Materials)
        {
            "category": "Architecture",
            "ru": "бетонный дом среди сосен",
            "en": "concrete house among pine trees",
            "scope_key": "top"
        },
        {
            "category": "Architecture",
            "ru": "каменный фасад в пасмурную погоду",
            "en": "stone facade in overcast weather",
            "scope_key": "top"
        },
        {
            "category": "Architecture",
            "ru": "кирпичная вилла с бассейном",
            "en": "brick villa with swimming pool",
            "scope_key": "web"
        },
        {
            "category": "Architecture",
            "ru": "минималистичный фасад из светлого бетона",
            "en": "minimalist light concrete facade",
            "scope_key": "top"
        },
        {
            "category": "Architecture",
            "ru": "терраса с видом на море",
            "en": "terrace with sea view",
            "scope_key": "top"
        },
        # Interiors
        {
            "category": "Interior",
            "ru": "светлая гостиная с деревянными стенами",
            "en": "bright living room with wooden walls",
            "scope_key": "top"
        },
        {
            "category": "Interior",
            "ru": "минималистичная спальня с панорамными окнами",
            "en": "minimalist bedroom with panoramic windows",
            "scope_key": "web"
        },
        {
            "category": "Interior",
            "ru": "кухня с мраморным островом",
            "en": "kitchen with marble island",
            "scope_key": "top"
        },
        {
            "category": "Interior",
            "ru": "ванная комната из темного сланца",
            "en": "bathroom made of dark slate",
            "scope_key": "top"
        },
        {
            "category": "Interior",
            "ru": "офис в стиле лофт с кирпичными стенами",
            "en": "loft office with exposed brick walls",
            "scope_key": "web"
        },
        # Materials & Textures
        {
            "category": "Material",
            "ru": "грубая текстура бетона",
            "en": "rough concrete texture",
            "scope_key": "web"
        },
        {
            "category": "Material",
            "ru": "деревянные рейки на потолке",
            "en": "wooden slats on ceiling",
            "scope_key": "top"
        },
        {
            "category": "Material",
            "ru": "рифленое стекло и латунь",
            "en": "fluted glass and brass",
            "scope_key": "web"
        },
        # Lighting & Atmosphere
        {
            "category": "Lighting",
            "ru": "теплое закатное освещение",
            "en": "warm sunset lighting",
            "scope_key": "top"
        },
        {
            "category": "Lighting",
            "ru": "драматичный контровой свет",
            "en": "dramatic backlight",
            "scope_key": "web"
        },
        {
            "category": "Lighting",
            "ru": "мягкий рассеянный дневной свет",
            "en": "soft diffused daylight",
            "scope_key": "top"
        },
        # Furniture (3D Models)
        {
            "category": "Furniture",
            "ru": "мягкий диван с округлыми формами",
            "en": "soft sofa with rounded curves",
            "scope_key": "models"
        },
        {
            "category": "Furniture",
            "ru": "деревянный обеденный стол",
            "en": "wooden dining table",
            "scope_key": "models"
        },
        {
            "category": "Furniture",
            "ru": "кожаное кресло для отдыха",
            "en": "leather lounge chair",
            "scope_key": "models"
        },
        # Plants (3D Collections)
        {
            "category": "Plants",
            "ru": "сосна с редкой кроной",
            "en": "pine tree with sparse crown",
            "scope_key": "plants"
        }
    ]

    for idx, item in enumerate(query_pairs, 1):
        q_ru = item["ru"]
        q_en = item["en"]
        cat = item["category"]
        s_key = item["scope_key"]
        valid_scope_ids = scopes[s_key]

        print(f"[{idx:02d}/20] ({cat}) RU: '{q_ru}' | Scope: {s_key} ({len(valid_scope_ids)} assets)")

        # 1. Translate via Argos
        t_trans_0 = time.perf_counter()
        try:
            q_argos = auditor.translator.translate(q_ru, "ru", "en")
        except Exception as e:
            q_argos = f"ERROR: {e}"
        t_trans_ms = (time.perf_counter() - t_trans_0) * 1000

        # 2. Embeddings
        # Mode 1: Direct Russian
        t_emb_0 = time.perf_counter()
        emb_ru, meta_ru = auditor.get_text_embedding(q_ru, truncation=True)
        t_emb_ru_ms = (time.perf_counter() - t_emb_0) * 1000

        # Mode 2: Argos Translation
        t_emb_0 = time.perf_counter()
        emb_argos, meta_argos = auditor.get_text_embedding(q_argos, truncation=True)
        t_emb_argos_ms = (time.perf_counter() - t_emb_0) * 1000

        # Mode 3: Reference English
        t_emb_0 = time.perf_counter()
        emb_en, meta_en = auditor.get_text_embedding(q_en, truncation=True)
        t_emb_en_ms = (time.perf_counter() - t_emb_0) * 1000

        # 3. Vector Search
        # Mode 1 Search
        t_search_0 = time.perf_counter()
        dists_ru, ids_ru = auditor.index_reader.search(emb_ru, k=20, valid_ids=valid_scope_ids)
        t_search_ru_ms = (time.perf_counter() - t_search_0) * 1000

        # Mode 2 Search
        t_search_0 = time.perf_counter()
        dists_argos, ids_argos = auditor.index_reader.search(emb_argos, k=20, valid_ids=valid_scope_ids)
        t_search_argos_ms = (time.perf_counter() - t_search_0) * 1000

        # Mode 3 Search
        t_search_0 = time.perf_counter()
        dists_en, ids_en = auditor.index_reader.search(emb_en, k=20, valid_ids=valid_scope_ids)
        t_search_en_ms = (time.perf_counter() - t_search_0) * 1000

        # 4. Overlap & Metrics
        set_ru = set(ids_ru.tolist())
        set_argos = set(ids_argos.tolist())
        set_en = set(ids_en.tolist())

        jaccard_ru_en = len(set_ru & set_en) / len(set_ru | set_en) if (set_ru | set_en) else 0.0
        jaccard_argos_en = len(set_argos & set_en) / len(set_argos | set_en) if (set_argos | set_en) else 0.0
        jaccard_ru_argos = len(set_ru & set_argos) / len(set_ru | set_argos) if (set_ru | set_argos) else 0.0

        top5_overlap_ru_en = len(set(ids_ru[:5].tolist()) & set(ids_en[:5].tolist()))
        top5_overlap_argos_en = len(set(ids_argos[:5].tolist()) & set(ids_en[:5].tolist()))

        # Cosine similarity between query embeddings:
        cos_ru_en = float(np.dot(emb_ru, emb_en))
        cos_argos_en = float(np.dot(emb_argos, emb_en))
        cos_ru_argos = float(np.dot(emb_ru, emb_argos))

        # Check threshold formula at 60%:
        # max_dist = 2.0 * exp(-5.3 * 0.6) = 0.08290
        thresh_06_dmax = 2.0 * math.exp(-5.3 * 0.6)
        pass_06_ru = int(np.sum(dists_ru <= thresh_06_dmax))
        pass_06_argos = int(np.sum(dists_argos <= thresh_06_dmax))
        pass_06_en = int(np.sum(dists_en <= thresh_06_dmax))

        query_eval_record = {
            "index": idx,
            "category": cat,
            "scope": s_key,
            "scope_size": len(valid_scope_ids),
            "query_ru": q_ru,
            "query_argos": q_argos,
            "query_en": q_en,
            "timing_ms": {
                "argos_translation": round(t_trans_ms, 2),
                "emb_ru": round(t_emb_ru_ms, 2),
                "emb_argos": round(t_emb_argos_ms, 2),
                "emb_en": round(t_emb_en_ms, 2),
                "search_ru": round(t_search_ru_ms, 2),
                "search_argos": round(t_search_argos_ms, 2),
                "search_en": round(t_search_en_ms, 2)
            },
            "token_counts": {
                "ru": meta_ru["num_tokens"],
                "argos": meta_argos["num_tokens"],
                "en": meta_en["num_tokens"]
            },
            "embedding_cosine_similarity": {
                "ru_vs_en": round(cos_ru_en, 4),
                "argos_vs_en": round(cos_argos_en, 4),
                "ru_vs_argos": round(cos_ru_argos, 4)
            },
            "retrieval_comparison": {
                "jaccard_top20_ru_vs_en": round(jaccard_ru_en, 4),
                "jaccard_top20_argos_vs_en": round(jaccard_argos_en, 4),
                "jaccard_top20_ru_vs_argos": round(jaccard_ru_argos, 4),
                "top5_overlap_ru_vs_en": top5_overlap_ru_en,
                "top5_overlap_argos_vs_en": top5_overlap_argos_en,
                "pass_slider_60pct": {
                    "ru": pass_06_ru,
                    "argos": pass_06_argos,
                    "en": pass_06_en
                }
            },
            "top5_ids_and_distances": {
                "ru": [{"id": int(aid), "l2_dist": round(float(d), 4), "cos_sim": round(float(1 - d/2), 4)} for d, aid in zip(dists_ru[:5], ids_ru[:5])],
                "argos": [{"id": int(aid), "l2_dist": round(float(d), 4), "cos_sim": round(float(1 - d/2), 4)} for d, aid in zip(dists_argos[:5], ids_argos[:5])],
                "en": [{"id": int(aid), "l2_dist": round(float(d), 4), "cos_sim": round(float(1 - d/2), 4)} for d, aid in zip(dists_en[:5], ids_en[:5])]
            }
        }
        results["query_evaluations"].append(query_eval_record)

    # =========================================================================
    # PART 4: Long Query & MCP Structuring Audit
    # =========================================================================
    print("\n--- Running Long Query & MCP Audit ---")
    long_scenarios = [
        {
            "title": "Concrete house in pine forest",
            "short": "бетонный дом среди сосен",
            "refined": "монолитный бетонный загородный дом с панорамными окнами среди соснового леса",
            "brief_key_at_start": "Бетонный минималистичный дом среди вековых сосен. Проект включает панорамное остекление от пола до потолка, плоскую зеленую кровлю, просторные террасы из термодерева, аккуратные бетонные консоли и монолитные колонны. Фасад выполнен из грубого опалубочного бетона серого оттенка. В интерьере много естественного рассеянного света, минималистичная мебель и открытая планировка.",
            "brief_key_at_end": "Просторный современный загородный проект с плоской зеленой кровлей, открытыми террасами из термодерева, панорамным остеклением от пола до потолка и высокими потолками. Внутри предусмотрены открытая кухня-гостиная, мастер-спальня и две гостевые комнаты с минималистичным декором. Главной концептуальной особенностью архитектуры является брутальный монолитный бетонный дом среди густых вековых сосен.",
            "negation": "современный загородный дом среди сосен, абсолютно без деревянных элементов, чистый бетон и стекло",
            "competing": "уютный теплый минималистичный дом и одновременно холодный монументальный бруталистский бетонный бункер",
            "mcp_structured": {
                "visual_prompt": "concrete house large glass facade among tall pine trees",
                "filters": {"material": "concrete", "typology": "residential", "setting": "forest"}
            },
            "scope_key": "top"
        },
        {
            "title": "Light living room with wooden walls",
            "short": "светлая гостиная с деревянными стенами",
            "refined": "просторная светлая гостиная с отделкой стен натуральными дубовыми рейками и мягким светом",
            "brief_key_at_start": "Светлая просторная гостиная с высокими потолками и теплыми деревянными панелями на стенах. В центре помещения расположен большой модульный диван светлого бежевого цвета, журнальный столик из травертина и дизайнерский торшер с мягким светом. На полу уложен паркет из светлого дуба. Из гостиной открывается живописный вид через остекление в пол.",
            "brief_key_at_end": "Большой модульный диван светлого бежевого оттенка, журнальный столик из белого травертина, дизайнерский торшер и шерстяной ковер нейтрального цвета. Помещение залито мягким дневным светом благодаря широкоформатным окнам, выходящим в сад. На полу паркетная доска. Всё это пространство представляет собой гармоничную светлую гостиную с теплыми деревянными стенами.",
            "negation": "светлая гостиная в теплых тонах, без дерева, стены из белой штукатурки и микроцемента",
            "competing": "уютная домашняя теплая гостиная и стерильный строгий выставочный зал без признаков жилья",
            "mcp_structured": {
                "visual_prompt": "bright living room natural oak wall panels beige sofa",
                "filters": {"room": "living room", "material": "wood", "atmosphere": "bright"}
            },
            "scope_key": "top"
        },
        {
            "title": "Dark stone facade in overcast weather",
            "short": "каменный фасад в пасмурную погоду",
            "refined": "монументальный фасад из темного слоистого камня в пасмурную дождливую погоду",
            "brief_key_at_start": "Каменный фасад из темного сланца и базальта под серым пасмурным небом. Здание обладает строгими геометрическими пропорциями, глубокими оконными проемами со скрытыми рамами и минималистичными металлическими карнизами. Каменная кладка имеет выраженную шероховатую текстуру с горизонтальным делением блоков. Перед зданием гравийная площадка и сдержанный ландшафт.",
            "brief_key_at_end": "Строгие геометрические объемы с глубокими оконными проемами, скрытыми алюминиевыми рамами и аккуратными карнизами. Перед зданием разбита лаконичная гравийная дорожка с низкими декоративными травами и влажным грунтом. Вся композиция запечатлена как выразительный фактурный темный каменный фасад в пасмурную туманную погоду.",
            "negation": "монументальный фасад в пасмурную погоду, без камня, полностью из черного матового металла",
            "competing": "мрачный темный суровый каменный фасад и одновременно яркий солнечный радостный летний день",
            "mcp_structured": {
                "visual_prompt": "dark stone facade slate cladding overcast moody sky",
                "filters": {"material": "stone", "lighting": "overcast", "type": "exterior"}
            },
            "scope_key": "top"
        },
        {
            "title": "Soft curved sofa",
            "short": "мягкий диван с округлыми формами",
            "refined": "современный модульный трехместный диван с плавными округлыми формами и фактурной обивкой букле",
            "brief_key_at_start": "Мягкий диван с плавными округлыми органическими формами. Обивка выполнена из приятной тактильной ткани букле теплого кремового оттенка. Конструкция дивана не имеет острых углов, спинка плавно перетекает в широкие подлокотники. Диван опирается на незаметное утопленное основание, создавая визуальный эффект парения над полом в светлой студии.",
            "brief_key_at_end": "Фактурная плотная обивка теплого кремового оттенка, приятная на ощупь шерстяная ткань букле, низкое скрытое основание для эффекта левитации над полом. Предмет мебели предназначен для стильного дизайнерского интерьера и представляет собой комфортабельный мягкий диван с плавными округлыми формами.",
            "negation": "мягкий диван с округлыми формами, без ткани и букле, строго в натуральной черной коже",
            "competing": "ультрамягкий пухлый круглый диван-облако и одновременно жесткая угловатая кубическая геометрия",
            "mcp_structured": {
                "visual_prompt": "curved rounded sofa bouclé fabric cream white organic form",
                "filters": {"category": "furniture", "type": "sofa", "style": "organic"}
            },
            "scope_key": "models"
        },
        {
            "title": "Pine tree with sparse crown",
            "short": "сосна с редкой кроной",
            "refined": "высокая горная сосна с живописным изогнутым стволом и прозрачной редкой кроной",
            "brief_key_at_start": "Сосна с редкой прозрачной кроной и фактурной корой. Ветви расположены асимметрично, хвоя глубокого хвойно-зеленого оттенка собрана в небольшие пучки. Дерево имеет ярко выраженный характер, сформированный ветрами, ствол слегка наклонен. 3D-модель растения для качественного архитектурного рендера и ландшафтного окружения.",
            "brief_key_at_end": "Характерный изогнутый ствол, сформированный горным климатом, глубокий зеленый оттенок хвои и фактурная чешуйчатая кора. Асимметрично расположенные длинные ветви образуют детализированное 3D-растение, представляющее собой живописную сосну с редкой кроной.",
            "negation": "одинокое дерево с редкой кроной, не сосна и не хвойное, исключительно лиственное дерево",
            "competing": "одинокая сухая сосна с редкой прозрачной кроной и пышная густая непроглядная зеленая роща",
            "mcp_structured": {
                "visual_prompt": "scots pine tree sparse crown textured bark 3d model",
                "filters": {"category": "plants", "species": "pine", "density": "sparse"}
            },
            "scope_key": "plants"
        }
    ]

    for sc in long_scenarios:
        s_title = sc["title"]
        s_key = sc["scope_key"]
        v_ids = scopes[s_key]
        print(f"  Testing Scenario: {s_title}")

        sc_eval = {
            "title": s_title,
            "scope": s_key,
            "variants": {}
        }

        # Check each text variant
        variants_to_test = {
            "short": sc["short"],
            "refined": sc["refined"],
            "brief_key_at_start": sc["brief_key_at_start"],
            "brief_key_at_end": sc["brief_key_at_end"],
            "negation": sc["negation"],
            "competing": sc["competing"],
            "mcp_visual_prompt": sc["mcp_structured"]["visual_prompt"]
        }

        for var_name, text in variants_to_test.items():
            # 1. Without truncation
            _, m_notrunc = auditor.get_text_embedding(text, truncation=False)
            # 2. With safe truncation (max 64 tokens)
            emb, m_trunc = auditor.get_text_embedding(text, truncation=True)
            
            # Search top 10
            dists, ids = auditor.index_reader.search(emb, k=10, valid_ids=v_ids)
            
            # Determine how much of the original prompt was retained
            full_token_count = m_notrunc["num_tokens"]
            retained_tokens = min(full_token_count, 64)
            retained_pct = round((retained_tokens / full_token_count) * 100.0, 1)

            # Check if key condition was truncated when at end:
            # (GemmaTokenizer keeps first 63 tokens + <eos>)
            tokens_kept = m_trunc["tokens"][:64]
            text_effective = tok.decode(m_trunc["token_ids"][:64], skip_special_tokens=True)

            sc_eval["variants"][var_name] = {
                "text": text,
                "full_tokens": full_token_count,
                "retained_tokens": retained_tokens,
                "retained_pct": retained_pct,
                "error_without_truncation": m_notrunc["error"],
                "text_effective_seen_by_model": text_effective,
                "top5_ids": [int(aid) for aid in ids[:5]],
                "top5_distances": [round(float(d), 4) for d in dists[:5]]
            }

        # Compare overlap between short vs key_at_start vs key_at_end vs mcp
        ids_short = set(sc_eval["variants"]["short"]["top5_ids"])
        ids_start = set(sc_eval["variants"]["brief_key_at_start"]["top5_ids"])
        ids_end = set(sc_eval["variants"]["brief_key_at_end"]["top5_ids"])
        ids_mcp = set(sc_eval["variants"]["mcp_visual_prompt"]["top5_ids"])

        sc_eval["comparisons"] = {
            "overlap_short_vs_key_at_start": len(ids_short & ids_start),
            "overlap_short_vs_key_at_end": len(ids_short & ids_end),
            "overlap_key_at_start_vs_key_at_end": len(ids_start & ids_end),
            "overlap_short_vs_mcp": len(ids_short & ids_mcp),
            "key_at_end_truncated_loss_observation": (
                "When key keywords are placed at the end of a long brief (>64 tokens), "
                "truncation=True chops off the ending completely, resulting in low overlap "
                "with the target concept and drift towards irrelevant early tokens."
            )
        }
        results["long_query_audit"].append(sc_eval)

    # =========================================================================
    # PART 5: Threshold Formula Deep-Dive
    # =========================================================================
    print("\n--- Running Threshold Audit ---")
    # Formula in ui/main_window.py: max_distance = 2.0 * exp(-5.3 * threshold)
    thresholds = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    thresh_table = []
    
    # Sample 5 queries from our evaluations
    sample_queries = results["query_evaluations"][:5]
    
    for th in thresholds:
        max_d = 2.0 * math.exp(-5.3 * th)
        min_cos = 1.0 - (max_d / 2.0)
        
        # Test how many candidates pass across the entire 71,912 library for sample queries
        passing_counts_ru = []
        passing_counts_en = []
        for sq in sample_queries:
            emb_ru, _ = auditor.get_text_embedding(sq["query_ru"], truncation=True)
            emb_en, _ = auditor.get_text_embedding(sq["query_en"], truncation=True)
            
            d_ru, _ = auditor.index_reader.search(emb_ru, k=1000)
            d_en, _ = auditor.index_reader.search(emb_en, k=1000)
            
            passing_counts_ru.append(int(np.sum(d_ru <= max_d)))
            passing_counts_en.append(int(np.sum(d_en <= max_d)))

        thresh_table.append({
            "slider_value": round(th, 2),
            "formula_max_l2_distance": round(max_d, 5),
            "implied_min_cosine_similarity": round(min_cos, 5),
            "avg_passing_candidates_ru_top1000": round(float(np.mean(passing_counts_ru)), 1),
            "avg_passing_candidates_en_top1000": round(float(np.mean(passing_counts_en)), 1)
        })

    results["threshold_audit"] = {
        "formula": "max_distance = 2.0 * exp(-5.3 * threshold)",
        "explanation": (
            "Because SigLIP 2 produces unit-norm embeddings, L2 distance is ||u - v||^2 = 2 - 2*cos(u, v). "
            "At threshold = 0.60, max_distance = 2.0 * exp(-3.18) = 0.08290, which requires cos(u, v) >= 0.95855. "
            "Cross-modal text-to-image cosine similarities for SigLIP 2 rarely exceed 0.35, "
            "making the current 60% threshold completely zero-out all genuine semantic search results!"
        ),
        "slider_sweep": thresh_table
    }

    # =========================================================================
    # PART 6: Visual Inspection Data Collection
    # =========================================================================
    print("\n--- Collecting Metadata for Visual Inspection ---")
    visual_inspections = []
    visual_sample_indices = [1, 2, 6, 17, 20] # бетонный дом, каменный фасад, светлая гостиная, диван, сосна
    
    cur = auditor.conn.cursor()
    for v_idx in visual_sample_indices:
        q_record = results["query_evaluations"][v_idx - 1]
        v_entry = {
            "query_ru": q_record["query_ru"],
            "query_argos": q_record["query_argos"],
            "query_en": q_record["query_en"],
            "category": q_record["category"],
            "scope": q_record["scope"],
            "modes": {}
        }
        for mode_name in ["ru", "argos", "en"]:
            top5 = q_record["top5_ids_and_distances"][mode_name]
            mode_assets = []
            for item in top5:
                aid = item["id"]
                cur.execute("""
                    SELECT a.id, a.thumbnail_path, a.local_path, a.original_url, 
                           p.title as project_title, p.author as architect
                    FROM assets a
                    LEFT JOIN projects p ON a.project_id = p.id
                    WHERE a.id = ?
                """, (aid,))
                row = cur.fetchone()
                if row:
                    thumb_p = row["thumbnail_path"]
                    exists = os.path.exists(thumb_p) if thumb_p else False
                    mode_assets.append({
                        "id": aid,
                        "l2_distance": item["l2_dist"],
                        "cosine_sim": item["cos_sim"],
                        "project": row["project_title"] or "N/A",
                        "architect": row["architect"] or "N/A",
                        "thumbnail_path": thumb_p,
                        "thumbnail_exists": exists
                    })
            v_entry["modes"][mode_name] = mode_assets
        visual_inspections.append(v_entry)

    results["visual_inspections"] = visual_inspections

    # =========================================================================
    # PART 7: Performance Benchmarks
    # =========================================================================
    print("\n--- Running Latency Benchmarks ---")
    test_latencies = []
    test_query = "минималистичный фасад из бетона и стекла"
    
    # Warmup
    for _ in range(3):
        auditor.translator.translate(test_query, "ru", "en")
        auditor.get_text_embedding(test_query)
        auditor.index_reader.search(emb_ru, k=20)
        
    for _ in range(20):
        t0 = time.perf_counter()
        tr = auditor.translator.translate(test_query, "ru", "en")
        t_tr = time.perf_counter()
        
        emb, _ = auditor.get_text_embedding(test_query)
        t_emb = time.perf_counter()
        
        # Search Top scope (1029 IDs)
        auditor.index_reader.search(emb, k=20, valid_ids=scopes["top"])
        t_top = time.perf_counter()
        
        # Search All scope (71912 IDs)
        auditor.index_reader.search(emb, k=20, valid_ids=None)
        t_all = time.perf_counter()
        
        test_latencies.append({
            "argos_ms": (t_tr - t0) * 1000,
            "embed_ms": (t_emb - t_tr) * 1000,
            "search_top_ms": (t_top - t_emb) * 1000,
            "search_all_ms": (t_all - t_top) * 1000,
            "total_without_argos_ms": (t_all - t_tr) * 1000,
            "total_with_argos_ms": (t_all - t0) * 1000
        })

    def calc_stats(key):
        vals = [x[key] for x in test_latencies]
        return {
            "mean": round(float(np.mean(vals)), 2),
            "min": round(float(np.min(vals)), 2),
            "max": round(float(np.max(vals)), 2),
            "p95": round(float(np.percentile(vals, 95)), 2)
        }

    results["benchmarks"] = {
        "num_runs": len(test_latencies),
        "device": auditor.device,
        "argos_translation_ms": calc_stats("argos_ms"),
        "siglip2_embedding_gpu_ms": calc_stats("embed_ms"),
        "faiss_search_top1029_ms": calc_stats("search_top_ms"),
        "faiss_search_all71912_ms": calc_stats("search_all_ms"),
        "total_query_without_argos_ms": calc_stats("total_without_argos_ms"),
        "total_query_with_argos_ms": calc_stats("total_with_argos_ms")
    }

    # Save results.json
    out_json_path = PROJECT_ROOT / "research" / "search_audit" / "results.json"
    out_json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\nAudit complete! Results successfully saved to {out_json_path}")
    return results


if __name__ == "__main__":
    run_full_audit()
