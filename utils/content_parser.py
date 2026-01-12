import os
from typing import List

from utils import get_logger

logger = get_logger(__name__)


class FileContentUtils:
    @staticmethod
    def delete_duplicates_from_text_db(db_text_file: str = "db_text.txt") -> List[str]:
        # Check the file path
        if not db_text_file or db_text_file.strip() == "":
            logger.warning("Text database file path is empty, returning empty list")
            return []

        # Check if file exists
        if not os.path.exists(db_text_file):
            logger.error(f"Text database file does not exist: {db_text_file}")
            return []

        real_lines = []
        try:
            with open(db_text_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                for line in lines:
                    if "\"" in line:
                        line = line.replace('"', '')
                    if "    " in line:
                        line = line.replace("    ", "")
                    real_lines.append(line.strip())  # Добавляем strip() для удаления лишних пробелов

                real_lines = list(set(real_lines))
            return real_lines
        except Exception as e:
            logger.error(f"Error reading text database file {db_text_file}: {e}")
            return []
