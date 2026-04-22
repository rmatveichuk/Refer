import logging
import torch
import numpy as np
from PIL import Image
from transformers import AutoProcessor, AutoModel
import argostranslate.package
import argostranslate.translate
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
        self.model = AutoModel.from_pretrained(
            config.SIGLIP_MODEL, 
            torch_dtype=self.dtype, 
            low_cpu_mem_usage=False
        ).to(self.device).eval()
        
        self.processor = AutoProcessor.from_pretrained(config.SIGLIP_MODEL, use_fast=True)
        
        # Для параллельной загрузки картинок
        self.executor = ThreadPoolExecutor(max_workers=8)
        
        # Initialize Translation (RU -> EN)
        self._init_translator()

    def _init_translator(self):
        """Автоматическая загрузка пакетов перевода при первом запуске."""
        from_code = "ru"
        to_code = "en"
        
        try:
            # Проверяем, установлен ли пакет
            installed_languages = argostranslate.translate.get_installed_languages()
            from_lang = list(filter(lambda x: x.code == from_code, installed_languages))
            to_lang = list(filter(lambda x: x.code == to_code, installed_languages))
            
            if not from_lang or not to_lang or not from_lang[0].get_translation(to_lang[0]):
                logger.info("Downloading translation packages (RU -> EN)...")
                argostranslate.package.update_package_index()
                available_packages = argostranslate.package.get_available_packages()
                package_to_install = next(
                    filter(
                        lambda x: x.from_code == from_code and x.to_code == to_code,
                        available_packages
                    )
                )
                argostranslate.package.install_from_path(package_to_install.download())
                logger.info("Translation packages installed successfully.")
            
            self.translator = argostranslate.translate
        except Exception as e:
            logger.error(f"Failed to initialize translator: {e}")
            self.translator = None

    def translate_ru_to_en(self, text: str) -> str:
        """Переводит русский текст на английский для лучшего поиска."""
        if not self.translator or not any(c in text for c in "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"):
            return text
        
        try:
            translated = self.translator.translate(text, "ru", "en")
            logger.info(f"Translated: '{text}' -> '{translated}'")
            return translated
        except Exception as e:
            logger.error(f"Translation failed: {e}")
            return text

    def get_text_embedding(self, text: str) -> np.ndarray:
        """Генерирует вектор для текста (с автопереводом)."""
        english_text = self.translate_ru_to_en(text)
        try:
            inputs = self.processor(text=[english_text], return_tensors="pt", padding="max_length").to(self.device)
            with torch.no_grad():
                with torch.amp.autocast('cuda', dtype=self.dtype):
                    text_features = self.model.get_text_features(**inputs)
            
            # Normalize and convert to numpy
            text_features = text_features / text_features.norm(p=2, dim=-1, keepdim=True)
            return text_features.cpu().float().numpy().flatten()
        except Exception as e:
            logger.error(f"Failed to embed text: {e}")
            return np.zeros(config.VECTOR_DIMENSION)

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
            return np.zeros(config.VECTOR_DIMENSION)

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
