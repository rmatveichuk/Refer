import logging
import torch
import numpy as np
from PIL import Image
from transformers import AutoProcessor, AutoModel
import config
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# Оптимизация PyTorch для современных GPU (Ampere/Ada)
torch.backends.cuda.matmul.allow_tf32 = True
torch.backends.cudnn.allow_tf32 = True
torch.backends.cudnn.benchmark = True

logger = logging.getLogger(__name__)

class AiEngine:
    def __init__(self):
        logger.info(f"Initializing AiEngine with model: {config.SIGLIP_MODEL}")
        
        # Load SigLIP model via Transformers
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        
        # Для RTX 4060 используем float16. bfloat16 иногда капризничает в определенных операциях.
        self.dtype = torch.float16 if torch.cuda.is_available() else torch.float32
        
        logger.info(f"Using device: {self.device}, dtype: {self.dtype}")
        
        # Загружаем модель сразу в нужном типе данных. 
        # low_cpu_mem_usage=False предотвращает ошибку "Cannot copy out of meta tensor"
        # Since we downloaded to a local_dir during SetupWizard without symlinks,
        # we construct the exact path to that local_dir to load the model.
        model_path = config.MODELS_DIR / config.SIGLIP_MODEL.replace("/", "--")
        if not model_path.exists():
            # Fallback to cache_dir if it was downloaded the old way
            model_path = config.SIGLIP_MODEL
            
        self.model = AutoModel.from_pretrained(
            str(model_path), 
            torch_dtype=self.dtype, 
            low_cpu_mem_usage=False,
            cache_dir=str(config.MODELS_DIR) if model_path == config.SIGLIP_MODEL else None
        ).to(self.device).eval()
        
        try:
            self.processor = AutoProcessor.from_pretrained(
                str(model_path), 
                use_fast=False,
                cache_dir=str(config.MODELS_DIR) if model_path == config.SIGLIP_MODEL else None
            )
        except Exception:
            self.processor = AutoProcessor.from_pretrained(
                str(model_path), 
                cache_dir=str(config.MODELS_DIR) if model_path == config.SIGLIP_MODEL else None
            )
        
        # Для параллельной загрузки картинок
        self.executor = ThreadPoolExecutor(max_workers=8)
        
    @property
    def text_token_limit(self) -> int:
        return self.model.config.text_config.max_position_embeddings

    def get_text_query_info(self, text: str) -> dict:
        """Counts the actual tokenizer tokens, including EOS, without inference."""
        tokenizer = self.processor.tokenizer
        ids = tokenizer(text)["input_ids"]
        effective = tokenizer(text, truncation=True, max_length=self.text_token_limit)["input_ids"]
        return {"token_count": len(ids), "token_limit": self.text_token_limit,
                "truncated": len(ids) > self.text_token_limit,
                "effective_text": tokenizer.decode(effective, skip_special_tokens=True)}

    def get_text_embedding(self, text: str) -> np.ndarray:
        """Embeds the original multilingual query; failures never become vectors."""
        text = text.strip()
        if not text:
            raise ValueError("Введите текст поискового запроса.")
        try:
            inputs = self.processor(text=[text], return_tensors="pt", padding="max_length",
                                    truncation=True, max_length=self.text_token_limit).to(self.device)
            with torch.no_grad():
                with torch.amp.autocast('cuda', enabled=(self.device == 'cuda'), dtype=self.dtype):
                    text_features = self.model.get_text_features(**inputs)
            
            # Normalize and convert to numpy
            text_features = text_features.float()
            text_features = text_features / text_features.norm(p=2, dim=-1, keepdim=True)
            vector = text_features.cpu().numpy().flatten()
            if not np.isfinite(vector).all() or not np.any(vector):
                raise ValueError("Модель вернула некорректный вектор текста.")
            return vector
        except Exception as e:
            logger.error(f"Failed to embed text: {e}")
            raise RuntimeError("Не удалось обработать текст поискового запроса.") from e

    def get_image_embedding(self, image_path: str) -> np.ndarray:
        """Генерирует вектор для одного изображения."""
        try:
            img = Image.open(image_path).convert("RGB")
            # Передаем на устройство в float32, autocast сам переведет в нужный формат при инференсе
            inputs = self.processor(images=img, return_tensors="pt").to(self.device)
            
            with torch.no_grad():
                with torch.amp.autocast('cuda', enabled=(self.device == 'cuda'), dtype=self.dtype):
                    image_features = self.model.get_image_features(**inputs)
            
            # Normalize and convert to numpy
            image_features = image_features / image_features.norm(p=2, dim=-1, keepdim=True)
            return image_features.cpu().float().numpy().flatten()
        except Exception as e:
            logger.error(f"Failed to embed image {image_path}: {e}")
            raise RuntimeError("Не удалось обработать изображение. Проверьте доступность файла.") from e

    def get_image_embeddings_batch(self, image_paths: list[str]) -> np.ndarray:
        """Генерирует векторы для списка изображений (батчинг с параллельной загрузкой)."""
        def load_image(path):
            try:
                # Открываем и конвертируем сразу, чтобы не грузить GIL в основном цикле
                return Image.open(path).convert("RGB"), path
            except Exception as e:
                logger.warning(f"Failed to load image {path}: {e}")
                return None, path

        # Параллельная загрузка изображений с диска
        results = list(self.executor.map(load_image, image_paths))
        
        valid_images = []
        valid_indices = []
        
        for i, (img, path) in enumerate(results):
            if img is not None:
                valid_images.append(img)
                valid_indices.append(i)

        if not valid_images:
            return np.zeros((len(image_paths), config.VECTOR_DIMENSION), dtype=np.float32)

        try:
            # Препроцессинг
            # Передаем на устройство в float32
            inputs = self.processor(images=valid_images, return_tensors="pt").to(self.device)
            
            with torch.no_grad():
                with torch.amp.autocast('cuda', enabled=(self.device == 'cuda'), dtype=self.dtype):
                    image_features = self.model.get_image_features(**inputs)
            
            # Нормализация
            image_features = image_features / image_features.norm(p=2, dim=-1, keepdim=True)
            batch_vectors = image_features.cpu().float().numpy()
            
            # Сборка финального результата
            result = np.zeros((len(image_paths), config.VECTOR_DIMENSION), dtype=np.float32)
            for j, valid_idx in enumerate(valid_indices):
                result[valid_idx] = batch_vectors[j]
                
            return result
            
        except Exception as e:
            logger.error(f"Batch embedding failed: {e}")
            return np.zeros((len(image_paths), config.VECTOR_DIMENSION), dtype=np.float32)

    def extract_tags(self, image_path: str, vocabulary: dict = None) -> list[str]:
        """
        Zero-Shot Classification: извлекает теги из картинки на основе базового словаря.
        Возвращает список лучших тегов (по 1-2 из каждой категории).
        """
        if vocabulary is None:
            vocabulary = getattr(config, 'TAG_VOCABULARY', {})
            
        if not vocabulary:
            return []
            
        img_emb = self.get_image_embedding(image_path)
        if img_emb is None or np.all(img_emb == 0):
            return []
            
        # Инициализируем кэш векторов слов, если его нет
        if not hasattr(self, '_vocab_embeddings'):
            self._vocab_embeddings = {}
            
        extracted_tags = []
        
        for category, tags in vocabulary.items():
            best_tag = None
            best_sim = -1.0
            
            for tag in tags:
                if tag not in self._vocab_embeddings:
                    self._vocab_embeddings[tag] = self.get_text_embedding(tag)
                txt_emb = self._vocab_embeddings[tag]
                
                # Cosine similarity между нормализованными векторами
                sim = float(np.dot(img_emb, txt_emb))
                
                if sim > best_sim:
                    best_sim = sim
                    best_tag = tag
                    
            # Берем лучший тег из категории. Порог 0.035
            if best_tag and best_sim > 0.035:
                extracted_tags.append(best_tag)
                logger.info(f"Extracted {category} tag: {best_tag} (sim: {best_sim:.3f})")
                
        return extracted_tags
