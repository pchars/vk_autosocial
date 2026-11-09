import io
from pathlib import Path
from typing import Iterable, List, Optional, Union

from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

WALL_MAX_SIDE = 2560
STORY_SIZE = (1080, 1920)
DUPLICATE_DISTANCE = 6
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}

ImageSource = Union[Path, bytes]


def _open(source: ImageSource) -> Image.Image:
    image = Image.open(io.BytesIO(source) if isinstance(source, bytes) else source)
    image.load()
    image = ImageOps.exif_transpose(image)
    return image.convert("RGB")


def image_hash(source: ImageSource) -> Optional[int]:
    """64-bit difference hash; None for files Pillow cannot decode."""
    try:
        with _open(source) as image:
            pixels = list(image.convert("L").resize((9, 8), Image.Resampling.LANCZOS).get_flattened_data())
    except (OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError):
        return None
    value = 0
    for row in range(8):
        for col in range(8):
            value = (value << 1) | (pixels[row * 9 + col] > pixels[row * 9 + col + 1])
    return value


def is_duplicate(candidate: int, known: Iterable[int], distance: int = DUPLICATE_DISTANCE) -> bool:
    return any((candidate ^ other).bit_count() <= distance for other in known)


def wall_jpeg(path: Path, max_side: int = WALL_MAX_SIDE) -> bytes:
    with _open(path) as image:
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        return _jpeg(image)


def story_jpeg(path: Path) -> bytes:
    """Fit the picture into 1080x1920 over a blurred copy of itself."""
    with _open(path) as image:
        background = ImageOps.fit(image, STORY_SIZE, Image.Resampling.LANCZOS)
        background = background.filter(ImageFilter.GaussianBlur(40))
        foreground = ImageOps.contain(image, STORY_SIZE, Image.Resampling.LANCZOS)
        offset = ((STORY_SIZE[0] - foreground.width) // 2, (STORY_SIZE[1] - foreground.height) // 2)
        background.paste(foreground, offset)
        return _jpeg(background)


def list_images(folder: Path) -> List[Path]:
    if not folder.exists():
        return []
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES]
    return sorted(files, key=lambda p: (p.stat().st_mtime, p.name))


def _jpeg(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()
