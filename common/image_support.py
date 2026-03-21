from __future__ import annotations

import io
import uuid
from pathlib import Path

from PIL import Image, ImageOps

STANDARD_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
HEIF_EXTENSIONS = {".heic", ".heif"}
SUPPORTED_UPLOAD_EXTENSIONS = STANDARD_IMAGE_EXTENSIONS | HEIF_EXTENSIONS
STANDARD_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}
HEIF_MIME_TYPES = {
    "image/heic",
    "image/heif",
    "image/heic-sequence",
    "image/heif-sequence",
}

_HEIF_REGISTERED = False
_HEIF_AVAILABLE = False


def register_heif_support() -> bool:
    global _HEIF_REGISTERED, _HEIF_AVAILABLE
    if _HEIF_REGISTERED:
        return _HEIF_AVAILABLE

    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
        _HEIF_AVAILABLE = True
    except Exception:
        _HEIF_AVAILABLE = False

    _HEIF_REGISTERED = True
    return _HEIF_AVAILABLE


register_heif_support()


def is_heif_suffix(suffix: str) -> bool:
    return suffix.lower() in HEIF_EXTENSIONS


def is_heif_content_type(content_type: str) -> bool:
    return content_type.strip().lower() in HEIF_MIME_TYPES


def is_supported_upload(filename: str, content_type: str = "") -> bool:
    suffix = Path(filename).suffix.lower()
    normalized_type = content_type.strip().lower()
    return (
        suffix in SUPPORTED_UPLOAD_EXTENSIONS
        or normalized_type in STANDARD_IMAGE_MIME_TYPES
        or normalized_type in HEIF_MIME_TYPES
    )


def normalize_upload_to_path(upload_dir: Path, filename: str, body: bytes, content_type: str = "") -> Path:
    upload_dir.mkdir(parents=True, exist_ok=True)
    original = Path(filename or "upload.bin")
    suffix = original.suffix.lower()
    normalized_type = content_type.strip().lower()

    if is_heif_suffix(suffix) or is_heif_content_type(normalized_type):
        return _convert_bytes_to_jpeg(upload_dir, body)

    if suffix in STANDARD_IMAGE_EXTENSIONS:
        target = upload_dir / f"{uuid.uuid4().hex}{suffix}"
        target.write_bytes(body)
        return target

    if normalized_type == "image/jpeg":
        target = upload_dir / f"{uuid.uuid4().hex}.jpg"
        target.write_bytes(body)
        return target
    if normalized_type == "image/png":
        target = upload_dir / f"{uuid.uuid4().hex}.png"
        target.write_bytes(body)
        return target
    if normalized_type == "image/webp":
        target = upload_dir / f"{uuid.uuid4().hex}.webp"
        target.write_bytes(body)
        return target

    raise ValueError(f"Unsupported image format: {filename or content_type or 'unknown'}")


def prepare_local_image_path(path: Path, output_dir: Path) -> Path:
    if path.suffix.lower() not in HEIF_EXTENSIONS:
        return path
    return _convert_path_to_jpeg(path, output_dir)


def _convert_bytes_to_jpeg(output_dir: Path, body: bytes) -> Path:
    if not register_heif_support():
        raise ValueError("HEIC/HEIF support is not available. Please install pillow-heif and rebuild the image.")

    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{uuid.uuid4().hex}.jpg"
    with Image.open(io.BytesIO(body)) as image:
        normalized = _normalize_image_for_jpeg(image)
        normalized.save(target, format="JPEG", quality=92, optimize=True)
    return target


def _convert_path_to_jpeg(path: Path, output_dir: Path) -> Path:
    if not register_heif_support():
        raise ValueError("HEIC/HEIF support is not available. Please install pillow-heif and rebuild the image.")

    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{path.stem}-{uuid.uuid4().hex[:8]}.jpg"
    with Image.open(path) as image:
        normalized = _normalize_image_for_jpeg(image)
        normalized.save(target, format="JPEG", quality=92, optimize=True)
    return target


def _normalize_image_for_jpeg(image: Image.Image) -> Image.Image:
    normalized = ImageOps.exif_transpose(image)
    if normalized.mode in {"RGBA", "LA"}:
        rgba_image = normalized.convert("RGBA")
        background = Image.new("RGBA", rgba_image.size, (255, 255, 255, 255))
        return Image.alpha_composite(background, rgba_image).convert("RGB")
    if normalized.mode == "P":
        return normalized.convert("RGB")
    if normalized.mode != "RGB":
        return normalized.convert("RGB")
    return normalized
