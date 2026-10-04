"""Photometric and colorimetric feature extraction for Refer using PIL + NumPy + sklearn."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Any

import numpy as np
from PIL import Image
from sklearn.cluster import MiniBatchKMeans

logger = logging.getLogger(__name__)

# Standard sRGB to XYZ (D65) matrix
SRGB_TO_XYZ = np.array([
    [0.4124564, 0.3575761, 0.1804375],
    [0.2126729, 0.7151522, 0.0721750],
    [0.0193339, 0.1191920, 0.9503041]
], dtype=np.float32)

# D65 Reference white point
D65 = np.array([0.95047, 1.00000, 1.08883], dtype=np.float32)


def rgb_to_cielab(rgb_u8: np.ndarray) -> np.ndarray:
    """
    Vectorized standard sRGB [0..255] to CIE L*a*b* conversion (D65 illuminant).
    L* in [0, 100], a* in [-128, 127], b* in [-128, 127].
    """
    rgb = rgb_u8.astype(np.float32) / 255.0

    # Linearize sRGB (gamma expansion)
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)

    # Convert to XYZ
    xyz = np.dot(linear, SRGB_TO_XYZ.T) / D65

    # Non-linear transformation function f(t)
    delta = 6.0 / 29.0
    f_xyz = np.where(xyz > delta ** 3, np.cbrt(xyz), (xyz / (3.0 * delta ** 2)) + (4.0 / 29.0))

    # Calculate L*, a*, b*
    L = 116.0 * f_xyz[..., 1] - 16.0
    a = 500.0 * (f_xyz[..., 0] - f_xyz[..., 1])
    b = 200.0 * (f_xyz[..., 1] - f_xyz[..., 2])

    return np.stack([L, a, b], axis=-1)


def cielab_to_rgb(lab: np.ndarray) -> np.ndarray:
    """
    Converts a single or array of CIE L*a*b* points to sRGB [0..255].
    """
    L = lab[..., 0]
    a = lab[..., 1]
    b = lab[..., 2]

    fy = (L + 16.0) / 116.0
    fx = fy + (a / 500.0)
    fz = fy - (b / 200.0)

    delta = 6.0 / 29.0
    def f_inv(t):
        return np.where(t > delta, t ** 3, 3.0 * (delta ** 2) * (t - 4.0 / 29.0))

    xr = f_inv(fx) * D65[0]
    yr = f_inv(fy) * D65[1]
    zr = f_inv(fz) * D65[2]

    xyz = np.stack([xr, yr, zr], axis=-1)

    # Inverse XYZ to sRGB matrix
    XYZ_TO_SRGB = np.array([
        [ 3.2404542, -1.5371385, -0.4985314],
        [-0.9692660,  1.8760108,  0.0415560],
        [ 0.0556434, -0.2040259,  1.0572252]
    ], dtype=np.float32)

    linear = np.dot(xyz, XYZ_TO_SRGB.T)
    linear = np.clip(linear, 0.0, 1.0)

    # Gamma compression
    srgb = np.where(linear <= 0.0031308, 12.92 * linear, 1.055 * (linear ** (1.0 / 2.4)) - 0.055)
    return np.clip(np.round(srgb * 255.0), 0, 255).astype(np.uint8)


def extract_photometry(
    image_input: Path | str | Image.Image | np.ndarray,
    target_size: int = 192,
    n_colors: int = 5,
    sample_pixels: int = 4096
) -> Dict[str, Any]:
    """
    Extracts reproducible photometric features and dominant palette from an image:
    - warmth_palette: 0.0 (icy cool) to 1.0 (warm yellow/orange)
    - global_contrast: std(L* / 100.0) in standard CIELAB
    - lstar_mean: mean lightness (0.0 to 100.0)
    - palette: Top-N colors with HEX, standard CIELAB coordinates, and pixel weights
    """
    try:
        if isinstance(image_input, (str, Path)):
            p = Path(image_input)
            if not p.is_file():
                return {
                    "status": "missing_file",
                    "warmth_palette": None,
                    "global_contrast": None,
                    "lstar_mean": None,
                    "palette": None
                }
            with Image.open(p) as pil_img:
                from PIL import ImageOps
                pil_img = ImageOps.exif_transpose(pil_img)
                pil_img = pil_img.convert("RGB")
                pil_img.thumbnail((target_size, target_size), Image.Resampling.BILINEAR)
                rgb_arr = np.array(pil_img, dtype=np.uint8)
        elif isinstance(image_input, Image.Image):
            pil_img = image_input.copy().convert("RGB")
            pil_img.thumbnail((target_size, target_size), Image.Resampling.BILINEAR)
            rgb_arr = np.array(pil_img, dtype=np.uint8)
        elif isinstance(image_input, np.ndarray):
            rgb_arr = image_input
        else:
            raise TypeError(f"Unsupported image input type: {type(image_input)}")

        lab = rgb_to_cielab(rgb_arr)
        L = lab[..., 0]
        a = lab[..., 1]
        b = lab[..., 2]

        # 1. Warmth Score: projection onto yellow-orange axis (0.5 * a* + 0.866 * b*)
        # with tanh compression over a scale factor of 20.0
        projection = 0.5 * a + 0.8660254 * b
        warmth_palette = float(0.5 + 0.5 * np.mean(np.tanh(projection / 20.0)))

        # 2. Global contrast & lightness mean
        lstar_mean = float(np.mean(L))
        global_contrast = float(np.std(L / 100.0))

        # 3. Dominant Color Palette (MiniBatchKMeans on subsampled pixels in CIE LAB)
        flat_lab = lab.reshape(-1, 3)

        if flat_lab.shape[0] > sample_pixels:
            rng = np.random.default_rng(42)
            indices = rng.choice(flat_lab.shape[0], size=sample_pixels, replace=False)
            train_pixels = flat_lab[indices]
        else:
            train_pixels = flat_lab

        k = min(n_colors, max(1, flat_lab.shape[0]))
        kmeans = MiniBatchKMeans(
            n_clusters=k,
            batch_size=1024,
            random_state=42,
            n_init=3,
            max_iter=30
        )
        kmeans.fit(train_pixels)

        # Predict cluster for all pixels to get true surface distribution
        all_labels = kmeans.predict(flat_lab)
        counts = np.bincount(all_labels, minlength=k)
        total_pixels = float(len(all_labels))
        weights = counts / (total_pixels if total_pixels > 0 else 1.0)

        # Sort clusters by weight descending
        sort_order = np.argsort(-weights)
        centers = kmeans.cluster_centers_

        palette = []
        for idx in sort_order:
            cL, ca, cb = centers[idx]
            weight = float(weights[idx])

            # Convert to sRGB hex
            rgb_pt = cielab_to_rgb(np.array([[cL, ca, cb]], dtype=np.float32))[0]
            hex_color = f"#{rgb_pt[0]:02x}{rgb_pt[1]:02x}{rgb_pt[2]:02x}"

            palette.append({
                "hex": hex_color,
                "lab": [round(float(cL), 1), round(float(ca), 1), round(float(cb), 1)],
                "weight": round(weight, 3)
            })

        return {
            "status": "completed",
            "warmth_palette": round(warmth_palette, 3),
            "global_contrast": round(global_contrast, 3),
            "lstar_mean": round(lstar_mean, 1),
            "palette": palette
        }

    except Exception as e:
        logger.warning(f"Error computing photometry for {image_path}: {e}")
        return {
            "status": "error",
            "warmth_palette": None,
            "global_contrast": None,
            "lstar_mean": None,
            "palette": None
        }
