import imagehash
from PIL import Image
from typing import Tuple

def compute_phash(image_path: str) -> str:
    """
    Вычисляет перцептивный хэш (pHash) изображения.
    Это необходимо для дедупликации (сравнения скачанных ассетов).
    Возвращает строку-хэш.
    """
    try:
        img = Image.open(image_path)
        return str(imagehash.phash(img))
    except Exception as e:
        raise ValueError(f"Failed to compute pHash for {image_path}: {e}")

def optimize_thumbnail(source_path: str, output_path: str, max_size: int = 1024) -> Tuple[int, int]:
    """
    Масштабирует оригинальное изображение при сохранении соотношения сторон (не больше max_size).
    Сохраняет результат в высокоэффективный формат WebP.
    Возвращает (ширину, высоту) получившегося превью.
    """
    try:
        with Image.open(source_path) as img:
            # Если исходник имеет палитру, переводим в RGBA/RGB (WebP лоялен к ним)
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA")
            
            # thumbnail сохраняет пропорции
            img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            
            # Качество 85 в WebP дает отличную картинку при минимальном весе
            img.save(output_path, 'WEBP', quality=85)
            return img.size
    except Exception as e:
        raise ValueError(f"Failed to optimize image {source_path}: {e}")
