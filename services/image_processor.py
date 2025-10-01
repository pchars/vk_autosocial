import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, Tuple

import imagehash
from PIL import Image, UnidentifiedImageError
from utils import get_logger, OSManagement

logger = get_logger(__name__)


class ImageProcessor:
    def __init__(self, folder_path: Path, similarity_threshold: int = 5):
        self.similarity_threshold = similarity_threshold
        self.folder_path = folder_path

    def check_for_duplicates(self, folder: Path) -> None:
        """
        Remove broken images and duplicates using perceptual hashing
        """
        folder_manager = OSManagement()
        if folder_manager.is_folder_exists(folder_path=folder):
            logger.info(f"Folder {self.folder_path} is ready to use")

        broken_files = []
        duplicate_files = []

        def _process_file(file_path: Path) -> Tuple[Path, Optional[imagehash.ImageHash]]:
            """Process single file and return tuple with result"""
            try:
                with Image.open(file_path) as img:
                    if img.mode in ('RGBA', 'LA'):
                        img = img.convert('RGB')
                    return file_path, imagehash.phash(img)
            except (OSError, IOError, UnidentifiedImageError, ValueError) as ex:
                logger.warning(f"Invalid image: {file_path.name} - {str(ex)}")
                return file_path, None
            except Exception as ex:
                logger.error(f"Error processing {file_path.name}: {str(ex)}")
                return file_path, None

        try:
            # Process files in parallel
            with ThreadPoolExecutor(max_workers=min(32, os.cpu_count() * 2 + 4)) as executor:
                results = list(executor.map(_process_file, folder.iterdir()))

            # Remove broken files
            for file, img_hash in filter(lambda x: x[1] is None, results):
                try:
                    file.unlink()
                    logger.warning(f"Deleted invalid image: {file.name}")
                    broken_files.append(file)
                except Exception as e:
                    logger.error(f"Failed to delete {file.name}: {str(e)}")

            # Duplicate detection
            hash_dict = {}
            for file, img_hash in filter(lambda x: x[1] is not None, results):
                is_duplicate = False
                for existing_hash in hash_dict:
                    if img_hash - existing_hash <= self.similarity_threshold:
                        duplicate_files.append(file)
                        is_duplicate = True
                        break
                if not is_duplicate:
                    hash_dict[img_hash] = file

            # Remove duplicates
            for file in duplicate_files:
                try:
                    file.unlink()
                    logger.warning(f"Deleted duplicate: {file.name}")
                except Exception as e:
                    logger.error(f"Failed to delete duplicate {file.name}: {str(e)}")

            logger.info(
                f"Cleanup completed. Removed {len(broken_files)} invalid and {len(duplicate_files)} duplicate images.")

        except Exception as e:
            logger.error(f"Fatal error during duplicate check: {str(e)}")

    @staticmethod
    def optimize_image(image_path: Path, max_size: tuple[int, int] = (1080, 1080)) -> None:
        """Resize and optimize image for VK"""
        try:
            with Image.open(image_path) as img:
                img.thumbnail(max_size, Image.Resampling.LANCZOS)
                if img.mode in ('RGBA', 'LA'):
                    img = img.convert('RGB')
                img.save(image_path, 'JPEG', quality=85, optimize=True)
        except Exception as e:
            logger.error(f"Failed to optimize {image_path.name}: {e}")
