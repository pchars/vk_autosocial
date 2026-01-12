import os
from pathlib import Path
from typing import List, Iterator, Union
from utils import get_logger

logger = get_logger(__name__)


class OSManagement:
    def __init__(self, base_dir: Path = Path(".")):
        self.base_dir = base_dir

    def ensure_folder_exists(self, folder_path: Path) -> bool:
        """Create folder if not exist"""
        try:
            folder_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"Folder ensured: {folder_path}")
            return True
        except OSError as e:
            logger.error(f"Error creating folder {folder_path}: {e}")
            return False

    def is_folder_exists(self, folder_path: Path) -> bool:
        """Check if folder exist"""
        exists = folder_path.exists() and folder_path.is_dir()
        logger.debug(f"Folder {folder_path} exists: {exists}")
        return exists

    def ensure_multiple_folders(self, folders: List[Path]) -> bool:
        """Create multiple folders"""
        all_success = True
        for folder in folders:
            success = self.ensure_folder_exists(folder)
            if not success:
                all_success = False
        return all_success

    def cleanup_folder(self, folder_path: Path) -> bool:
        """Folder cleanup"""
        try:
            if not self.is_folder_exists(folder_path):
                return True

            for file in folder_path.iterdir():
                if file.is_file():
                    file.unlink()
            logger.info(f"Cleaned up folder: {folder_path}")
            return True
        except Exception as e:
            logger.error(f"Error cleaning folder {folder_path}: {e}")
            return False

    def iterate_files(
            self,
            folder_path: Path,
            pattern: str = "*",
            recursive: bool = False
    ) -> Iterator[Path]:
        """
        Generator for the files iter in folder

        Args:
            folder_path: Path to folder
            pattern: Search pattern (e.g., "*.jpg")
            recursive: Recursive search in sub-folders

        Yields:
            Path object files
        """
        if not self.is_folder_exists(folder_path):
            logger.warning(f"Folder does not exist: {folder_path}")
            return

        try:
            if recursive:
                # Recursive search
                for file_path in folder_path.rglob(pattern):
                    if file_path.is_file():
                        yield file_path
            else:
                # Search only in the current directory
                for file_path in folder_path.glob(pattern):
                    if file_path.is_file():
                        yield file_path

        except Exception as e:
            logger.error(f"Error iterating files in {folder_path}: {e}")

    def get_files_list(
            self,
            folder_path: Path,
            pattern: str = "*",
            recursive: bool = False,
            sort_by: str = "name",
            reverse: bool = False
    ) -> List[Path]:
        """
        Returning all files from specified folder

        Args:
            folder_path: Path to folder
            pattern: Pattern for searching
            recursive: Recursive searching
            sort_by: Sorting ("name", "size", "modified")
            reverse: Reverse sorting

        Returns:
            List of Path objects
        """
        files = list(self.iterate_files(folder_path, pattern, recursive))

        # Filtering invalid path
        valid_files = []
        for file_path in files:
            if (file_path and
                    str(file_path).strip() and
                    file_path != Path('.') and
                    file_path != Path('')):
                valid_files.append(file_path)

        files = valid_files

        # Sort
        if sort_by == "name":
            files.sort(key=lambda x: x.name, reverse=reverse)
        elif sort_by == "size":
            files.sort(key=lambda x: x.stat().st_size, reverse=reverse)
        elif sort_by == "modified":
            files.sort(key=lambda x: x.stat().st_mtime, reverse=reverse)

        return files

    def delete_file(self, file_path: Union[Path, str]) -> bool:
        """
        Removing the file

        Args:
            file_path: Path to file

        Returns:
            True if success, False if not
        """
        try:
            file_path = Path(file_path)

            if not file_path or str(file_path) == '.' or str(file_path) == '':
                logger.warning("Attempted to delete empty file path")
                return False

            if not file_path.exists():
                logger.warning(f"File does not exist: {file_path}")
                return False

            if not file_path.is_file():
                logger.error(f"Path is not a file: {file_path}")
                return False

            os.remove(file_path)
            logger.info(f"Deleted file: {file_path}")
            return True

        except PermissionError:
            logger.error(f"Permission denied to delete: {file_path}")
            return False
        except Exception as e:
            logger.error(f"Error deleting file {file_path}: {e}")
            return False