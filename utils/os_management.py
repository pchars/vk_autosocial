import os
from pathlib import Path
from utils import get_logger

logger = get_logger(__name__)

class OSManagement:
    def __init__(self, folder_path: Path):
        self.folder_path = folder_path

    def is_folder_exists(self):
        """Create folder with proper error handling"""
        try:
            os.makedirs(self.folder_path, exist_ok=True)
            logger.info(f"Folder ensured: {self.folder_path}")
            return True
        except OSError as e:
            logger.error(f"Error creating folder {self.folder_path}: {e}")
            return False